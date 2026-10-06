from __future__ import annotations

import json
import math
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from app.domain.telemetry import (
    CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
    SamplingMetadata,
    TelemetrySample,
    CanonicalTelemetrySession,
    V2_SIGNAL_DEFINITIONS,
    V2_CORE_SIGNAL_COLUMNS,
)
from app.ecu.profiles.honda_keihin_71_17 import (
    FRAME_LENGTH,
    HEADER,
    LEGACY_FRAME_LENGTH,
    LEGACY_PREFIX,
    PRODUCTION_DECODER_ID,
    PRODUCTION_DECODER_VERSION,
    PRODUCTION_ECU_PROFILE_ID,
    FrameFormatError,
    bytes_to_hex,
    decode_production_values,
    validate_normalized_frame,
)
from app.ingestion.raw_ecu.parser import _parse_raw_hex_value


NATIVE_SOURCE_TYPE = "native_honda_0x17"
RAW_DECODE_ANALYZE_SOURCE_TYPE = "raw_decode_analyze"
RAW_REPRESENTATION_LEGACY29_FF5 = "legacy29_ff5"
RAW_REPRESENTATION_NATIVE24_TABLE17 = "native24_table17"
SUPPORTED_RAW_REPRESENTATIONS = {
    RAW_REPRESENTATION_LEGACY29_FF5,
    RAW_REPRESENTATION_NATIVE24_TABLE17,
}
NATIVE_TIMESTAMP_FIELDS = ("elapsed_ms", "timestamp_ms", "device_time_ms", "host_monotonic_ms")


@dataclass(slots=True)
class NativeHondaImportResult:
    canonical_session: CanonicalTelemetrySession
    frame_data: pd.DataFrame
    statistics: dict[str, Any]


def _coerce_optional_number(value: Any, field_name: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"{field_name} must be numeric"
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return float(value), None
    if isinstance(value, str) and value.strip():
        try:
            return float(value), None
        except ValueError:
            return None, f"{field_name} must be numeric"
    return None, None


def _extract_timestamp(record: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
    for field_name in NATIVE_TIMESTAMP_FIELDS:
        timestamp_ms, error = _coerce_optional_number(record.get(field_name), field_name)
        if error is not None:
            return None, None, error
        if timestamp_ms is not None:
            return timestamp_ms, field_name, None
    return None, None, None


def _derived_timestamp(
    sequence: int,
    sample_interval_ms: float | None,
    sampling_rate_hz: float | None,
) -> tuple[float | None, str | None]:
    if sample_interval_ms is not None:
        return sequence * sample_interval_ms, "sample_interval_ms"
    if sampling_rate_hz is not None and sampling_rate_hz > 0:
        return sequence * (1000.0 / sampling_rate_hz), "sampling_rate_hz"
    return None, None


def _resolve_timestamp(
    parsed_timestamp_ms: float | None,
    parsed_source: str | None,
    sequence: int,
    sample_interval_ms: float | None,
    sampling_rate_hz: float | None,
) -> tuple[float | None, str | None]:
    if parsed_timestamp_ms is not None:
        return parsed_timestamp_ms, parsed_source
    return _derived_timestamp(sequence, sample_interval_ms, sampling_rate_hz)


def _physical_errors(values: dict[str, Any]) -> list[str]:
    rules = {
        "rpm": (0.0, 16000.0),
        "tps_voltage": (0.0, 5.0),
        "tps_raw": (0.0, 255.0),
        "battery_voltage": (6.0, 18.0),
        "iat_c": (-40.0, 150.0),
        "ect_c": (-40.0, 180.0),
    }
    errors: list[str] = []
    for field_name, (minimum, maximum) in rules.items():
        value = values.get(field_name)
        if not isinstance(value, int | float) or not math.isfinite(float(value)):
            errors.append(f"{field_name} must be finite numeric")
        elif value < minimum or value > maximum:
            errors.append(f"{field_name}={value} outside production range {minimum}..{maximum}")
    return errors


def _invalid_sample(
    *,
    sequence: int,
    timestamp_ms: float | None,
    parse_error: str,
    line_number: int | None,
    timestamp_source: str | None,
    source_format: str = NATIVE_SOURCE_TYPE,
) -> TelemetrySample:
    return TelemetrySample(
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        frame_valid=False,
        checksum_valid=False,
        quality_flags={
            "parse_ok": False,
            "parse_error": parse_error,
            "decoded_signals_valid": False,
            "validation_errors": [parse_error],
            "line_number": line_number,
            "source_format": source_format,
            "timestamp_source": timestamp_source,
        },
    )


def _sample_and_row_from_frame(
    frame: Iterable[int],
    *,
    sequence: int,
    timestamp_ms: float | None = None,
    timestamp_source: str | None = None,
    line_number: int | None = None,
    raw_hex: str | None = None,
    source_format: str = NATIVE_SOURCE_TYPE,
) -> tuple[TelemetrySample, dict[str, Any]]:
    raw_frame = list(frame)
    validation = validate_normalized_frame(raw_frame, source_format=source_format)
    decoded: dict[str, Any] = {}
    decode_error: str | None = None
    physical_validation_errors: list[str] = []

    if validation.length_valid and validation.header_valid and validation.checksum_valid:
        try:
            decoded = decode_production_values(raw_frame, strict_checksum=True, allow_legacy_29_byte=False)
            physical_validation_errors = _physical_errors(decoded)
        except FrameFormatError as exc:
            decode_error = str(exc)

    validation_errors = list(validation.errors)
    if decode_error:
        validation_errors.append(decode_error)
    validation_errors.extend(physical_validation_errors)
    decoded_valid = bool(validation.parse_valid and decoded and not physical_validation_errors and decode_error is None)
    checksum_valid = bool(validation.checksum_valid)

    candidate_signals = {
        "tps_percent_calibrated": decoded.get("tps_percent_calibrated"),
        "iat_voltage_candidate": decoded.get("iat_voltage_candidate"),
        "ect_voltage_candidate": decoded.get("ect_voltage_candidate"),
        "injector_raw": decoded.get("injector_raw"),
        "injector_ms_candidates": decoded.get("injector_ms_candidates"),
        "byte17": decoded.get("byte17"),
        "byte18": decoded.get("byte18"),
        "unknown_reserved_raw": decoded.get("unknown_reserved_raw"),
        "checksum_byte": decoded.get("checksum_byte"),
    }
    quality_flags = {
        "parse_ok": validation.length_valid,
        "parse_error": None if validation.length_valid else "; ".join(validation.errors),
        "decoded_signals_valid": decoded_valid,
        "validation_errors": validation_errors,
        "line_number": line_number,
        "source_format": source_format,
        "timestamp_source": timestamp_source,
        "header_valid": validation.header_valid,
        "length_valid": validation.length_valid,
        "profile_supported": validation.profile_supported,
    }
    sample = TelemetrySample(
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        rpm=decoded.get("rpm"),
        tps_voltage=decoded.get("tps_voltage"),
        tps_raw=decoded.get("tps_raw"),
        battery_voltage=decoded.get("battery_voltage"),
        iat_c=decoded.get("iat_c"),
        ect_c=decoded.get("ect_c"),
        frame_valid=decoded_valid,
        checksum_valid=checksum_valid,
        quality_flags=quality_flags,
        candidate_signals=candidate_signals,
    )
    row = {
        "frame_index": sequence,
        "sequence": sequence,
        "line_number": line_number,
        "raw_hex": raw_hex or bytes_to_hex(raw_frame),
        "normalized_hex": bytes_to_hex(raw_frame) if len(raw_frame) == FRAME_LENGTH else "",
        "relative_time_ms": timestamp_ms,
        "timestamp_source": timestamp_source,
        "source_format": source_format,
        "parse_ok": validation.length_valid,
        "length_valid": validation.length_valid,
        "header_valid": validation.header_valid,
        "checksum_valid": checksum_valid,
        "profile_supported": validation.profile_supported,
        "is_valid": decoded_valid,
        "ml_eligible": bool(decoded_valid and checksum_valid),
        "validation_errors": "; ".join(validation_errors),
    }
    for index in range(FRAME_LENGTH):
        row[f"b{index}"] = raw_frame[index] if len(raw_frame) == FRAME_LENGTH else None
    for column in V2_CORE_SIGNAL_COLUMNS:
        row[column] = getattr(sample, column)
    row.update({key: value for key, value in candidate_signals.items() if key != "injector_ms_candidates"})
    return sample, row


def native_frames_to_canonical_session(
    frames: Iterable[Iterable[int]],
    *,
    timestamps_ms: Iterable[float | None] | None = None,
    session_id: str | None = None,
    device_id: str | None = None,
    vehicle_id: str | None = None,
    sample_interval_ms: float | None = None,
    sampling_rate_hz: float | None = None,
    session_note: str | None = None,
    firmware_version: str | None = None,
) -> NativeHondaImportResult:
    timestamp_values = list(timestamps_ms) if timestamps_ms is not None else []
    samples: list[TelemetrySample] = []
    rows: list[dict[str, Any]] = []
    for sequence, frame in enumerate(frames):
        timestamp_ms = timestamp_values[sequence] if sequence < len(timestamp_values) else None
        timestamp_source = "provided" if timestamp_ms is not None else None
        timestamp_ms, timestamp_source = _resolve_timestamp(
            timestamp_ms,
            timestamp_source,
            sequence,
            sample_interval_ms,
            sampling_rate_hz,
        )
        sample, row = _sample_and_row_from_frame(
            frame,
            sequence=sequence,
            timestamp_ms=timestamp_ms,
            timestamp_source=timestamp_source,
        )
        samples.append(sample)
        rows.append(row)
    return _build_result(
        samples,
        rows,
        session_id=session_id,
        device_id=device_id,
        vehicle_id=vehicle_id,
        sample_interval_ms=sample_interval_ms,
        sampling_rate_hz=sampling_rate_hz,
        session_note=session_note,
        firmware_version=firmware_version,
    )


def _normalize_raw_representation(raw_bytes: list[int], raw_representation: str) -> tuple[list[int], str | None]:
    if raw_representation == RAW_REPRESENTATION_NATIVE24_TABLE17:
        if len(raw_bytes) != FRAME_LENGTH:
            return raw_bytes, f"native24_table17 requires exactly {FRAME_LENGTH} bytes; got {len(raw_bytes)}"
        return raw_bytes, None

    if raw_representation == RAW_REPRESENTATION_LEGACY29_FF5:
        if len(raw_bytes) != LEGACY_FRAME_LENGTH:
            return raw_bytes, f"legacy29_ff5 requires exactly {LEGACY_FRAME_LENGTH} bytes; got {len(raw_bytes)}"
        if raw_bytes[: len(LEGACY_PREFIX)] != LEGACY_PREFIX:
            return raw_bytes, "legacy29_ff5 requires five leading FF bytes"
        return raw_bytes[len(LEGACY_PREFIX) :], None

    supported = ", ".join(sorted(SUPPORTED_RAW_REPRESENTATIONS))
    raise ValueError(f"unsupported raw_representation {raw_representation!r}; supported: {supported}")


def raw_records_to_canonical_session(
    records: Iterable[dict[str, Any]],
    *,
    raw_representation: str,
    session_id: str | None = None,
    device_id: str | None = None,
    vehicle_id: str | None = None,
    sample_interval_ms: float | None = None,
    sampling_rate_hz: float | None = None,
    session_note: str | None = None,
    firmware_version: str | None = None,
) -> NativeHondaImportResult:
    samples: list[TelemetrySample] = []
    rows: list[dict[str, Any]] = []

    for position, record in enumerate(records):
        sequence = int(record.get("sequence", position))
        timestamp_ms, timestamp_source = _resolve_timestamp(
            record.get("timestamp_ms"),
            "provided" if record.get("timestamp_ms") is not None else None,
            sequence,
            sample_interval_ms,
            sampling_rate_hz,
        )
        raw_bytes, parse_error = _parse_raw_hex_value(record.get("raw_hex"))
        normalized = raw_bytes
        if parse_error is None:
            normalized, parse_error = _normalize_raw_representation(raw_bytes, raw_representation)

        if parse_error is not None:
            sample = _invalid_sample(
                sequence=sequence,
                timestamp_ms=timestamp_ms,
                parse_error=parse_error,
                line_number=None,
                timestamp_source=timestamp_source,
                source_format=raw_representation,
            )
            samples.append(sample)
            rows.append(_invalid_row(sample, line_number=None, raw_hex=record.get("raw_hex"), source_format=raw_representation))
            continue

        sample, row = _sample_and_row_from_frame(
            normalized,
            sequence=sequence,
            timestamp_ms=timestamp_ms,
            timestamp_source=timestamp_source,
            raw_hex=record.get("raw_hex"),
            source_format=raw_representation,
        )
        samples.append(sample)
        rows.append(row)

    result = _build_result(
        samples,
        rows,
        session_id=session_id,
        device_id=device_id,
        vehicle_id=vehicle_id,
        sample_interval_ms=sample_interval_ms,
        sampling_rate_hz=sampling_rate_hz,
        session_note=session_note,
        firmware_version=firmware_version,
        source_type=RAW_DECODE_ANALYZE_SOURCE_TYPE,
    )
    result.statistics["raw_representation"] = raw_representation
    return result


def import_native_jsonl_file(
    path: str | Path,
    *,
    session_id: str | None = None,
    device_id: str | None = None,
    vehicle_id: str | None = None,
    sample_interval_ms: float | None = None,
    sampling_rate_hz: float | None = None,
    session_note: str | None = None,
    firmware_version: str | None = None,
) -> NativeHondaImportResult:
    samples: list[TelemetrySample] = []
    rows: list[dict[str, Any]] = []
    total_lines = 0

    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            total_lines += 1
            raw_line = line.strip()
            if not raw_line:
                continue

            sequence = len(samples)
            timestamp_ms: float | None = None
            timestamp_source: str | None = None
            try:
                record = json.loads(raw_line)
            except JSONDecodeError as exc:
                sample = _invalid_sample(
                    sequence=sequence,
                    timestamp_ms=None,
                    parse_error=f"malformed JSON at line {line_number}: {exc.msg}",
                    line_number=line_number,
                    timestamp_source=None,
                    source_format=NATIVE_SOURCE_TYPE,
                )
                samples.append(sample)
                rows.append(_invalid_row(sample, line_number=line_number, raw_hex=raw_line))
                continue
            if not isinstance(record, dict):
                sample = _invalid_sample(
                    sequence=sequence,
                    timestamp_ms=None,
                    parse_error=f"JSONL record at line {line_number} must be an object",
                    line_number=line_number,
                    timestamp_source=None,
                    source_format=NATIVE_SOURCE_TYPE,
                )
                samples.append(sample)
                rows.append(_invalid_row(sample, line_number=line_number, raw_hex=raw_line))
                continue

            timestamp_ms, timestamp_source, timestamp_error = _extract_timestamp(record)
            if timestamp_error is not None:
                sample = _invalid_sample(
                    sequence=sequence,
                    timestamp_ms=None,
                    parse_error=timestamp_error,
                    line_number=line_number,
                    timestamp_source=None,
                    source_format=NATIVE_SOURCE_TYPE,
                )
                samples.append(sample)
                rows.append(_invalid_row(sample, line_number=line_number, raw_hex=record.get("raw_hex", raw_line)))
                continue
            timestamp_ms, timestamp_source = _resolve_timestamp(
                timestamp_ms,
                timestamp_source,
                sequence,
                sample_interval_ms,
                sampling_rate_hz,
            )

            raw_bytes, parse_error = _parse_raw_hex_value(record.get("raw_hex"))
            if parse_error is None and len(raw_bytes) != FRAME_LENGTH:
                parse_error = (
                    f"native Honda 0x17 import requires exactly {FRAME_LENGTH} bytes; "
                    f"got {len(raw_bytes)}"
                )
            if parse_error is None and raw_bytes[: len(HEADER)] != HEADER:
                parse_error = (
                    "unsupported header: "
                    + " ".join(f"{byte:02X}" for byte in raw_bytes[: len(HEADER)])
                    + f" != {' '.join(f'{byte:02X}' for byte in HEADER)}"
                )
            if parse_error is not None:
                sample = _invalid_sample(
                    sequence=sequence,
                    timestamp_ms=timestamp_ms,
                    parse_error=parse_error,
                    line_number=line_number,
                    timestamp_source=timestamp_source,
                    source_format=NATIVE_SOURCE_TYPE,
                )
                samples.append(sample)
                rows.append(_invalid_row(sample, line_number=line_number, raw_hex=record.get("raw_hex", raw_line)))
                continue

            sample, row = _sample_and_row_from_frame(
                raw_bytes,
                sequence=sequence,
                timestamp_ms=timestamp_ms,
                timestamp_source=timestamp_source,
                line_number=line_number,
                raw_hex=record.get("raw_hex"),
            )
            samples.append(sample)
            rows.append(row)

    result = _build_result(
        samples,
        rows,
        session_id=session_id,
        device_id=device_id,
        vehicle_id=vehicle_id,
        sample_interval_ms=sample_interval_ms,
        sampling_rate_hz=sampling_rate_hz,
        session_note=session_note,
        firmware_version=firmware_version,
    )
    result.statistics["total_lines"] = total_lines
    return result


def _invalid_row(
    sample: TelemetrySample,
    *,
    line_number: int | None,
    raw_hex: Any,
    source_format: str = NATIVE_SOURCE_TYPE,
) -> dict[str, Any]:
    error = sample.quality_flags.get("parse_error")
    row = {
        "frame_index": sample.sequence,
        "sequence": sample.sequence,
        "line_number": line_number,
        "raw_hex": raw_hex,
        "normalized_hex": "",
        "relative_time_ms": sample.timestamp_ms,
        "timestamp_source": sample.quality_flags.get("timestamp_source"),
        "source_format": source_format,
        "parse_ok": False,
        "length_valid": False,
        "header_valid": False,
        "checksum_valid": False,
        "profile_supported": False,
        "is_valid": False,
        "ml_eligible": False,
        "validation_errors": error,
    }
    for index in range(FRAME_LENGTH):
        row[f"b{index}"] = None
    for column in V2_CORE_SIGNAL_COLUMNS:
        row[column] = None
    return row


def _build_result(
    samples: list[TelemetrySample],
    rows: list[dict[str, Any]],
    *,
    session_id: str | None,
    device_id: str | None,
    vehicle_id: str | None,
    sample_interval_ms: float | None,
    sampling_rate_hz: float | None,
    session_note: str | None,
    firmware_version: str | None,
    source_type: str = NATIVE_SOURCE_TYPE,
) -> NativeHondaImportResult:
    frame_data = pd.DataFrame(rows)
    canonical = CanonicalTelemetrySession(
        session_id=session_id,
        vehicle_id=vehicle_id,
        device_id=device_id,
        firmware_version=firmware_version,
        session_note=session_note,
        ecu_profile_id=PRODUCTION_ECU_PROFILE_ID,
        decoder_id=PRODUCTION_DECODER_ID,
        decoder_version=PRODUCTION_DECODER_VERSION,
        telemetry_schema_version=CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
        sampling=SamplingMetadata(sample_interval_ms=sample_interval_ms, sampling_rate_hz=sampling_rate_hz),
        samples=samples,
        signal_definitions=V2_SIGNAL_DEFINITIONS.copy(),
        source_type=source_type,
    )
    parsed = int(frame_data["parse_ok"].fillna(False).astype(bool).sum()) if not frame_data.empty else 0
    checksum_valid = int(frame_data["checksum_valid"].fillna(False).astype(bool).sum()) if not frame_data.empty else 0
    valid = int(frame_data["is_valid"].fillna(False).astype(bool).sum()) if not frame_data.empty else 0
    statistics = {
        "total_lines": len(samples),
        "frames_found": len(samples),
        "frames_parsed": parsed,
        "frame_errors": len(samples) - parsed,
        "checksum_valid_frames": checksum_valid,
        "checksum_invalid_frames": parsed - checksum_valid,
        "decoded_valid_frames": valid,
        "decoded_invalid_frames": parsed - valid,
        "source_format": source_type,
        "source_type": source_type,
        "telemetry_schema_version": CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
        "decoder_id": PRODUCTION_DECODER_ID,
        "decoder_version": PRODUCTION_DECODER_VERSION,
    }
    return NativeHondaImportResult(canonical, frame_data, statistics)
