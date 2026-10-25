"""Research-only Table 17 speed resolution; production decoding stays unchanged."""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.audit.loading import parse_raw_hex
from app.context_annotation.session_loader import LoadedTelemetrySession
from app.context_annotation.storage import file_sha256
from app.ecu.profiles.honda_keihin_71_17 import (
    FrameFormatError, decode_production_values, normalize_frame, validate_normalized_frame,
)
from app.ingestion.raw_ecu.parser import parse_log_text


REPO_ROOT = Path(__file__).resolve().parents[2]
SPEED_DECODER_VERSION = "ctx2-honda-table17-speed-v1"
SIGNATURE_FIELDS = ("rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c")


def finite(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def decode_speed(frame: list[int]) -> tuple[float | None, dict[str, Any]]:
    try:
        normalized, representation = normalize_frame(frame)
    except FrameFormatError as exc:
        return None, {"valid": False, "errors": [str(exc)]}
    validation = validate_normalized_frame(normalized, source_format=representation)
    info = {"valid": validation.parse_valid, "representation": representation, "errors": validation.errors}
    return (float(normalized[19]) if validation.parse_valid else None), info


def sample_signature(sample: dict[str, Any]) -> tuple[Any, ...]:
    candidate = sample.get("candidate_signals") or {}
    return tuple(sample.get(field) for field in SIGNATURE_FIELDS) + tuple(candidate.get("unknown_reserved_raw") or [])


@dataclass
class RawCapture:
    signatures: list[tuple[Any, ...] | None]
    speeds: list[float | None]
    timestamps_ms: list[float | None]
    timestamp_candidates: dict[str, list[float | None]]
    validation_counts: dict[str, int]


@lru_cache(maxsize=24)
def read_raw_capture(path: str, sha256: str) -> RawCapture:
    source = Path(path)
    if file_sha256(source) != sha256:
        raise ValueError("original RAW SHA mismatch")
    if source.suffix.lower() == ".jsonl":
        records = []
        with source.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    records.append(record if isinstance(record, dict) else {})
                except ValueError:
                    records.append({})
    else:
        records = [{"frame": frame.bytes, "timestamp_ms": frame.timestamp_ms}
                   for frame in parse_log_text(source.read_text(encoding="utf-8", errors="replace")).frames]
    signatures, speeds, timestamps = [], [], []
    counts: Counter[str] = Counter()
    time_fields = ("elapsed_ms", "timestamp_ms", "device_time_ms", "host_monotonic_ms")
    candidates = {key: [] for key in time_fields}
    for record in records:
        for key in time_fields:
            candidates[key].append(finite(record.get(key)))
        timestamps.append(next((finite(record.get(k)) for k in time_fields if finite(record.get(k)) is not None), None))
        try:
            frame = record.get("frame") or parse_raw_hex(record.get("raw_hex", ""))
            speed, validation = decode_speed(frame)
            counts["valid_frames" if validation["valid"] else "invalid_frames"] += 1
            if validation.get("representation"):
                counts[validation["representation"]] += 1
            if speed is None:
                signatures.append(None)
            else:
                normalized, _ = normalize_frame(frame)
                decoded = decode_production_values(normalized)
                signatures.append(tuple(decoded.get(k) for k in SIGNATURE_FIELDS) + tuple(decoded["unknown_reserved_raw"]))
            speeds.append(speed)
        except (ValueError, TypeError, KeyError):
            signatures.append(None)
            speeds.append(None)
            counts["invalid_frames"] += 1
    if file_sha256(source) != sha256:
        raise ValueError("original RAW changed while being read")
    return RawCapture(signatures, speeds, timestamps, candidates, dict(counts))


def aligned_timestamp_field(capture: RawCapture, offset: int, samples: list[dict[str, Any]]) -> str | None:
    canonical = [finite(sample.get("timestamp_ms")) for sample in samples]
    if not canonical or any(t is None for t in canonical):
        return None
    for field, values in capture.timestamp_candidates.items():
        raw = values[offset:offset + len(samples)]
        if len(raw) != len(samples) or any(t is None for t in raw):
            continue
        if all(abs((t - raw[0]) - (c - canonical[0])) <= 5.0 for t, c in zip(raw, canonical, strict=True)):
            return field
    return None


class SpeedResolver:
    def __init__(self, source_map: Path | None = None) -> None:
        self.source_map = source_map or REPO_ROOT / "data/context_annotation/raw_signal_sources.json"

    def resolve(self, loaded: LoadedTelemetrySession) -> tuple[list[float | None], dict[str, Any]]:
        missing = [None] * len(loaded.samples)
        info: dict[str, Any] = {
            "signal": "vehicle_speed_kmh", "decoder_version": SPEED_DECODER_VERSION,
            "native_byte_index": 19, "unit": "km/h", "calibration": "rider_corroborated_not_gps_calibrated",
            "available_count": 0, "total_count": len(missing), "status": "unavailable",
        }
        source_path = Path(loaded.metadata.source.path)
        try:
            if source_path.is_file() and loaded.metadata.source_format not in {"canonical_json", "canonical_samples_jsonl"}:
                raw_path, expected_sha, offset = source_path, loaded.metadata.source.sha256, 0
                mapping = None
            else:
                mappings = json.loads(self.source_map.read_text(encoding="utf-8"))["sessions"] if self.source_map.exists() else []
                mapping = next((m for m in mappings if m["session_id"] == loaded.metadata.source.source_id), None)
                if not mapping or not mapping.get("raw_source_path"):
                    info["reason"] = "No accessible, verified original RAW range for this canonical session."
                    return missing, info
                if mapping["canonical_sha256"] != loaded.metadata.source.sha256:
                    raise ValueError("RAW mapping canonical SHA mismatch")
                raw_path = Path(mapping["raw_source_path"])
                if not raw_path.is_absolute():
                    raw_path = REPO_ROOT / raw_path
                expected_sha, offset = mapping["raw_source_sha256"], mapping["sample_offset"]
            if not raw_path.exists():
                raise ValueError("original RAW is missing")
            if file_sha256(raw_path) != expected_sha:
                raise ValueError("original RAW SHA mismatch")
            capture = read_raw_capture(str(raw_path), expected_sha)
            speeds = capture.speeds[offset:offset + len(missing)]
            if len(speeds) != len(missing):
                raise ValueError("original RAW sample count mismatch")
            if mapping:
                if mapping["sample_count"] != len(loaded.samples):
                    raise ValueError("RAW mapping sample count mismatch")
                if any(sample_signature(sample) != signature for sample, signature in
                       zip(loaded.samples, capture.signatures[offset:offset + len(missing)], strict=True)):
                    raise ValueError("original RAW sample alignment mismatch")
                field = aligned_timestamp_field(capture, offset, loaded.samples)
                if field is None or field != mapping["timestamp_field"]:
                    raise ValueError("original RAW timestamp alignment mismatch")
            info.update({
                "status": "available" if all(s is not None for s in speeds) else "partial",
                "origin": "original_raw", "raw_source_path": _source_reference(raw_path), "raw_source_sha256": expected_sha,
                "sample_offset": offset, "available_count": sum(s is not None for s in speeds),
                "validation_counts": capture.validation_counts,
                "alignment": "full_signal_sequence_and_timestamp_intervals" if mapping else "direct_source_record_order",
            })
            return speeds, info
        except (OSError, ValueError, KeyError, TypeError) as exc:
            info.update({"status": "unavailable", "reason": str(exc)})
            return missing, info


def _source_reference(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)
