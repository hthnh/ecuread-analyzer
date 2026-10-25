"""Separate proposal persistence and explicit human acceptance."""
from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING, Any
import math
from uuid import uuid4

from app.context_annotation.auto_labeler import RuleConfig, suggest_context
from app.context_annotation.models import (
    ANNOTATION_SCHEMA_VERSION_V2, TAXONOMY_VERSION_V2, ContextAnnotationDocument,
    ContextEvent, ContextSegment, HumanConfirmation, OperatingLabels, Suggestion,
)
from app.context_annotation.storage import utc_now_iso
from app.context_annotation.validation import AnnotationValidationError, find_overlaps

if TYPE_CHECKING:
    from app.context_annotation.service import ContextAnnotationService


def review_measurement(seconds: float | None) -> dict:
    if seconds is not None and (not math.isfinite(seconds) or not 0 <= seconds <= 3600):
        raise AnnotationValidationError("review time must be finite and between 0 and 3600 seconds")
    return {"action_id": str(uuid4()), "review_time_s": seconds,
            "time_basis": "client_visible_dialog_elapsed" if seconds is not None else "not_measured"}


class SuggestionReview:
    def __init__(self, service: ContextAnnotationService) -> None:
        self.service = service
        self.lock = RLock()

    def _current(self, source_id: str):
        loaded = self.service.load_session(source_id)
        if loaded.metadata.status != "ok":
            raise AnnotationValidationError(f"cannot review source: {loaded.metadata.status}")
        proposals = self.service.storage.read_suggestions(source_id)
        if proposals is None:
            raise AnnotationValidationError("no suggestions generated for this session")
        if proposals.source.sha256 != loaded.metadata.source.sha256:
            raise AnnotationValidationError("suggestion source SHA mismatch; rerun after resolving source changes")
        document = self.service.annotation_for(loaded)
        if document.source.sha256 != loaded.metadata.source.sha256:
            raise AnnotationValidationError("SOURCE CHANGED")
        raw_sha = proposals.signal_provenance.get("raw_source_sha256")
        if raw_sha and raw_sha != loaded.metadata.research_signal_provenance.get("raw_source_sha256"):
            raise AnnotationValidationError("original RAW evidence changed or is unavailable; regenerate suggestions")
        return loaded, proposals, document

    def read(self, source_id: str, *, loaded=None) -> dict | None:
        proposals = self.service.storage.read_suggestions(source_id)
        if proposals is None:
            return None
        loaded = loaded or self.service.load_session(source_id)
        document = self.service.annotation_for(loaded)
        payload = proposals.model_dump(mode="json")
        payload["source_changed"] = proposals.source.sha256 != loaded.metadata.source.sha256
        payload["raw_evidence_changed"] = bool(proposals.signal_provenance.get("raw_source_sha256") and
            proposals.signal_provenance.get("raw_source_sha256") != loaded.metadata.research_signal_provenance.get("raw_source_sha256"))
        accepted = {item.suggestion_id: item for item in document.segments + document.events if item.suggestion_id}
        for collection in ("segments", "events"):
            for item in payload[collection]:
                # The accepted sidecar is authoritative if a process stops between the two writes.
                if item["id"] in accepted:
                    item["review_status"] = "accepted"
                    confirmation = accepted[item["id"]].human_confirmation
                    item["human_confirmation"] = confirmation.model_dump(mode="json") if confirmation else None
                    endorsement = accepted[item["id"]].bulk_endorsement
                    item["bulk_endorsement"] = endorsement.model_dump(mode="json") if endorsement else None
                item["conflicts_with_reviewed"] = collection == "segments" and any(
                    item["start_s"] < segment.end_s and item["end_s"] > segment.start_s
                    and segment.suggestion_id != item["id"] for segment in document.segments)
        payload["pending_count"] = sum(item["review_status"] == "pending" for item in payload["segments"] + payload["events"])
        payload["uncertain_count"] = sum(item["review_status"] == "uncertain" for item in payload["segments"] + payload["events"])
        return payload

    def generate(self, source_id: str, config: RuleConfig | None = None) -> dict:
        with self.lock:
            loaded = self.service.load_session(source_id)
            fresh = suggest_context(loaded, config, speeds=[s.get("vehicle_speed_kmh") for s in loaded.samples],
                                    speed_provenance=loaded.metadata.research_signal_provenance)
            old = self.service.storage.read_suggestions(source_id)
            if old is not None and old.source.sha256 != fresh.source.sha256:
                raise AnnotationValidationError("existing suggestions refer to changed source SHA; resolve provenance before rerunning")
            if old:
                for key in ("segments", "events"):
                    existing = {item.id: item for item in getattr(old, key)}
                    new_items = [existing.get(item.id, item) for item in getattr(fresh, key)]
                    new_ids = {item.id for item in new_items}
                    new_items.extend(item for item in existing.values() if item.id not in new_ids and item.review_status != "pending")
                    setattr(fresh, key, new_items)
            self.service.storage.write_suggestions(source_id, fresh)
            return self.read(source_id, loaded=loaded)

    def edit(self, source_id: str, suggestion_id: str, changes: dict[str, Any], reviewer: str,
             *, review_time_s: float | None = None) -> dict:
        with self.lock:
            measurement = review_measurement(review_time_s)
            loaded, proposals, _ = self._current(source_id)
            item = next((s for s in proposals.segments + proposals.events if s.id == suggestion_id), None)
            if item is None or item.review_status not in {"pending", "uncertain"}:
                raise AnnotationValidationError("only pending or uncertain suggestions can be corrected")
            if not reviewer.strip():
                raise AnnotationValidationError("reviewer is required")
            permitted = {"start_s", "end_s", "operating_context", "thermal_phase", "event", "notes"}
            if changes.keys() - permitted:
                raise AnnotationValidationError("suggestion provenance is read-only")
            changes = {key: value for key, value in changes.items() if getattr(item, key) != value}
            if not changes:
                return self.read(source_id, loaded=loaded)
            corrected = Suggestion.model_validate({**item.model_dump(mode="json"), **changes})
            if corrected.end_s > loaded.metadata.duration_s:
                raise AnnotationValidationError("suggestion ends outside session bounds")
            if (corrected.event is None) != (item.event is None):
                raise AnnotationValidationError("cannot convert an event into an operating segment")
            others = [s for s in proposals.segments if s.id != item.id and s.review_status in {"pending", "uncertain"}]
            if corrected.event is None and any(corrected.start_s < s.end_s and corrected.end_s > s.start_s for s in others):
                raise AnnotationValidationError("corrected suggestion overlaps another pending operating segment")
            corrected.review_history.append({"action": "corrected", "reviewer": reviewer.strip(), "at": utc_now_iso(), "changes": changes, **measurement})
            key = "events" if item.event else "segments"
            setattr(proposals, key, [corrected if s.id == item.id else s for s in getattr(proposals, key)])
            self.service.storage.write_suggestions(source_id, proposals)
            return self.read(source_id, loaded=loaded)

    def decide(self, source_id: str, suggestion_ids: list[str], action: str, reviewer: str,
               *, start_s: float | None = None, end_s: float | None = None, notes: str = "",
               review_time_s: float | None = None) -> dict:
        with self.lock:
            measurement = review_measurement(review_time_s)
            loaded, proposals, document = self._current(source_id)
            if action not in {"accept", "reject", "uncertain"} or not reviewer.strip():
                raise AnnotationValidationError("accept/reject/uncertain and a named reviewer are required")
            ids = set(suggestion_ids)
            items = proposals.segments + proposals.events
            if ids - {item.id for item in items}:
                raise AnnotationValidationError("unknown suggestion ID")
            if not ids and (start_s is None or end_s is None or not 0 <= start_s < end_s <= loaded.metadata.duration_s):
                raise AnnotationValidationError("select suggestion IDs or a valid review range")
            selected = [item for item in items if (item.id in ids if ids else start_s <= item.start_s and item.end_s <= end_s)]
            accepted_by_id = {s.suggestion_id for s in document.segments + document.events if s.suggestion_id}
            segments, events = list(document.segments), list(document.events)
            skipped, decided = [], []
            for item in sorted(selected, key=lambda s: (s.start_s, s.end_s, s.id)):
                # Range actions skip deferred suggestions; resolving uncertainty is explicit by ID.
                if (item.review_status not in {"pending", "uncertain"} or item.id in accepted_by_id
                        or item.review_status == "uncertain" and (not ids or action == "uncertain")):
                    skipped.append({"id": item.id, "reason": "already reviewed"})
                    continue
                if item.end_s > loaded.metadata.duration_s:
                    raise AnnotationValidationError("suggestion ends outside session bounds")
                if action == "accept" and item.event is None and any(item.start_s < s.end_s and item.end_s > s.start_s for s in segments):
                    skipped.append({"id": item.id, "reason": "overlaps reviewed annotation"})
                    continue
                now = utc_now_iso()
                original = item.original_proposal
                corrections = {key: value for key, value in item.model_dump(mode="json").items()
                               if key in {"start_s", "end_s", "operating_context", "thermal_phase", "event"} and original.get(key) != value}
                confirmation = HumanConfirmation(reviewer=reviewer.strip(), confirmed_at=now, corrections=corrections)
                if action == "accept":
                    provenance = {"id": "auto_" + item.id, "start_s": item.start_s, "end_s": item.end_s,
                                  "origin": item.origin, "rule_version": item.rule_version, "review_status": "accepted",
                                  "suggestion_id": item.id, "evidence": item.evidence, "evidence_quality": item.evidence_quality,
                                  "reason": item.reason, "original_proposal": original,
                                  "human_confirmation": confirmation, "notes": item.notes}
                    if item.event:
                        events.append(ContextEvent(**provenance, event=item.event))
                    else:
                        segments.append(ContextSegment(**provenance, taxonomy_version=TAXONOMY_VERSION_V2,
                                                       labels=OperatingLabels(operating_context=item.operating_context, thermal_phase=item.thermal_phase)))
                item.review_status = {"accept": "accepted", "reject": "rejected", "uncertain": "uncertain"}[action]
                item.human_confirmation = confirmation if action != "uncertain" else None
                item.review_history.append({"action": action, "reviewer": reviewer.strip(), "at": now, "notes": notes, **measurement})
                decided.append(item.id)
            if action == "accept" and decided:
                if find_overlaps(segments):
                    raise AnnotationValidationError("accepted suggestions overlap")
                payload = {**document.model_dump(mode="json"), "schema_version": ANNOTATION_SCHEMA_VERSION_V2,
                           "segments": [s.model_dump(mode="json") for s in sorted(segments, key=lambda s: s.start_s)],
                           "events": [e.model_dump(mode="json") for e in sorted(events, key=lambda e: e.start_s)]}
                self.service.save_document(source_id, ContextAnnotationDocument.model_validate(payload))
            self.service.storage.write_suggestions(source_id, proposals)
            return {"suggestions": self.read(source_id, loaded=loaded),
                    "annotation": self.service.annotation_for(loaded).model_dump(mode="json"), "decided": decided, "skipped": skipped}
