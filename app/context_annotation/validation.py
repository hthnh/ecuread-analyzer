from __future__ import annotations

from dataclasses import dataclass

from app.context_annotation.models import ContextAnnotationDocument, ContextSegment


EPSILON = 1e-9


@dataclass(frozen=True)
class Overlap:
    left_id: str
    right_id: str
    start_s: float
    end_s: float


class AnnotationValidationError(ValueError):
    pass


class AnnotationOverlapError(AnnotationValidationError):
    def __init__(self, overlaps: list[Overlap]):
        self.overlaps = overlaps
        detail = ", ".join(
            f"{item.left_id}/{item.right_id} [{item.start_s:.3f}, {item.end_s:.3f}]"
            for item in overlaps
        )
        super().__init__(f"annotation segments overlap: {detail}")


def find_overlaps(segments: list[ContextSegment]) -> list[Overlap]:
    ordered = sorted(segments, key=lambda segment: (segment.start_s, segment.end_s, segment.id))
    overlaps: list[Overlap] = []
    previous: ContextSegment | None = None
    for segment in ordered:
        if previous is not None and segment.start_s < previous.end_s - EPSILON:
            overlaps.append(
                Overlap(
                    left_id=previous.id,
                    right_id=segment.id,
                    start_s=max(previous.start_s, segment.start_s),
                    end_s=min(previous.end_s, segment.end_s),
                )
            )
        if previous is None or segment.end_s > previous.end_s:
            previous = segment
    return overlaps


def covered_duration(segments: list[ContextSegment], duration_s: float) -> float:
    if duration_s <= 0:
        return 0.0
    clipped: list[tuple[float, float]] = []
    for segment in segments:
        start = max(0.0, min(float(duration_s), segment.start_s))
        end = max(0.0, min(float(duration_s), segment.end_s))
        if end > start:
            clipped.append((start, end))
    if not clipped:
        return 0.0

    clipped.sort()
    merged: list[tuple[float, float]] = [clipped[0]]
    for start, end in clipped[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + EPSILON:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged)


def coverage_ratio(segments: list[ContextSegment], duration_s: float) -> float:
    if duration_s <= 0:
        return 0.0
    return min(1.0, covered_duration(segments, duration_s) / duration_s)


def validate_segments(
    segments: list[ContextSegment],
    *,
    duration_s: float,
    require_non_overlapping: bool = True,
) -> None:
    ids = [segment.id for segment in segments]
    if len(ids) != len(set(ids)):
        raise AnnotationValidationError("segment IDs must be unique")
    for segment in segments:
        if segment.end_s > duration_s + EPSILON:
            raise AnnotationValidationError(
                f"segment {segment.id} ends outside session bounds: {segment.end_s:.6f} > {duration_s:.6f}"
            )
    if require_non_overlapping:
        overlaps = find_overlaps(segments)
        if overlaps:
            raise AnnotationOverlapError(overlaps)


def validate_document(
    document: ContextAnnotationDocument,
    *,
    duration_s: float,
    source_sha256: str,
) -> None:
    if document.source.sha256 != source_sha256:
        raise AnnotationValidationError("annotation source SHA does not match the current source file")
    validate_segments(document.segments, duration_s=duration_s, require_non_overlapping=True)
    for event in document.events:
        if event.end_s > duration_s + EPSILON:
            raise AnnotationValidationError(f"event {event.id} ends outside session bounds")
