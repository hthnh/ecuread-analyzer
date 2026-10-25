from __future__ import annotations

import json
import math
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import Settings
from app.context_annotation.corpus import active_corpus_manifest, manifest_source, manifest_sources, read_curated_manifest
from app.context_annotation.models import (
    CAMPAIGN_STATUS_SCHEMA_VERSION,
    ANNOTATION_SCHEMA_VERSION_V2,
    OPERATING_VALUES,
    THERMAL_PHASE_VALUES,
    EVENT_VALUES,
    ContextAnnotationDocument,
    ContextLabels,
    ContextSegment,
    ContextEvent,
    OperatingLabels,
    HumanConfirmation,
    CorpusSummary,
    SessionLabels,
    SessionSummary,
    SignalSeries,
    RawCorpusManifest,
)
from app.context_annotation.session_loader import (
    DISPLAY_SIGNAL_SPECS,
    LoadedTelemetrySession,
    discover_sources,
    load_source,
)
from app.context_annotation.storage import AnnotationStorage, atomic_write_json, utc_now_iso
from app.context_annotation.speed import SpeedResolver
from app.context_annotation.validation import (
    AnnotationOverlapError,
    AnnotationValidationError,
    coverage_ratio,
    covered_duration,
    find_overlaps,
    validate_document,
)


class SessionNotFoundError(KeyError):
    pass


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def _sample_value(sample: dict[str, Any], fields: tuple[str, ...]) -> float | None:
    for field in fields:
        value = sample.get(field)
        if _is_finite_number(value):
            return float(value)
    return None


def _segments_payload(segments: list[ContextSegment]) -> list[dict[str, Any]]:
    return [segment.model_dump(mode="json") for segment in sorted(segments, key=lambda item: (item.start_s, item.end_s, item.id))]


def _same_segment_meaning(left: ContextSegment, right: ContextSegment) -> bool:
    return (left.origin == right.origin == "human" and left.taxonomy_version == right.taxonomy_version
            and left.labels == right.labels and left.notes == right.notes
            and left.human_confirmation == right.human_confirmation and left.confirmation_history == right.confirmation_history)


def _clone_segment(segment: ContextSegment, **updates: Any) -> ContextSegment:
    payload = segment.model_dump(mode="json")
    payload.update(updates)
    return ContextSegment.model_validate(payload)


def _confirm_edit(previous: ContextSegment | ContextEvent, payload: dict[str, Any], reviewer: str | None) -> None:
    if previous.origin != "auto_suggested" and not previous.human_confirmation and not reviewer:
        return
    if reviewer is None or not reviewer.strip():
        raise AnnotationValidationError("a reviewer is required to correct accepted auto-origin annotations or named human annotations")
    current = {key: payload[key] for key in ("start_s", "end_s", "notes")}
    current.update(payload.get("labels", {}))
    if "event" in payload:
        current["event"] = payload["event"]
    corrections = {key: value for key, value in current.items() if previous.original_proposal.get(key, "" if key == "notes" else None) != value}
    payload["confirmation_history"] = [item.model_dump(mode="json") for item in previous.confirmation_history]
    if previous.human_confirmation:
        payload["confirmation_history"].append(previous.human_confirmation.model_dump(mode="json"))
    payload["human_confirmation"] = HumanConfirmation(reviewer=reviewer.strip(), confirmed_at=utc_now_iso(), corrections=corrections).model_dump(mode="json")


def _merge_adjacent_segments(segments: list[ContextSegment]) -> list[ContextSegment]:
    ordered = sorted(segments, key=lambda item: (item.start_s, item.end_s, item.id))
    if not ordered:
        return []
    merged: list[ContextSegment] = [ordered[0]]
    for segment in ordered[1:]:
        previous = merged[-1]
        if abs(previous.end_s - segment.start_s) <= 1e-9 and _same_segment_meaning(previous, segment):
            merged[-1] = _clone_segment(previous, end_s=segment.end_s)
        else:
            merged.append(segment)
    return merged


def _next_segment_id(segments: list[ContextSegment]) -> str:
    used = {segment.id for segment in segments}
    index = 1
    while True:
        candidate = f"ctx_{index:03d}"
        if candidate not in used:
            return candidate
        index += 1


def _replace_overlaps(
    existing: list[ContextSegment],
    replacement: ContextSegment,
    *,
    skip_id: str | None = None,
) -> list[ContextSegment]:
    result: list[ContextSegment] = []
    used_ids = {replacement.id}
    for segment in existing:
        if segment.id == skip_id:
            continue
        if segment.end_s <= replacement.start_s or segment.start_s >= replacement.end_s:
            result.append(segment)
            used_ids.add(segment.id)
            continue

        if segment.start_s < replacement.start_s:
            left = _clone_segment(segment, end_s=replacement.start_s)
            result.append(left)
            used_ids.add(left.id)
        if segment.end_s > replacement.end_s:
            right_id = segment.id
            if right_id in used_ids:
                suffix = 1
                while f"{segment.id}_r{suffix}" in used_ids:
                    suffix += 1
                right_id = f"{segment.id}_r{suffix}"
            right = _clone_segment(segment, id=right_id, start_s=replacement.end_s)
            result.append(right)
            used_ids.add(right.id)
    result.append(replacement)
    return _merge_adjacent_segments(result)


class ContextAnnotationService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        storage: AnnotationStorage | None = None,
        input_dir: Path | None = None,
        annotations_dir: Path | None = None,
        exports_dir: Path | None = None,
        corpus_manifest: Path | None = None,
        suggestions_dir: Path | None = None,
        raw_source_map: Path | None = None,
    ) -> None:
        self.settings = settings or Settings()
        if corpus_manifest is None and input_dir is None and storage is None:
            corpus_manifest = active_corpus_manifest(self.settings.data_dir)
        if corpus_manifest is not None and isinstance(read_curated_manifest(corpus_manifest), RawCorpusManifest):
            campaign = corpus_manifest.resolve().parent
            input_dir = input_dir or campaign / "raw"
            annotations_dir = annotations_dir or campaign / "annotations"
            suggestions_dir = suggestions_dir or campaign / "suggestions"
            exports_dir = exports_dir or campaign / "exports"
        self.storage = storage or AnnotationStorage(
            data_dir=self.settings.data_dir,
            input_dir=input_dir,
            annotations_dir=annotations_dir,
            exports_dir=exports_dir,
            suggestions_dir=suggestions_dir,
        )
        self.corpus_manifest = corpus_manifest
        self.speed_resolver = SpeedResolver(raw_source_map)
        from app.context_annotation.review import SuggestionReview
        self.review = SuggestionReview(self)
        from app.context_annotation.macro import MacroReview
        self.macro_review = MacroReview(self)

    def _sources(self) -> list[Any]:
        if self.corpus_manifest is not None:
            return manifest_sources(self.corpus_manifest)
        return discover_sources(self.storage.input_dir)

    def _sources_by_id(self) -> dict[str, Any]:
        return {source.source_id: source for source in self._sources()}

    def _source_for_id(self, source_id: str):
        if self.corpus_manifest is not None:
            source = manifest_source(self.corpus_manifest, source_id)
            if source is None:
                raise SessionNotFoundError(source_id)
            return source
        source = self._sources_by_id().get(source_id)
        if source is None:
            raise SessionNotFoundError(source_id)
        return source

    def load_session(self, source_id: str) -> LoadedTelemetrySession:
        loaded = load_source(self._source_for_id(source_id), self.settings)
        if loaded.metadata.status == "ok":
            speeds, provenance = self.speed_resolver.resolve(loaded)
            loaded.samples = [{**sample, "vehicle_speed_kmh": speed} for sample, speed in zip(loaded.samples, speeds, strict=True)]
            loaded.metadata.available_signals["vehicle_speed_kmh"] = sum(s is not None for s in speeds)
            loaded.metadata.research_signal_provenance = provenance
        return loaded

    def annotation_for(self, loaded: LoadedTelemetrySession) -> ContextAnnotationDocument:
        existing = self.storage.read_annotation(loaded.metadata.source.source_id)
        if existing is not None:
            return existing
        return self.storage.blank_document(
            source_id=loaded.metadata.source.source_id,
            source_filename=loaded.metadata.source.filename,
            source_sha256=loaded.metadata.source.sha256,
            session_id=loaded.metadata.session_id,
            decoder_provenance=loaded.metadata.decoder_provenance,
        )

    def list_sessions(self) -> dict[str, Any]:
        summaries: list[SessionSummary] = []
        corpus = CorpusSummary()
        for source in self._sources():
            loaded = load_source(source, self.settings)
            document = self.storage.read_annotation(source.source_id)
            source_changed = bool(document and document.source.sha256 != source.sha256)
            annotated_duration = (
                covered_duration(document.segments, loaded.metadata.duration_s)
                if document is not None and loaded.metadata.duration_s > 0
                else 0.0
            )
            annotation_coverage = (
                coverage_ratio(document.segments, loaded.metadata.duration_s)
                if document is not None and loaded.metadata.duration_s > 0
                else 0.0
            )

            parse_status = loaded.metadata.status
            if source_changed:
                status = "source_changed"
            elif parse_status != "ok":
                status = parse_status
            elif annotation_coverage >= 0.999:
                status = "complete"
            elif annotation_coverage > 0:
                status = "partial"
            else:
                status = "unstarted"

            summaries.append(
                SessionSummary(
                    source_id=source.source_id,
                    filename=source.filename,
                    session_id=loaded.metadata.session_id,
                    duration_s=loaded.metadata.duration_s,
                    sample_count=loaded.metadata.sample_count,
                    available_signals=loaded.metadata.available_signals,
                    annotation_coverage=annotation_coverage,
                    annotated_duration_s=annotated_duration,
                    status=status,
                    parse_status=parse_status,
                    source_changed=source_changed,
                    expected_sha256=source.expected_sha256,
                    actual_sha256=source.sha256,
                    manifest_reason=source.manifest_reason,
                    error=loaded.metadata.error,
                    capture_time=loaded.metadata.capture_time,
                )
            )

        corpus.total_sessions = len(summaries)
        corpus.fully_annotated = sum(1 for item in summaries if item.status == "complete")
        corpus.partially_annotated = sum(1 for item in summaries if item.status == "partial")
        corpus.unannotated = sum(1 for item in summaries if item.status == "unstarted")
        corpus.parse_failures = sum(1 for item in summaries if item.parse_status != "ok")
        corpus.source_changed = sum(1 for item in summaries if item.source_changed)
        return {
            "summary": corpus.model_dump(mode="json"),
            "sessions": [item.model_dump(mode="json") for item in summaries],
        }

    def session_detail(self, source_id: str) -> dict[str, Any]:
        loaded = self.load_session(source_id)
        document = self.annotation_for(loaded)
        source_changed = document.source.sha256 != loaded.metadata.source.sha256
        sources = list(self._sources())
        index = next((idx for idx, source in enumerate(sources) if source.source_id == source_id), None)
        previous_id = sources[index - 1].source_id if index is not None and index > 0 else None
        next_id = sources[index + 1].source_id if index is not None and index < len(sources) - 1 else None
        from app.context_annotation.attestation import session_provenance
        provenance = session_provenance(self.corpus_manifest, source_id, loaded.metadata.source.sha256) if self.corpus_manifest else None
        return {
            "capture_provenance": provenance,
            "session": loaded.metadata.model_dump(mode="json"),
            "annotation": document.model_dump(mode="json"),
            "source_changed": source_changed,
            "coverage": coverage_ratio(document.segments, loaded.metadata.duration_s),
            "annotated_duration_s": covered_duration(document.segments, loaded.metadata.duration_s),
            "previous_source_id": previous_id,
            "next_source_id": next_id,
            "taxonomy": {
                "operating_context": list(OPERATING_VALUES),
                "thermal_phase": list(THERMAL_PHASE_VALUES),
                "event": list(EVENT_VALUES),
                "engine": ["off", "running", "unknown"],
                "throttle": ["closed", "low", "mid", "high", "unknown"],
                "dynamics": ["steady", "opening", "closing", "transition", "unknown"],
                "thermal": ["cold", "warming", "stable", "unknown"],
                "capture_type": ["road", "stationary", "calibration", "unknown"],
                "start_thermal_state": ["cold", "warm", "unknown"],
            },
            "suggestions": self.review.read(source_id, loaded=loaded),
            "macro_review": self.macro_review.read(source_id, loaded=loaded),
            "legacy_segment_count": sum(isinstance(s.labels, ContextLabels) for s in document.segments),
        }

    def telemetry(
        self,
        source_id: str,
        *,
        start_s: float | None = None,
        end_s: float | None = None,
        max_points: int = 1200,
    ) -> dict[str, Any]:
        loaded = self.load_session(source_id)
        start = 0.0 if start_s is None else max(0.0, float(start_s))
        end = loaded.metadata.duration_s if end_s is None else min(loaded.metadata.duration_s, float(end_s))
        series: list[SignalSeries] = []
        for spec in DISPLAY_SIGNAL_SPECS:
            key = str(spec["key"])
            fields = tuple(str(field) for field in spec["fields"])
            points: list[dict[str, float | None]] = []
            available_count = 0
            for sample, elapsed_s in zip(loaded.samples, loaded.elapsed_s, strict=False):
                if elapsed_s < start or elapsed_s > end:
                    continue
                value = _sample_value(sample, fields)
                if value is not None:
                    available_count += 1
                points.append({"t": elapsed_s, "y": value})
            series.append(
                SignalSeries(
                    key=key,
                    label=str(spec["label"]),
                    unit=loaded.metadata.signal_units.get(key) or str(spec["unit"]),
                    available_count=loaded.metadata.available_signals.get(key, 0),
                    total_count=loaded.metadata.sample_count,
                    points=self._downsample_points(points, max_points=max_points),
                )
            )
        return {
            "source_id": source_id,
            "duration_s": loaded.metadata.duration_s,
            "sample_count": loaded.metadata.sample_count,
            "window": {"start_s": start, "end_s": end},
            "signals": [item.model_dump(mode="json") for item in series],
            "speed_provenance": loaded.metadata.research_signal_provenance,
        }

    def _downsample_points(
        self,
        points: list[dict[str, float | None]],
        *,
        max_points: int,
    ) -> list[dict[str, float | None]]:
        if max_points <= 0 or len(points) <= max_points:
            return points
        bucket_count = max(1, max_points // 2)
        bucket_size = max(1, math.ceil(len(points) / bucket_count))
        output: list[dict[str, float | None]] = []
        for index in range(0, len(points), bucket_size):
            bucket = points[index : index + bucket_size]
            finite = [point for point in bucket if point["y"] is not None]
            if not finite:
                output.append(bucket[0])
                continue
            minimum = min(finite, key=lambda point: float(point["y"]))
            maximum = max(finite, key=lambda point: float(point["y"]))
            if minimum["t"] <= maximum["t"]:
                output.extend([minimum, maximum] if minimum != maximum else [minimum])
            else:
                output.extend([maximum, minimum])
        if points[0] not in output:
            output.insert(0, points[0])
        if points[-1] not in output:
            output.append(points[-1])
        if len(output) > max_points:
            return output[:max_points - 1] + [points[-1]]
        return output

    def save_document(
        self,
        source_id: str,
        document: ContextAnnotationDocument,
    ) -> ContextAnnotationDocument:
        loaded = self.load_session(source_id)
        if loaded.metadata.status != "ok":
            raise AnnotationValidationError(f"cannot save annotations for session status {loaded.metadata.status}")
        if document.source.sha256 != loaded.metadata.source.sha256:
            raise AnnotationValidationError("SOURCE CHANGED")
        payload = document.model_dump(mode="json")
        payload["updated_at"] = utc_now_iso()
        payload["source"] = {
            **payload["source"],
            "filename": loaded.metadata.source.filename,
            "sha256": loaded.metadata.source.sha256,
            "session_id": loaded.metadata.session_id,
            "decoder_provenance": loaded.metadata.decoder_provenance,
        }
        payload["session_id"] = loaded.metadata.session_id
        validated = ContextAnnotationDocument.model_validate(payload)
        validate_document(validated, duration_s=loaded.metadata.duration_s, source_sha256=loaded.metadata.source.sha256)
        self.storage.write_annotation_for_source(source_id, validated)
        return validated

    def update_session_labels(
        self,
        source_id: str,
        labels: SessionLabels,
    ) -> ContextAnnotationDocument:
        loaded = self.load_session(source_id)
        document = self.annotation_for(loaded)
        payload = document.model_dump(mode="json")
        payload["session_labels"] = labels.model_dump(mode="json")
        return self.save_document(source_id, ContextAnnotationDocument.model_validate(payload))

    def add_segment(
        self,
        source_id: str,
        segment_payload: dict[str, Any],
        *,
        overlap_strategy: str = "reject",
        reviewer: str | None = None,
    ) -> ContextAnnotationDocument:
        loaded = self.load_session(source_id)
        document = self.annotation_for(loaded)
        if document.source.sha256 != loaded.metadata.source.sha256:
            raise AnnotationValidationError("SOURCE CHANGED")
        if not segment_payload.get("id"):
            segment_payload = {**segment_payload, "id": _next_segment_id(document.segments)}
        if reviewer and reviewer.strip():
            segment_payload = {**segment_payload, "human_confirmation": HumanConfirmation(reviewer=reviewer.strip(), confirmed_at=utc_now_iso()).model_dump(mode="json")}
        segment = ContextSegment.model_validate(segment_payload)
        if segment.end_s > loaded.metadata.duration_s + 1e-9:
            raise AnnotationValidationError("segment ends outside session bounds")
        overlap_probe = find_overlaps([*document.segments, segment])
        if overlap_probe and overlap_strategy != "replace":
            raise AnnotationOverlapError(overlap_probe)
        segments = (
            _replace_overlaps(document.segments, segment)
            if overlap_strategy == "replace"
            else _merge_adjacent_segments([*document.segments, segment])
        )
        payload = document.model_dump(mode="json")
        payload["segments"] = _segments_payload(segments)
        if isinstance(segment.labels, OperatingLabels):
            payload["schema_version"] = ANNOTATION_SCHEMA_VERSION_V2
        return self.save_document(source_id, ContextAnnotationDocument.model_validate(payload))

    def update_segment(
        self,
        source_id: str,
        segment_id: str,
        segment_payload: dict[str, Any],
        *,
        overlap_strategy: str = "reject",
        reviewer: str | None = None,
    ) -> ContextAnnotationDocument:
        loaded = self.load_session(source_id)
        document = self.annotation_for(loaded)
        if not any(segment.id == segment_id for segment in document.segments):
            raise AnnotationValidationError(f"unknown segment {segment_id}")
        previous = next(item for item in document.segments if item.id == segment_id)
        payload = {**previous.model_dump(mode="json"), **segment_payload, "id": segment_id}
        _confirm_edit(previous, payload, reviewer)
        segment = ContextSegment.model_validate(payload)
        remaining = [item for item in document.segments if item.id != segment_id]
        overlap_probe = find_overlaps([*remaining, segment])
        if overlap_probe and overlap_strategy != "replace":
            raise AnnotationOverlapError(overlap_probe)
        segments = (
            _replace_overlaps(document.segments, segment, skip_id=segment_id)
            if overlap_strategy == "replace"
            else _merge_adjacent_segments([*remaining, segment])
        )
        document_payload = document.model_dump(mode="json")
        document_payload["segments"] = _segments_payload(segments)
        if isinstance(segment.labels, OperatingLabels):
            document_payload["schema_version"] = ANNOTATION_SCHEMA_VERSION_V2
        return self.save_document(source_id, ContextAnnotationDocument.model_validate(document_payload))

    def delete_segment(self, source_id: str, segment_id: str) -> ContextAnnotationDocument:
        loaded = self.load_session(source_id)
        document = self.annotation_for(loaded)
        segments = [segment for segment in document.segments if segment.id != segment_id]
        if len(segments) == len(document.segments):
            raise AnnotationValidationError(f"unknown segment {segment_id}")
        payload = document.model_dump(mode="json")
        payload["segments"] = _segments_payload(segments)
        return self.save_document(source_id, ContextAnnotationDocument.model_validate(payload))

    def save_event(self, source_id: str, changes: dict[str, Any], *, event_id: str | None = None,
                   reviewer: str | None = None) -> ContextAnnotationDocument:
        loaded = self.load_session(source_id)
        document = self.annotation_for(loaded)
        previous = next((e for e in document.events if e.id == event_id), None)
        if event_id is not None and previous is None:
            raise AnnotationValidationError(f"unknown event {event_id}")
        if event_id is None:
            event_id = _next_segment_id(document.events).replace("ctx_", "event_")
            used = {e.id for e in document.events}
            index = 1
            while event_id in used:
                index += 1
                event_id = f"event_{index:03d}"
        event_payload = {**(previous.model_dump(mode="json") if previous else {}), **changes, "id": event_id}
        if previous is not None:
            _confirm_edit(previous, event_payload, reviewer)
        elif reviewer and reviewer.strip():
            event_payload["human_confirmation"] = HumanConfirmation(reviewer=reviewer.strip(), confirmed_at=utc_now_iso()).model_dump(mode="json")
        event = ContextEvent.model_validate(event_payload)
        events = [e for e in document.events if e.id != event_id] + [event]
        payload = {**document.model_dump(mode="json"), "schema_version": ANNOTATION_SCHEMA_VERSION_V2,
                   "events": [e.model_dump(mode="json") for e in sorted(events, key=lambda e: e.start_s)]}
        return self.save_document(source_id, ContextAnnotationDocument.model_validate(payload))

    def delete_event(self, source_id: str, event_id: str) -> ContextAnnotationDocument:
        loaded = self.load_session(source_id)
        document = self.annotation_for(loaded)
        if not any(e.id == event_id for e in document.events):
            raise AnnotationValidationError(f"unknown event {event_id}")
        payload = {**document.model_dump(mode="json"), "events": [e.model_dump(mode="json") for e in document.events if e.id != event_id]}
        return self.save_document(source_id, ContextAnnotationDocument.model_validate(payload))

    def generate_manifest(self) -> dict[str, Any]:
        listing = self.list_sessions()
        sessions = []
        for item in listing["sessions"]:
            loaded = self.load_session(item["source_id"])
            document = self.storage.read_annotation(item["source_id"])
            sessions.append(
                {
                    "source_id": item["source_id"],
                    "source_sha256": loaded.metadata.source.sha256,
                    "filename": item["filename"],
                    "session_id": loaded.metadata.session_id,
                    "duration_s": loaded.metadata.duration_s,
                    "available_signals": loaded.metadata.available_signals,
                    "annotation_coverage": item["annotation_coverage"],
                    "session_labels": (
                        document.session_labels.model_dump(mode="json") if document is not None else SessionLabels().model_dump(mode="json")
                    ),
                    "segment_count": len(document.segments) if document is not None else 0,
                    "event_count": len(document.events) if document is not None else 0,
                    "annotation_version": document.schema_version if document is not None else None,
                    "status": item["status"],
                }
            )
        payload = {
            "schema_version": "context-dataset-manifest-v1",
            "generated_at": utc_now_iso(),
            "dataset_id": (
                read_curated_manifest(self.corpus_manifest).dataset_id
                if self.corpus_manifest is not None
                else None
            ),
            "corpus_manifest": str(self.corpus_manifest) if self.corpus_manifest is not None else None,
            "corpus_summary": listing["summary"],
            "sessions": sessions,
        }
        self.storage.write_export_json("context_dataset_manifest.json", payload)
        return payload

    def generate_campaign_status(self) -> dict[str, Any]:
        listing = self.list_sessions()
        payload = self.build_campaign_status(listing)
        self.storage.write_export_json("annotation_campaign_status.json", payload)
        return payload

    def build_campaign_status(self, listing: dict[str, Any]) -> dict[str, Any]:
        total_duration = 0.0
        annotated_duration = 0.0
        explicitly_unknown_duration = 0.0
        sessions: list[dict[str, Any]] = []
        for item in listing["sessions"]:
            document = self.storage.read_annotation(item["source_id"])
            duration = item["duration_s"] if item["parse_status"] == "ok" else 0.0
            covered = covered_duration(document.segments, duration) if document is not None else 0.0
            unknown = self._explicit_unknown_duration(document.segments, duration) if document is not None else 0.0
            total_duration += duration
            annotated_duration += covered
            explicitly_unknown_duration += unknown
            sessions.append(
                {
                    "source_id": item["source_id"],
                    "session_id": item["session_id"],
                    "status": item["status"],
                    "parse_status": item["parse_status"],
                    "duration_s": duration,
                    "annotated_duration_s": covered,
                    "explicitly_unknown_duration_s": unknown,
                    "truly_unannotated_duration_s": max(0.0, duration - covered),
                    "annotation_file_present": document is not None,
                    "missing_annotation_not_hidden_as_unknown": document is not None or unknown == 0.0,
                }
            )
        payload = {
            "schema_version": CAMPAIGN_STATUS_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "corpus_manifest": str(self.corpus_manifest) if self.corpus_manifest is not None else None,
            "total_curated_sessions": listing["summary"]["total_sessions"],
            "unstarted": listing["summary"]["unannotated"],
            "partial": listing["summary"]["partially_annotated"],
            "complete": listing["summary"]["fully_annotated"],
            "total_corpus_duration_s": total_duration,
            "annotated_duration_s": annotated_duration,
            "explicitly_unknown_duration_s": explicitly_unknown_duration,
            "truly_unannotated_duration_s": max(0.0, total_duration - annotated_duration),
            "sessions": sessions,
        }
        return payload

    def export_context_records(self, output_path: Path | None = None) -> dict[str, Any]:
        output = output_path or self.storage.exports_dir / "context_canonical_records.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        started = perf_counter()
        record_count = 0
        session_count = 0
        tmp_path = output.with_suffix(output.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for source in self._sources():
                loaded = load_source(source, self.settings)
                if loaded.metadata.status != "ok":
                    continue
                document = self.storage.read_annotation(source.source_id)
                segments = sorted(document.segments, key=lambda item: item.start_s) if document else []
                session_count += 1
                for sample, elapsed_s in zip(loaded.samples, loaded.elapsed_s, strict=False):
                    segment = self._segment_for_time(segments, elapsed_s, loaded.metadata.duration_s)
                    labels = segment.labels.model_dump(mode="json") if segment else {}
                    row = {
                        "source_id": source.source_id,
                        "session_id": loaded.metadata.session_id,
                        "timestamp_ms": sample.get("timestamp_ms"),
                        "elapsed_s": elapsed_s,
                        "rpm": _sample_value(sample, ("rpm",)),
                        "tps_voltage": _sample_value(sample, ("tps_voltage",)),
                        "tps_raw": _sample_value(sample, ("tps_raw", "tps_raw_candidate")),
                        "battery_voltage": _sample_value(sample, ("battery_voltage",)),
                        "ect_c": _sample_value(sample, ("ect_c", "ect_c_candidate")),
                        "iat_c": _sample_value(sample, ("iat_c",)),
                        "context_engine": labels.get("engine"),
                        "context_throttle": labels.get("throttle"),
                        "context_dynamics": labels.get("dynamics"),
                        "context_thermal": labels.get("thermal"),
                        "context_operating_context": labels.get("operating_context"),
                        "context_thermal_phase": labels.get("thermal_phase"),
                        "context_taxonomy_version": segment.taxonomy_version if segment else None,
                        "context_origin": segment.origin if segment else None,
                        "context_review_status": segment.review_status if segment else None,
                    }
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
                    record_count += 1
        tmp_path.replace(output)
        return {
            "path": str(output),
            "session_count": session_count,
            "record_count": record_count,
            "duration_seconds": round(perf_counter() - started, 4),
        }

    def _segment_for_time(
        self,
        segments: list[ContextSegment],
        elapsed_s: float,
        duration_s: float,
    ) -> ContextSegment | None:
        for segment in segments:
            if segment.start_s <= elapsed_s < segment.end_s:
                return segment
            if abs(elapsed_s - duration_s) <= 1e-9 and abs(segment.end_s - duration_s) <= 1e-9:
                return segment
        return None

    def _explicit_unknown_duration(
        self,
        segments: list[ContextSegment],
        duration_s: float,
    ) -> float:
        unknown_segments = [
            segment
            for segment in segments
            if "unknown" in segment.labels.model_dump(mode="json").values()
        ]
        return covered_duration(unknown_segments, duration_s)

    def corpus_diagnostics(self) -> dict[str, Any]:
        listing = self.list_sessions()
        signal_counts: dict[str, int] = {str(spec["key"]): 0 for spec in DISPLAY_SIGNAL_SPECS}
        total_duration = 0.0
        largest_session = {"source_id": None, "sample_count": 0, "duration_s": 0.0}
        for item in listing["sessions"]:
            if item["parse_status"] != "ok":
                continue
            total_duration += float(item["duration_s"])
            for key, count in item["available_signals"].items():
                if count > 0:
                    signal_counts[key] = signal_counts.get(key, 0) + 1
            if item["sample_count"] > largest_session["sample_count"]:
                largest_session = {
                    "source_id": item["source_id"],
                    "sample_count": item["sample_count"],
                    "duration_s": item["duration_s"],
                }
        return {
            "sessions_discovered": listing["summary"]["total_sessions"],
            "sessions_successfully_parsed": sum(1 for item in listing["sessions"] if item["parse_status"] == "ok"),
            "parse_failures": listing["summary"]["parse_failures"],
            "signal_availability_counts": signal_counts,
            "total_duration_s": total_duration,
            "largest_session": largest_session,
        }
