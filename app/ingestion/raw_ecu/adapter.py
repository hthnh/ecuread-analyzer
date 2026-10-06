from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings
from app.domain.telemetry import CanonicalTelemetrySession, SamplingMetadata, TelemetrySample
from app.ingestion.raw_ecu.checksum import EXPECTED_FRAME_LENGTH
from app.ingestion.raw_ecu.decoder import TRUSTED_SIGNAL_COLUMNS, UNKNOWN_SIGNAL_COLUMNS, decode_frame
from app.ingestion.raw_ecu.parser import ParseResult, parse_log_file, parse_log_text
from app.ingestion.raw_ecu.validation import validate_decoded_signals


RAW_COMPAT_ECU_PROFILE_ID = "honda_keihin_legacy_29"
RAW_COMPAT_DECODER_ID = "honda_keihin_legacy_29"
RAW_COMPAT_DECODER_VERSION = "0.1.0"


@dataclass(slots=True)
class RawImportResult:
    canonical_session: CanonicalTelemetrySession
    frame_data: pd.DataFrame
    statistics: dict[str, Any]


def relative_time_for_frame(
    frame_index: int,
    sample_interval_ms: float | None,
    sampling_rate_hz: float | None,
) -> float | None:
    if sample_interval_ms is not None:
        return frame_index * sample_interval_ms
    if sampling_rate_hz is not None and sampling_rate_hz > 0:
        return frame_index * (1000.0 / sampling_rate_hz)
    return None


def timestamp_for_frame(
    parsed_timestamp_ms: float | None,
    frame_index: int,
    sample_interval_ms: float | None,
    sampling_rate_hz: float | None,
) -> float | None:
    if parsed_timestamp_ms is not None:
        return parsed_timestamp_ms
    return relative_time_for_frame(frame_index, sample_interval_ms, sampling_rate_hz)


def parse_result_to_canonical_session(
    parse_result: ParseResult,
    settings: Settings,
    *,
    session_id: str | None = None,
    device_id: str | None = None,
    vehicle_id: str | None = None,
    sample_interval_ms: float | None = None,
    sampling_rate_hz: float | None = None,
    session_note: str | None = None,
    firmware_version: str | None = None,
    decoder_id: str = RAW_COMPAT_DECODER_ID,
    decoder_version: str = RAW_COMPAT_DECODER_VERSION,
    ecu_profile_id: str = RAW_COMPAT_ECU_PROFILE_ID,
) -> RawImportResult:
    rows: list[dict[str, Any]] = []
    samples: list[TelemetrySample] = []
    decoded_fields = TRUSTED_SIGNAL_COLUMNS + UNKNOWN_SIGNAL_COLUMNS

    for parsed in parse_result.frames:
        row: dict[str, Any] = parsed.to_dict()
        row["validation_errors"] = ""
        row["is_valid"] = False
        row["ml_eligible"] = False
        row["source_timestamp_ms"] = parsed.timestamp_ms
        row["timestamp_source"] = parsed.timestamp_source
        row["relative_time_ms"] = timestamp_for_frame(
            parsed.timestamp_ms,
            parsed.frame_index,
            sample_interval_ms,
            sampling_rate_hz,
        )

        for byte_index in range(EXPECTED_FRAME_LENGTH):
            row[f"b{byte_index}"] = (
                parsed.bytes[byte_index]
                if parsed.parse_ok and len(parsed.bytes) == EXPECTED_FRAME_LENGTH
                else None
            )
        for field_name in decoded_fields:
            row[field_name] = None

        decoded: dict[str, Any] = {}
        validation_errors: list[str] = []
        decoded_valid = False
        if parsed.parse_ok and len(parsed.bytes) == EXPECTED_FRAME_LENGTH:
            decoded = decode_frame(
                parsed.bytes,
                parsed.frame_index,
                relative_time_ms=row["relative_time_ms"],
            ).to_dict()
            decoded_valid, validation_errors = validate_decoded_signals(decoded, settings.validation_limits)
            row.update(decoded)
            row["is_valid"] = decoded_valid
            row["validation_errors"] = "; ".join(validation_errors)
            row["ml_eligible"] = bool(parsed.checksum_valid and decoded_valid)

        quality_flags = {
            "parse_ok": parsed.parse_ok,
            "parse_error": parsed.parse_error,
            "decoded_signals_valid": decoded_valid,
            "validation_errors": validation_errors,
            "line_number": parsed.line_number,
            "source_format": parsed.source_format,
            "timestamp_source": parsed.timestamp_source,
        }
        samples.append(
            TelemetrySample(
                sequence=parsed.frame_index,
                timestamp_ms=row["relative_time_ms"],
                rpm=decoded.get("rpm"),
                tps_voltage=decoded.get("tps_voltage"),
                tps_raw_candidate=decoded.get("tps_raw_candidate"),
                battery_voltage=decoded.get("battery_voltage"),
                iat_c=decoded.get("iat_c"),
                ect_c_candidate=decoded.get("ect_c_candidate"),
                map_raw=decoded.get("map_raw"),
                frame_valid=bool(parsed.parse_ok and decoded_valid),
                checksum_valid=bool(parsed.checksum_valid),
                quality_flags=quality_flags,
                candidate_signals={
                    "signal_b19": decoded.get("signal_b19"),
                    "signal_word_20_21": decoded.get("signal_word_20_21"),
                    "signal_b22": decoded.get("signal_b22"),
                    "signal_b23": decoded.get("signal_b23"),
                    "signal_b24": decoded.get("signal_b24"),
                },
            )
        )
        rows.append(row)

    columns = [
        "frame_index",
        "line_number",
        "raw_frame",
        "bytes_hex",
        "parse_ok",
        "parse_error",
        "checksum_valid",
        "source_format",
        "source_timestamp_ms",
        "timestamp_source",
        "relative_time_ms",
        "is_valid",
        "validation_errors",
        "ml_eligible",
        *[f"b{index}" for index in range(EXPECTED_FRAME_LENGTH)],
        *TRUSTED_SIGNAL_COLUMNS,
        *UNKNOWN_SIGNAL_COLUMNS,
    ]
    frame_data = pd.DataFrame(rows, columns=columns)
    canonical = CanonicalTelemetrySession(
        session_id=session_id,
        vehicle_id=vehicle_id,
        device_id=device_id,
        firmware_version=firmware_version,
        session_note=session_note,
        ecu_profile_id=ecu_profile_id,
        decoder_id=decoder_id,
        decoder_version=decoder_version,
        sampling=SamplingMetadata(sample_interval_ms=sample_interval_ms, sampling_rate_hz=sampling_rate_hz),
        samples=samples,
        source_type="raw_compatibility_adapter",
    )
    return RawImportResult(
        canonical_session=canonical,
        frame_data=frame_data,
        statistics=parse_result.statistics.to_dict(),
    )


def import_raw_text(
    text: str,
    settings: Settings,
    **metadata: Any,
) -> RawImportResult:
    return parse_result_to_canonical_session(parse_log_text(text), settings, **metadata)


def import_raw_file(
    path: str | Path,
    settings: Settings,
    **metadata: Any,
) -> RawImportResult:
    return parse_result_to_canonical_session(parse_log_file(path), settings, **metadata)
