from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Callable, Iterable


DEFAULT_DECODED_FIELDS = [
    "rpm",
    "tps_voltage",
    "tps_percent",
    "iat_c",
    "ect_c",
    "battery_v",
    "injector_raw",
    "injector_ms",
    "fuel_cut_inferred",
]


@dataclass(slots=True)
class FormulaMatch:
    decoded_field: str
    best_raw_source: str
    best_transform: str
    exact_match_ratio: float
    mean_error: float
    maximum_error: float
    sample_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CandidateTransform:
    raw_source: str
    transform: str
    values: list[float]


def _byte_values(records: list[dict[str, Any]], byte_index: int) -> list[float]:
    return [float(record["frame"][byte_index]) for record in records]


def _word_values(records: list[dict[str, Any]], byte_index: int, *, endian: str) -> list[float]:
    values: list[float] = []
    for record in records:
        frame = record["frame"]
        if endian == "big":
            value = (frame[byte_index] << 8) | frame[byte_index + 1]
        else:
            value = (frame[byte_index + 1] << 8) | frame[byte_index]
        values.append(float(value))
    return values


def _apply(values: list[float], transform_name: str, func: Callable[[float], float]) -> CandidateTransform:
    return CandidateTransform(raw_source="", transform=transform_name, values=[func(value) for value in values])


def candidate_transforms(records: list[dict[str, Any]]) -> list[CandidateTransform]:
    candidates: list[CandidateTransform] = []

    for byte_index in range(24):
        source = f"byte {byte_index}"
        raw = _byte_values(records, byte_index)
        definitions: list[tuple[str, Callable[[float], float]]] = [
            ("raw", lambda value: value),
            ("raw - 40", lambda value: value - 40.0),
            ("raw / 10", lambda value: value / 10.0),
            ("raw * 5 / 256", lambda value: value * 5.0 / 256.0),
            ("raw * 100 / 255", lambda value: value * 100.0 / 255.0),
            ("raw == 0", lambda value: 1.0 if value == 0 else 0.0),
        ]
        for name, func in definitions:
            transformed = _apply(raw, name, func)
            transformed.raw_source = source
            candidates.append(transformed)

    for byte_index in range(23):
        for endian, endian_label in [("big", "big-endian"), ("little", "little-endian")]:
            source = f"bytes {byte_index}-{byte_index + 1}"
            raw = _word_values(records, byte_index, endian=endian)
            definitions = [
                (f"{endian_label} word", lambda value: value),
                (f"{endian_label} word / 10", lambda value: value / 10.0),
                (f"{endian_label} word / 100", lambda value: value / 100.0),
                (f"{endian_label} word / 250", lambda value: value / 250.0),
                (f"{endian_label} word / 256", lambda value: value / 256.0),
                (f"{endian_label} word == 0", lambda value: 1.0 if value == 0 else 0.0),
            ]
            for name, func in definitions:
                transformed = _apply(raw, name, func)
                transformed.raw_source = source
                candidates.append(transformed)

    return candidates


def _coerce_numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    return None


def _field_values(records: list[dict[str, Any]], field_name: str) -> list[float | None]:
    values: list[float | None] = []
    for record in records:
        decoded_fields = record.get("decoded_fields", {})
        values.append(_coerce_numeric(decoded_fields.get(field_name)))
    return values


def _score_candidate(
    expected: list[float | None],
    candidate: CandidateTransform,
    *,
    tolerance: float,
) -> tuple[float, float, float, int]:
    errors: list[float] = []
    exact = 0
    sample_count = 0
    for expected_value, candidate_value in zip(expected, candidate.values, strict=False):
        if expected_value is None:
            continue
        error = abs(expected_value - candidate_value)
        errors.append(error)
        sample_count += 1
        if error <= tolerance:
            exact += 1
    if not errors:
        return 0.0, float("inf"), float("inf"), 0
    exact_ratio = exact / len(errors)
    return exact_ratio, fmean(errors), max(errors), sample_count


def infer_formula_matches(
    records: Iterable[dict[str, Any]],
    *,
    decoded_fields: list[str] | None = None,
    tolerance: float = 0.005,
) -> list[FormulaMatch]:
    usable_records = [record for record in records if record.get("frame") is not None]
    if not usable_records:
        return []

    fields = decoded_fields or DEFAULT_DECODED_FIELDS
    transforms = candidate_transforms(usable_records)
    matches: list[FormulaMatch] = []

    for field_name in fields:
        expected = _field_values(usable_records, field_name)
        if all(value is None for value in expected):
            continue
        best: tuple[float, float, float, int, CandidateTransform] | None = None
        for transform in transforms:
            exact_ratio, mean_error, max_error, sample_count = _score_candidate(
                expected, transform, tolerance=tolerance
            )
            if sample_count == 0:
                continue
            sort_key = (exact_ratio, -mean_error, -max_error)
            if best is None or sort_key > (best[0], -best[1], -best[2]):
                best = (exact_ratio, mean_error, max_error, sample_count, transform)
        if best is None:
            continue
        exact_ratio, mean_error, max_error, sample_count, transform = best
        matches.append(
            FormulaMatch(
                decoded_field=field_name,
                best_raw_source=transform.raw_source,
                best_transform=transform.transform,
                exact_match_ratio=exact_ratio,
                mean_error=mean_error,
                maximum_error=max_error,
                sample_count=sample_count,
            )
        )

    return matches

