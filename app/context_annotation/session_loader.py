from __future__ import annotations

import json
import math
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.context_annotation.models import LoadedSession, SessionSource
from app.context_annotation.storage import source_id_for_path, source_tree_sha256
from app.domain.telemetry import (
    CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
    CanonicalTelemetrySession,
    SamplingMetadata,
    TelemetrySample,
    V2_SIGNAL_DEFINITIONS,
)
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.ingestion.raw_ecu.adapter import import_raw_file


DISPLAY_SIGNAL_SPECS: tuple[dict[str, object], ...] = (
    {"key": "rpm", "label": "RPM", "unit": "rpm", "fields": ("rpm",)},
    {"key": "tps_voltage", "label": "TPS voltage", "unit": "V", "fields": ("tps_voltage",)},
    {"key": "tps_raw", "label": "TPS raw", "unit": "raw", "fields": ("tps_raw", "tps_raw_candidate")},
    {"key": "vehicle_speed_kmh", "label": "Vehicle speed (candidate)", "unit": "km/h", "fields": ("vehicle_speed_kmh",)},
    {"key": "battery_voltage", "label": "Battery voltage", "unit": "V", "fields": ("battery_voltage",)},
    {"key": "ect_c", "label": "ECT", "unit": "degC", "fields": ("ect_c", "ect_c_candidate")},
    {"key": "iat_c", "label": "IAT", "unit": "degC", "fields": ("iat_c",)},
)
REQUIRED_CONTEXT_SIGNALS = ("rpm",)
SUPPORTED_FILE_SUFFIXES = {".jsonl", ".txt", ".log", ".json", ".dat"}


@dataclass(slots=True)
class LoadedTelemetrySession:
    metadata: LoadedSession
    canonical_session: CanonicalTelemetrySession | None
    samples: list[dict[str, Any]]
    elapsed_s: list[float]


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def _first_jsonl_record(path: Path) -> dict[str, Any] | None:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
    return None


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("JSONL records must be objects")
            rows.append(parsed)
    return rows


def _sample_value(sample: dict[str, Any], fields: tuple[str, ...]) -> float | None:
    for field in fields:
        value = sample.get(field)
        if _is_finite_number(value):
            return float(value)
    return None


def _derive_elapsed_seconds(
    samples: list[dict[str, Any]],
    sampling: SamplingMetadata | None,
) -> list[float]:
    raw_timestamps: list[float | None] = []
    for index, sample in enumerate(samples):
        value = sample.get("timestamp_ms")
        if _is_finite_number(value):
            raw_timestamps.append(float(value))
            continue
        if sampling and sampling.sample_interval_ms is not None:
            raw_timestamps.append(index * float(sampling.sample_interval_ms))
            continue
        if sampling and sampling.sampling_rate_hz is not None and sampling.sampling_rate_hz > 0:
            raw_timestamps.append(index * (1000.0 / float(sampling.sampling_rate_hz)))
            continue
        raw_timestamps.append(None)

    first_timestamp = next((value for value in raw_timestamps if value is not None), None)
    if first_timestamp is None:
        return [float(index) for index in range(len(samples))]
    elapsed: list[float] = []
    for index, value in enumerate(raw_timestamps):
        if value is None:
            elapsed.append(float(index))
        else:
            elapsed.append(max(0.0, (value - first_timestamp) / 1000.0))
    return elapsed


def _available_signals(samples: list[dict[str, Any]]) -> dict[str, int]:
    availability: dict[str, int] = {}
    for spec in DISPLAY_SIGNAL_SPECS:
        key = str(spec["key"])
        fields = tuple(str(field) for field in spec["fields"])
        availability[key] = sum(1 for sample in samples if _sample_value(sample, fields) is not None)
    return availability


def _signal_units(session: CanonicalTelemetrySession | None) -> dict[str, str]:
    units: dict[str, str] = {}
    definitions = session.signal_definitions if session is not None else {}
    for spec in DISPLAY_SIGNAL_SPECS:
        key = str(spec["key"])
        fields = tuple(str(field) for field in spec["fields"])
        unit = str(spec["unit"])
        for field in fields:
            if field in definitions and definitions[field].get("unit"):
                unit = str(definitions[field]["unit"])
                break
        units[key] = unit
    return units


def _decoder_provenance(session: CanonicalTelemetrySession | None) -> dict[str, object]:
    if session is None:
        return {}
    return {
        "ecu_profile_id": session.ecu_profile_id,
        "decoder_id": session.decoder_id,
        "decoder_version": session.decoder_version,
        "telemetry_schema_version": session.telemetry_schema_version,
        "source_type": session.source_type,
        "signal_definitions": session.signal_definitions,
    }


def _loaded_from_session(
    *,
    source: SessionSource,
    session: CanonicalTelemetrySession,
    source_format: str | None = None,
    capture_time: str | None = None,
) -> LoadedTelemetrySession:
    samples = [sample.model_dump(mode="json") for sample in session.samples]
    elapsed_s = _derive_elapsed_seconds(samples, session.sampling)
    duration_s = max(elapsed_s) if elapsed_s else 0.0
    availability = _available_signals(samples)
    missing_required = (any(availability.get(signal, 0) == 0 for signal in REQUIRED_CONTEXT_SIGNALS)
                        or (availability.get("tps_voltage", 0) == 0 and availability.get("tps_raw", 0) == 0))
    metadata = LoadedSession(
        source=source,
        status="missing_required_signals" if missing_required else "ok",
        error="context review requires RPM and either TPS voltage or TPS raw" if missing_required else None,
        session_id=session.session_id or source.source_id,
        duration_s=duration_s,
        sample_count=len(samples),
        available_signals=availability,
        signal_units=_signal_units(session),
        decoder_provenance=_decoder_provenance(session),
        capture_time=capture_time,
        telemetry_schema_version=session.telemetry_schema_version,
        source_format=source_format or session.source_type,
    )
    return LoadedTelemetrySession(metadata=metadata, canonical_session=session, samples=samples, elapsed_s=elapsed_s)


def _error_loaded(
    *,
    source: SessionSource,
    status: str,
    error: str,
) -> LoadedTelemetrySession:
    metadata = LoadedSession(
        source=source,
        status=status,  # type: ignore[arg-type]
        error=error,
        session_id=source.source_id,
        duration_s=0.0,
        sample_count=0,
        available_signals={str(spec["key"]): 0 for spec in DISPLAY_SIGNAL_SPECS},
        signal_units={str(spec["key"]): str(spec["unit"]) for spec in DISPLAY_SIGNAL_SPECS},
    )
    return LoadedTelemetrySession(metadata=metadata, canonical_session=None, samples=[], elapsed_s=[])


def _canonical_session_from_json_payload(path: Path) -> CanonicalTelemetrySession:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "canonical_session" in payload:
        payload = payload["canonical_session"]
    if not isinstance(payload, dict):
        raise ValueError("canonical JSON session must be an object")
    return CanonicalTelemetrySession.model_validate(payload)


def _canonical_samples_jsonl_session(
    path: Path,
    *,
    metadata_path: Path | None = None,
    session_id: str | None = None,
) -> tuple[CanonicalTelemetrySession, str | None]:
    records = _read_jsonl_records(path)
    samples = [TelemetrySample.model_validate(record) for record in records]
    metadata: dict[str, Any] = {}
    capture_time: str | None = None
    if metadata_path is not None and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        capture_time = metadata.get("created_at") or metadata.get("started_at") or metadata.get("uploaded_at")
    sampling = SamplingMetadata.model_validate(metadata.get("sampling") or {})
    session = CanonicalTelemetrySession(
        session_id=metadata.get("session_id") or session_id,
        vehicle_id=metadata.get("vehicle_id"),
        device_id=metadata.get("device_id"),
        firmware_version=metadata.get("firmware_version"),
        session_note=metadata.get("session_note"),
        ecu_profile_id=metadata.get("ecu_profile_id"),
        decoder_id=metadata.get("decoder_id") or "canonical_samples_jsonl",
        decoder_version=metadata.get("decoder_version") or "unknown",
        telemetry_schema_version=metadata.get("telemetry_schema_version") or CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
        sampling=sampling,
        samples=samples,
        signal_definitions=metadata.get("signal_definitions") or V2_SIGNAL_DEFINITIONS.copy(),
        source_type=metadata.get("source_type") or "canonical_samples_jsonl",
    )
    return session, capture_time


def discover_sources(input_dir: Path) -> list[SessionSource]:
    if not input_dir.exists():
        return []
    sources: list[SessionSource] = []
    for path in sorted(input_dir.iterdir(), key=lambda item: item.name.lower()):
        if path.name.startswith("."):
            continue
        if path.is_dir():
            source_id = source_id_for_path(path, input_dir)
            sources.append(
                SessionSource(
                    source_id=source_id,
                    filename=path.relative_to(input_dir).as_posix() + "/",
                    path=str(path),
                    sha256=source_tree_sha256(path),
                    is_directory=True,
                )
            )
            continue
        if path.is_file():
            source_id = source_id_for_path(path, input_dir)
            sources.append(
                SessionSource(
                    source_id=source_id,
                    filename=path.relative_to(input_dir).as_posix(),
                    path=str(path),
                    sha256=source_tree_sha256(path),
                    is_directory=False,
                )
            )
    return sources


def load_source(source: SessionSource, settings: Settings | None = None) -> LoadedTelemetrySession:
    settings = settings or Settings()
    path = Path(source.path)
    try:
        if not path.exists():
            return _error_loaded(source=source, status="source_missing", error=f"source path is missing: {source.path}")
        if source.expected_sha256 is not None and source.sha256 != source.expected_sha256:
            return _error_loaded(
                source=source,
                status="source_hash_mismatch",
                error=(
                    "source SHA mismatch: expected "
                    f"{source.expected_sha256}, found {source.sha256 or 'missing'}"
                ),
            )
        if source.is_directory:
            samples_path = path / "samples.jsonl"
            metadata_path = path / "metadata.json"
            if not samples_path.exists():
                return _error_loaded(source=source, status="unsupported", error="directory does not contain samples.jsonl")
            session, capture_time = _canonical_samples_jsonl_session(
                samples_path,
                metadata_path=metadata_path,
                session_id=path.name,
            )
            return _loaded_from_session(source=source, session=session, capture_time=capture_time)

        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_FILE_SUFFIXES:
            return _error_loaded(source=source, status="unsupported", error=f"unsupported file type: {suffix or '(none)'}")

        if suffix == ".json":
            session = _canonical_session_from_json_payload(path)
            return _loaded_from_session(source=source, session=session, source_format="canonical_json")

        if suffix == ".jsonl":
            first_record = _first_jsonl_record(path)
            if first_record is not None and "raw_hex" not in first_record:
                session, capture_time = _canonical_samples_jsonl_session(path, session_id=source.source_id)
                return _loaded_from_session(source=source, session=session, capture_time=capture_time)

            native_result = import_native_jsonl_file(path, session_id=source.source_id)
            raw_result = None
            if (
                native_result.statistics.get("frames_found", 0) > 0
                and native_result.statistics.get("frames_parsed", 0) == 0
            ):
                raw_result = import_raw_file(path, settings, session_id=source.source_id)
            result = raw_result if raw_result and raw_result.statistics.get("frames_parsed", 0) > 0 else native_result
            if result.statistics.get("frames_found", 0) == 0:
                return _error_loaded(source=source, status="parse_error", error="no JSONL records found")
            if result.statistics.get("frames_parsed", 0) == 0:
                return _error_loaded(source=source, status="parse_error", error="no parseable ECU frames found")
            return _loaded_from_session(
                source=source,
                session=result.canonical_session,
                source_format=str(result.statistics.get("source_format") or result.canonical_session.source_type),
            )

        raw_result = import_raw_file(path, settings, session_id=source.source_id)
        if raw_result.statistics.get("frames_found", 0) == 0:
            return _error_loaded(source=source, status="parse_error", error="no RAW ECU frames found")
        if raw_result.statistics.get("frames_parsed", 0) == 0:
            return _error_loaded(source=source, status="parse_error", error="no parseable RAW ECU frames found")
        return _loaded_from_session(
            source=source,
            session=raw_result.canonical_session,
            source_format=str(raw_result.statistics.get("source_format") or raw_result.canonical_session.source_type),
        )
    except (OSError, JSONDecodeError, ValidationError, ValueError) as exc:
        return _error_loaded(source=source, status="parse_error", error=str(exc))
