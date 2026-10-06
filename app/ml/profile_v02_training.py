from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.audit.loading import parse_raw_hex
from app.config import Settings
from app.ecu.profiles.honda_keihin_71_17 import (
    PROFILE_ID_V02,
    PRODUCTION_DECODER_ID,
    PRODUCTION_DECODER_VERSION,
    PRODUCTION_ECU_PROFILE_ID,
    PRODUCTION_SIGNAL_COLUMNS,
    FrameFormatError,
    bytes_to_hex,
    decode_production_values,
    normalize_frame,
    validate_normalized_frame,
)
from app.domain.telemetry import CANONICAL_TELEMETRY_SCHEMA_VERSION_V2
from app.ml.features import extract_window_features, get_feature_names
from app.ml.training import train_isolation_forest
from app.ml.windowing import create_windows


V02_DECODER_ID = PRODUCTION_DECODER_ID
V02_DECODER_VERSION = PRODUCTION_DECODER_VERSION
V02_TPS_CLOSED_RAW = 0.0
V02_TPS_OPEN_RAW = 156.0
V02_ML_SIGNAL_COLUMNS = PRODUCTION_SIGNAL_COLUMNS


@dataclass(slots=True)
class ProfileV02SessionData:
    source_file: Path
    frame_data: pd.DataFrame
    quality: dict[str, Any]


def discover_jsonl_files(input_dir: Path | None, input_paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    if input_dir is not None:
        if not input_dir.exists():
            raise ValueError(f"input directory does not exist: {input_dir}")
        files.extend(sorted(path for path in input_dir.rglob("*.jsonl") if path.is_file()))
    files.extend(input_paths)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _numeric(value: Any) -> float | None:
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return float(value)
    return None


def _tps_percent_calibrated(raw: int) -> float:
    percent = (raw - V02_TPS_CLOSED_RAW) / (V02_TPS_OPEN_RAW - V02_TPS_CLOSED_RAW) * 100.0
    return max(0.0, min(100.0, percent))


def decode_v02_ml_fields(frame: list[int]) -> dict[str, float]:
    decoded = decode_production_values(frame, strict_checksum=False, allow_legacy_29_byte=False)
    decoded["tps_raw_candidate"] = decoded["tps_raw"]
    return decoded


def _fields_physically_valid(fields: dict[str, float]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not 0 <= fields["rpm"] <= 16000:
        errors.append("rpm outside 0-16000")
    if not 0.0 <= fields["tps_voltage"] <= 5.0:
        errors.append("tps_voltage outside 0-5 V")
    tps_raw = fields.get("tps_raw", fields.get("tps_raw_candidate"))
    if not isinstance(tps_raw, int | float) or not 0 <= tps_raw <= 255:
        errors.append("tps_raw outside 0-255")
    if not -40 <= fields["iat_c"] <= 150:
        errors.append("iat_c outside -40..150 C")
    if not -40 <= fields["ect_c"] <= 180:
        errors.append("ect_c outside -40..180 C")
    if not 6.0 <= fields["battery_voltage"] <= 18.0:
        errors.append("battery_voltage outside 6-18 V")
    return not errors, errors


def _relative_time(row: dict[str, Any], first_device_time_ms: float | None) -> tuple[float | None, str]:
    elapsed = _numeric(row.get("elapsed_ms"))
    if elapsed is not None:
        return elapsed, "elapsed_ms"
    host_monotonic = _numeric(row.get("host_monotonic_ms"))
    if host_monotonic is not None:
        return host_monotonic, "host_monotonic_ms"
    device_time = _numeric(row.get("device_time_ms"))
    if device_time is not None:
        if first_device_time_ms is None:
            return 0.0, "device_time_ms_relative"
        return device_time - first_device_time_ms, "device_time_ms_relative"
    return None, "sample_index"


def load_profile_v02_jsonl_file(
    path: Path,
    *,
    strict_checksum: bool = True,
    allow_legacy_29_byte: bool = False,
    max_records: int | None = None,
) -> ProfileV02SessionData:
    rows: list[dict[str, Any]] = []
    malformed_records = 0
    malformed_raw_records = 0
    json_records = 0
    first_device_time_ms: float | None = None
    records_seen = 0

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            if max_records is not None and records_seen >= max_records:
                break
            if not line.strip():
                continue
            records_seen += 1
            row: dict[str, Any]
            try:
                row = json.loads(line)
                json_records += 1
            except json.JSONDecodeError:
                malformed_records += 1
                continue
            device_time = _numeric(row.get("device_time_ms"))
            if first_device_time_ms is None and device_time is not None:
                first_device_time_ms = device_time
            try:
                raw_bytes = parse_raw_hex(str(row.get("raw_hex", "")))
                frame, source_format = normalize_frame(raw_bytes, allow_legacy_29_byte=allow_legacy_29_byte)
            except (ValueError, FrameFormatError):
                malformed_raw_records += 1
                rows.append(
                    {
                        "frame_index": len(rows),
                        "line_number": line_number,
                        "relative_time_ms": None,
                        "time_source": "none",
                        "raw_hex": row.get("raw_hex", ""),
                        "normalized_hex": "",
                        "source_format": "",
                        "checksum_valid": False,
                        "is_valid": False,
                        "ml_eligible": False,
                        "validation_errors": "malformed raw frame",
                    }
                )
                continue

            validation = validate_normalized_frame(frame, source_format=source_format)
            relative_time_ms, time_source = _relative_time(row, first_device_time_ms)
            decoded_fields: dict[str, float | None] = {column: None for column in V02_ML_SIGNAL_COLUMNS}
            extra_fields: dict[str, float | None] = {
                "tps_raw_candidate": None,
                "tps_percent_calibrated": None,
                "injector_raw": None,
                "byte17": None,
                "byte18": None,
            }
            physical_valid = False
            physical_errors: list[str] = []
            if validation.length_valid and validation.header_valid and (validation.checksum_valid or not strict_checksum):
                decoded = decode_v02_ml_fields(frame)
                physical_valid, physical_errors = _fields_physically_valid(decoded)
                decoded_fields = {column: decoded[column] for column in V02_ML_SIGNAL_COLUMNS}
                extra_fields = {column: decoded[column] for column in extra_fields}

            is_valid = bool(validation.parse_valid and physical_valid)
            errors = list(validation.errors)
            errors.extend(physical_errors)
            rows.append(
                {
                    "frame_index": len(rows),
                    "line_number": line_number,
                    "source_file": str(path),
                    "seq": row.get("seq", row.get("capture_seq")),
                    "session_hash": row.get("session_hash"),
                    "relative_time_ms": relative_time_ms,
                    "time_source": time_source,
                    "raw_hex": row.get("raw_hex", ""),
                    "raw_length": len(raw_bytes),
                    "normalized_hex": bytes_to_hex(frame),
                    "source_format": source_format,
                    "parse_valid": validation.parse_valid,
                    "length_valid": validation.length_valid,
                    "header_valid": validation.header_valid,
                    "checksum_valid": validation.checksum_valid,
                    "profile_supported": validation.profile_supported,
                    "is_valid": is_valid,
                    "ml_eligible": is_valid,
                    "validation_errors": "; ".join(errors),
                    **decoded_fields,
                    **extra_fields,
                }
            )

    frame_data = pd.DataFrame(rows)
    if frame_data.empty:
        quality = {
            "source_file": str(path),
            "records_seen": records_seen,
            "records_parsed": json_records,
            "valid_frames": 0,
            "ml_eligible_frames": 0,
            "checksum_failures": 0,
            "header_failures": 0,
            "length_failures": 0,
            "malformed_records": malformed_records,
            "malformed_raw_records": malformed_raw_records,
            "time_regressions": 0,
            "duplicate_frames": 0,
        }
        return ProfileV02SessionData(path, frame_data, quality)

    checksum_failures = int(
        ((frame_data["length_valid"] == True) & (frame_data["header_valid"] == True) & (frame_data["checksum_valid"] == False)).sum()
    )
    normalized_hexes = [value for value in frame_data["normalized_hex"].dropna().tolist() if value]
    relative_times = [
        float(value)
        for value in frame_data["relative_time_ms"].dropna().tolist()
        if isinstance(value, int | float) or str(value).replace(".", "", 1).isdigit()
    ]
    quality = {
        "source_file": str(path),
        "records_seen": records_seen,
        "records_parsed": json_records,
        "valid_frames": int(frame_data["parse_valid"].fillna(False).astype(bool).sum()),
        "ml_eligible_frames": int(frame_data["ml_eligible"].fillna(False).astype(bool).sum()),
        "checksum_failures": checksum_failures,
        "checksum_failure_ratio": checksum_failures / max(1, len(frame_data)),
        "header_failures": int(((frame_data["length_valid"] == True) & (frame_data["header_valid"] == False)).sum()),
        "length_failures": int((frame_data["length_valid"] == False).sum()),
        "malformed_records": malformed_records,
        "malformed_raw_records": malformed_raw_records,
        "time_regressions": sum(1 for left, right in zip(relative_times, relative_times[1:], strict=False) if right < left),
        "duplicate_frames": sum(count - 1 for count in Counter(normalized_hexes).values() if count > 1),
        "duration_ms": (relative_times[-1] - relative_times[0]) if len(relative_times) >= 2 else None,
        "time_source": ";".join(sorted(set(str(value) for value in frame_data["time_source"].dropna().tolist()))),
    }
    return ProfileV02SessionData(path, frame_data, quality)


def _write_csv(path: Path, dataframe: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dataframe.to_csv(path, index=False)


def _write_rows_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _summarize_signal_ranges(frame_data: pd.DataFrame) -> dict[str, dict[str, float | int | None]]:
    summary: dict[str, dict[str, float | int | None]] = {}
    for column in [*V02_ML_SIGNAL_COLUMNS, "injector_raw", "byte17", "byte18"]:
        if column not in frame_data.columns:
            continue
        series = pd.to_numeric(frame_data[column], errors="coerce").dropna()
        if series.empty:
            summary[column] = {"count": 0, "min": None, "median": None, "p95": None, "max": None}
        else:
            summary[column] = {
                "count": int(series.count()),
                "min": float(series.min()),
                "median": float(series.median()),
                "p95": float(series.quantile(0.95)),
                "max": float(series.max()),
            }
    return summary


def _training_report_markdown(report: dict[str, Any]) -> str:
    quality_lines = "\n".join(
        f"- `{Path(row['source_file']).name}`: {row['records_seen']} records, "
        f"{row['valid_frames']} valid, {row['ml_eligible_frames']} ML-eligible, "
        f"checksum failures {row['checksum_failures']}, time regressions {row['time_regressions']}"
        for row in report["data_quality"]
    )
    signals = ", ".join(f"`{column}`" for column in report["signal_columns"])
    return f"""# Pi Native Honda 0x17 V2 ML Training Report

## Input Quality

{quality_lines}

## Training Dataset

- Telemetry schema: `{report['telemetry_schema_version']}`
- Decoder: `{report['decoder_id']}:{report['decoder_version']}`
- Evidence profile: `{report['decoder_profile_id']}`
- Total records: {report['total_records']}
- Valid frames: {report['valid_frames']}
- ML-eligible frames: {report['ml_eligible_frames']}
- Windows generated: {report['windows_generated']}
- Feature count: {report['feature_count']}
- Signal columns: {signals}

## Model

- Model directory: `{report['model_dir']}`
- Model version: `{report['model_metadata']['model_version_id']}`
- Training score p05: {report['model_metadata']['training_score_p05']:.6f}
- Training decision median: {report['model_metadata']['training_decision_median']:.6f}

This model is an experimental unsupervised road-run baseline. It was trained without labels and without anomaly scoring the source sessions as faults.
"""


def train_profile_v02_from_jsonl_files(
    *,
    input_paths: list[Path],
    settings: Settings,
    model_dir: Path,
    output_dir: Path,
    strict_checksum: bool = True,
    allow_legacy_29_byte: bool = False,
    max_records: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not input_paths:
        raise ValueError("no JSONL input files provided for profile v0.2 training")

    session_data = [
        load_profile_v02_jsonl_file(
            path,
            strict_checksum=strict_checksum,
            allow_legacy_29_byte=allow_legacy_29_byte,
            max_records=max_records,
        )
        for path in input_paths
    ]
    data_quality = [item.quality for item in session_data]
    all_frames = pd.concat([item.frame_data for item in session_data], ignore_index=True)
    if all_frames.empty:
        raise ValueError("no frames were parsed from profile v0.2 input files")
    if int(all_frames["ml_eligible"].fillna(False).astype(bool).sum()) < settings.window_size_samples:
        raise ValueError("not enough ML-eligible profile v0.2 frames to train")

    feature_frames: list[pd.DataFrame] = []
    window_rows: list[pd.DataFrame] = []
    for item in session_data:
        frame_data = item.frame_data
        windows = create_windows(
            frame_data,
            window_size_samples=settings.window_size_samples,
            step_size_samples=settings.window_step_samples,
            max_checksum_error_ratio=settings.max_window_checksum_error_ratio,
            max_invalid_decoded_ratio=settings.max_window_invalid_decoded_ratio,
        )
        features = extract_window_features(frame_data, windows, signal_columns=V02_ML_SIGNAL_COLUMNS)
        features.insert(0, "source_file", str(item.source_file))
        feature_frames.append(features)
        if features.empty:
            continue
        window_rows.append(features[[column for column in features.columns if column in {"source_file", "window_index", "start_frame_index", "end_frame_index", "sample_count", "duration_ms"}]])

    training_features = pd.concat(feature_frames, ignore_index=True) if feature_frames else pd.DataFrame()
    if training_features.empty:
        raise ValueError("no profile v0.2 windows were generated for training")

    feature_only = training_features.drop(columns=["source_file"], errors="ignore")
    model_metadata = train_isolation_forest(
        feature_only,
        [path.name for path in input_paths],
        settings,
        model_dir=model_dir,
        notes=(
            "Pi/native Honda 0x17 V2 baseline. Uses selected production core fields only; "
            "does not use v0.1 MAP hypothesis, legacy ect_c_candidate, or vehicle-specific TPS percent as core."
        ),
        training_decoder_versions=[f"{V02_DECODER_ID}:{V02_DECODER_VERSION}"],
        signal_columns=V02_ML_SIGNAL_COLUMNS,
        extra_metadata={
            "telemetry_schema_version": CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
            "ecu_profile_id": PRODUCTION_ECU_PROFILE_ID,
            "decoder_id": PRODUCTION_DECODER_ID,
            "decoder_version": PRODUCTION_DECODER_VERSION,
            "decoder_profile_id": PROFILE_ID_V02,
            "tps_percent_calibration": {
                "closed_raw": V02_TPS_CLOSED_RAW,
                "open_raw": V02_TPS_OPEN_RAW,
                "formula": "(raw - closed_raw) / (open_raw - closed_raw) * 100, clamped to 0..100",
            },
            "ml_approved_fields": V02_ML_SIGNAL_COLUMNS,
            "excluded_fields": [
                "map_actual_location",
                "tps_percent_calibrated_as_required_core",
                "injector_ms",
                "byte17",
                "byte18_speed",
            ],
            "training_source": "real_data/real_run",
        },
    )

    report = {
        "telemetry_schema_version": CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
        "ecu_profile_id": PRODUCTION_ECU_PROFILE_ID,
        "decoder_id": PRODUCTION_DECODER_ID,
        "decoder_version": PRODUCTION_DECODER_VERSION,
        "decoder_profile_id": PROFILE_ID_V02,
        "model_dir": str(model_dir),
        "input_files": [str(path) for path in input_paths],
        "data_quality": data_quality,
        "total_records": int(sum(row["records_seen"] for row in data_quality)),
        "valid_frames": int(sum(row["valid_frames"] for row in data_quality)),
        "ml_eligible_frames": int(sum(row["ml_eligible_frames"] for row in data_quality)),
        "checksum_failures": int(sum(row["checksum_failures"] for row in data_quality)),
        "header_failures": int(sum(row["header_failures"] for row in data_quality)),
        "length_failures": int(sum(row["length_failures"] for row in data_quality)),
        "malformed_records": int(sum(row["malformed_records"] for row in data_quality)),
        "malformed_raw_records": int(sum(row["malformed_raw_records"] for row in data_quality)),
        "time_regressions": int(sum(row["time_regressions"] for row in data_quality)),
        "windows_generated": int(len(training_features)),
        "feature_count": len(get_feature_names(V02_ML_SIGNAL_COLUMNS)),
        "signal_columns": V02_ML_SIGNAL_COLUMNS,
        "signal_ranges": _summarize_signal_ranges(all_frames),
        "model_metadata": model_metadata,
        "artifacts": {
            "frames": str(output_dir / "frames_v0.2.csv"),
            "features": str(output_dir / "features_v0.2.csv"),
            "data_quality": str(output_dir / "data_quality_v0.2.csv"),
            "training_report_json": str(output_dir / "training_report_v0.2.json"),
            "training_report_md": str(output_dir / "training_report_v0.2.md"),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "frames_v0.2.csv", all_frames)
    _write_csv(output_dir / "features_v0.2.csv", training_features)
    _write_rows_csv(output_dir / "data_quality_v0.2.csv", data_quality)
    (output_dir / "training_report_v0.2.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output_dir / "training_report_v0.2.md").write_text(_training_report_markdown(report), encoding="utf-8")
    return model_metadata, report
