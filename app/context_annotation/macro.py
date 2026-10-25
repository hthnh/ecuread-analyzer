"""Lossless review grouping, separate from CTX2 labels and accepted annotations."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.context_annotation.auto_labeler import stable_hash
from app.context_annotation.models import BulkEndorsement
from app.context_annotation.review import review_measurement
from app.context_annotation.speed import finite
from app.context_annotation.storage import atomic_write_json, safe_source_id, utc_now_iso
from app.context_annotation.validation import AnnotationValidationError


MACRO_RULE_VERSION = "ctx24-macro-v1"
MACRO_VALUES = ["moving_episode", "moving_steady", "stationary_running", "engine_off", "unknown"]
THERMAL_VALUES = ["warming", "stable", "unknown"]


class MacroEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    axis: Literal["operating", "thermal"]
    label: str
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)
    child_ids: list[str]
    event_ids: list[str]
    evidence: dict
    original_proposal: dict
    review_status: Literal["pending", "accepted", "rejected", "uncertain"] = "pending"
    notes: str = ""
    review_history: list[dict] = Field(default_factory=list)
    bulk_endorsement: BulkEndorsement | None = None

    @model_validator(mode="after")
    def validate_episode(self):
        if self.end_s <= self.start_s:
            raise ValueError("macro end must follow start")
        if not self.child_ids or len(self.child_ids) != len(set(self.child_ids)):
            raise ValueError("macro must reference unique original intervals")
        if self.label not in (MACRO_VALUES if self.axis == "operating" else THERMAL_VALUES):
            raise ValueError("label is not valid for this review axis")
        if self.bulk_endorsement is not None and self.review_status != "accepted":
            raise ValueError("bulk endorsement applies only to accepted episodes")
        if self.review_status == "accepted" and not self.bulk_endorsement and not any(h.get("action") == "accept" and h.get("reviewer") for h in self.review_history):
            raise ValueError("macro acceptance requires an explicit named human action")
        return self


class MacroDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["context-macro-review-v1"] = "context-macro-review-v1"
    rule_version: Literal["ctx24-macro-v1"] = MACRO_RULE_VERSION
    session_id: str
    source_sha256: str
    detailed_content_sha256: str
    generated_at: str
    episodes: list[MacroEpisode]
    retained_reviews: list[MacroEpisode] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [e.id for e in self.episodes + self.retained_reviews]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate macro episode identity")
        return self


def detail_hash(proposals) -> str:
    # Review status/timing do not change measured evidence or grouping inputs.
    keys = ("id", "start_s", "end_s", "operating_context", "thermal_phase", "event", "evidence", "original_proposal")
    return stable_hash({"source": proposals.source.sha256, "rule": proposals.rule_version,
                        "config": proposals.config_sha256, "signal_provenance": proposals.signal_provenance,
                        "timestamp_integrity": proposals.timestamp_integrity,
                        "items": [{k: getattr(s, k) for k in keys} for s in proposals.segments + proposals.events]})


def summary(loaded, start: float, end: float) -> dict:
    times = loaded.elapsed_s
    a, b = bisect_left(times, start), bisect_right(times, end)
    samples = loaded.samples[a:b]
    result = {"sample_count": len(samples), "signals": {}}
    for field in ("vehicle_speed_kmh", "rpm", "tps_voltage", "tps_raw", "ect_c"):
        values = [v for s in samples if (v := finite(s.get(field))) is not None
                  and s.get("frame_valid", True) and s.get("checksum_valid", True)]
        result["signals"][field] = {"available_count": len(values), "min": min(values) if values else None,
                                    "max": max(values) if values else None,
                                    "start": values[0] if values else None, "end": values[-1] if values else None}
    result["boundary_samples"] = []
    for t in (start, end):
        i = min(bisect_left(times, t), len(times) - 1)
        if i and abs(times[i - 1] - t) < abs(times[i] - t):
            i -= 1
        result["boundary_samples"].append({"boundary_s": t, "sample_elapsed_s": times[i],
                                           **{k: loaded.samples[i].get(k) for k in
                                              ("sequence", "timestamp_ms", "vehicle_speed_kmh", "rpm", "tps_voltage", "ect_c")}})
    return result


def generate_macros(proposals, loaded) -> MacroDocument:
    """Union only related contiguous intervals; never shorten/delete an input state."""
    segments = sorted(proposals.segments, key=lambda s: (s.start_s, s.end_s, s.id))
    if any(a.end_s > b.start_s + 1e-9 for a, b in zip(segments, segments[1:])):
        raise AnnotationValidationError("overlapping detailed proposals cannot be grouped")
    hard_cuts = {e.start_s for e in proposals.events if e.event in ("engine_start", "engine_stop")}
    hard_cuts.update(t for g in proposals.timestamp_integrity.get("capture_gaps", []) for t in (g["start_s"], g["end_s"]))
    episodes = []
    for axis in ("operating", "thermal"):
        groups = []
        previous = None
        for s in segments:
            value = s.operating_context if axis == "operating" else s.thermal_phase
            label = "moving_episode" if value.startswith("moving_") and value != "moving_steady" and axis == "operating" else value
            cuts = sorted(t for t in hard_cuts if s.start_s < t < s.end_s) if axis == "operating" else []
            points = [s.start_s, *cuts, s.end_s]
            for start, end in zip(points, points[1:]):
                reversal = axis == "operating" and {previous, value} == {"moving_accelerating", "moving_decelerating"}
                join = (groups and groups[-1]["label"] == label and abs(groups[-1]["end_s"] - start) < 1e-9
                        and not (axis == "operating" and (start in hard_cuts or reversal)))
                if join:
                    groups[-1]["end_s"] = end
                    if s.id not in groups[-1]["child_ids"]:
                        groups[-1]["child_ids"].append(s.id)
                else:
                    groups.append({"axis": axis, "label": label, "start_s": start, "end_s": end, "child_ids": [s.id]})
                previous = value
        for group in groups:
            identity = {"source_sha256": proposals.source.sha256, "rule": MACRO_RULE_VERSION, **group}
            event_ids = [e.id for e in proposals.events if e.start_s <= group["end_s"] and e.end_s >= group["start_s"]]
            episodes.append(MacroEpisode(id="macro-" + stable_hash(identity)[:24], **group, event_ids=event_ids,
                                         evidence=summary(loaded, group["start_s"], group["end_s"]), original_proposal=group.copy()))
    return MacroDocument(session_id=proposals.session_id, source_sha256=proposals.source.sha256,
                         detailed_content_sha256=detail_hash(proposals), generated_at=utc_now_iso(), episodes=episodes)


def progress(items) -> dict:
    counts = Counter(i.review_status for i in items)
    return {"total": len(items), **{s: counts[s] for s in ("pending", "accepted", "rejected", "uncertain")}}


class MacroReview:
    def __init__(self, service):
        self.service = service
        # A common lock prevents regeneration and detailed review racing macro actions.
        self.lock = service.review.lock

    def path(self, source_id: str) -> Path:
        return self.service.storage.suggestions_dir.parent / "macro_suggestions" / f"{safe_source_id(source_id)}.macro.json"

    def document(self, source_id: str) -> MacroDocument | None:
        path = self.path(source_id)
        return MacroDocument.model_validate_json(path.read_text()) if path.exists() else None

    def generate(self, source_id: str, *, loaded=None) -> dict:
        with self.lock:
            loaded = loaded or self.service.load_session(source_id)
            proposals = self.service.storage.read_suggestions(source_id)
            if proposals is None or proposals.source.sha256 != loaded.metadata.source.sha256 or loaded.metadata.status != "ok":
                raise AnnotationValidationError("valid current detailed proposals are required for macro grouping")
            raw_sha = proposals.signal_provenance.get("raw_source_sha256")
            if raw_sha and raw_sha != loaded.metadata.research_signal_provenance.get("raw_source_sha256"):
                raise AnnotationValidationError("original RAW evidence changed; regenerate detailed proposals first")
            fresh = generate_macros(proposals, loaded)
            old = self.document(source_id)
            if old:
                if old.source_sha256 != fresh.source_sha256:
                    raise AnnotationValidationError("macro source SHA mismatch; existing review must be reconciled")
                reviewed = {e.id: e for e in old.retained_reviews + old.episodes if e.review_history}
                fresh.episodes = [reviewed.get(e.id, e) for e in fresh.episodes]
                active_ids = {e.id for e in fresh.episodes}
                fresh.retained_reviews = [e for e in reviewed.values() if e.id not in active_ids]
            atomic_write_json(self.path(source_id), fresh.model_dump(mode="json"))
            return self.read(source_id, loaded=loaded)

    def read(self, source_id: str, *, loaded=None) -> dict | None:
        document = self.document(source_id)
        if document is None:
            return None
        loaded = loaded or self.service.load_session(source_id)
        proposals = self.service.storage.read_suggestions(source_id)
        payload = document.model_dump(mode="json")
        payload["stale"] = (document.source_sha256 != loaded.metadata.source.sha256 or proposals is None
                            or document.detailed_content_sha256 != detail_hash(proposals)
                            or loaded.metadata.status != "ok")
        if proposals:
            raw_sha = proposals.signal_provenance.get("raw_source_sha256")
            payload["stale"] |= bool(raw_sha and raw_sha != loaded.metadata.research_signal_provenance.get("raw_source_sha256"))
        payload["progress"] = {axis: progress([e for e in document.episodes if e.axis == axis]) for axis in ("operating", "thermal")}
        payload["labels"] = {"operating": MACRO_VALUES, "thermal": THERMAL_VALUES}
        return payload

    def inspect(self, source_id: str, episode_id: str) -> dict:
        loaded, proposals, _ = self.service.review._current(source_id)
        payload = self.read(source_id, loaded=loaded)
        if not payload or payload["stale"]:
            raise AnnotationValidationError("macro evidence is stale; regenerate before inspection/review")
        episode = next((e for e in payload["episodes"] if e["id"] == episode_id), None)
        if episode is None:
            raise AnnotationValidationError("unknown macro episode")
        # Include all original and currently intersecting children after boundary corrections.
        authoritative = self.service.review.read(source_id, loaded=loaded)
        children = [s for s in authoritative["segments"] if s["id"] in episode["child_ids"]
                    or s["start_s"] < episode["end_s"] and s["end_s"] > episode["start_s"]]
        events = [e for e in authoritative["events"] if e["id"] in episode["event_ids"]
                  or e["start_s"] <= episode["end_s"] and e["end_s"] >= episode["start_s"]]
        transitions = [{"at_s": b.start_s, "from": a.operating_context, "to": b.operating_context}
                       for a, b in zip(proposals.segments, proposals.segments[1:])
                       if a.operating_context != b.operating_context and episode["start_s"] <= b.start_s <= episode["end_s"]]
        return {"episode": episode, "children": children, "events": events, "transitions": transitions,
                "measured_evidence": summary(loaded, episode["start_s"], episode["end_s"]),
                "signal_provenance": proposals.signal_provenance, "timestamp_integrity": proposals.timestamp_integrity}

    def decide(self, source_id: str, episode_id: str, action: str, reviewer: str, *, changes=None, review_time_s=None) -> dict:
        with self.lock:
            loaded, _, _ = self.service.review._current(source_id)
            payload = self.read(source_id, loaded=loaded)
            if not payload or payload["stale"]:
                raise AnnotationValidationError("macro evidence is stale; regenerate before review")
            if action not in ("accept", "reject", "uncertain", "correct") or not reviewer.strip():
                raise AnnotationValidationError("explicit review action and reviewer name required")
            measurement = review_measurement(review_time_s)
            document = self.document(source_id)
            episode = next((e for e in document.episodes if e.id == episode_id), None)
            if episode is None or episode.review_status in ("accepted", "rejected"):
                raise AnnotationValidationError("unknown or finalized macro; finalized human decisions cannot be overwritten")
            changes = changes or {}
            if set(changes) - {"start_s", "end_s", "label", "notes"}:
                raise AnnotationValidationError("unsupported macro correction field")
            before = episode.model_dump(mode="json")
            candidate = MacroEpisode.model_validate({**before, **changes})
            if candidate.end_s > loaded.metadata.duration_s + 1e-9:
                raise AnnotationValidationError("macro range exceeds full source duration")
            if candidate.start_s != episode.start_s or candidate.end_s != episode.end_s:
                candidate.evidence = summary(loaded, candidate.start_s, candidate.end_s)
            if any(e.axis == candidate.axis and e.id != candidate.id and e.review_status == "accepted"
                   and e.start_s < candidate.end_s and e.end_s > candidate.start_s for e in document.episodes):
                raise AnnotationValidationError("macro overlaps an accepted episode on this axis")
            corrections = {k: {"before": before[k], "after": v} for k, v in changes.items() if before[k] != v}
            history = {"action": action, "reviewer": reviewer.strip(), "at": utc_now_iso(), "corrections": corrections, **measurement}
            state = {"accept": "accepted", "reject": "rejected", "uncertain": "uncertain"}.get(action, episode.review_status)
            candidate = MacroEpisode.model_validate({**candidate.model_dump(mode="json"), "review_status": state,
                                                      "review_history": candidate.review_history + [history]})
            document.episodes = [candidate if e.id == episode_id else e for e in document.episodes]
            atomic_write_json(self.path(source_id), document.model_dump(mode="json"))
            return self.read(source_id, loaded=loaded)
