from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.config import Settings
from app.domain.model import FEATURE_SCHEMA_VERSION
from app.domain.telemetry import CanonicalTelemetrySession, get_required_signal_columns
from app.ingestion.raw_ecu.adapter import import_raw_file
from app.ml.training import train_isolation_forest
from app.ml.validation import validate_canonical_session_for_ml
from app.repositories.file_repository import FileBackedRepository
from app.services.analysis_service import build_feature_frame, canonical_session_to_frame_data


def train_from_canonical_sessions(
    canonical_sessions: list[CanonicalTelemetrySession],
    training_sessions: list[str],
    settings: Settings,
    *,
    repository: FileBackedRepository | None = None,
) -> dict:
    all_features: list[pd.DataFrame] = []
    decoder_versions: set[str] = set()
    telemetry_schema_version = canonical_sessions[0].telemetry_schema_version if canonical_sessions else None
    signal_columns = get_required_signal_columns(telemetry_schema_version) if telemetry_schema_version is not None else None
    for session in canonical_sessions:
        if session.telemetry_schema_version != telemetry_schema_version:
            raise ValueError("cannot train a single model from mixed telemetry schema versions")
        validate_canonical_session_for_ml(session, settings, feature_schema_version=FEATURE_SCHEMA_VERSION)
        decoder_versions.add(session.decoder_version_key)
        all_features.append(
            build_feature_frame(
                canonical_session_to_frame_data(session, signal_columns),
                settings,
                signal_columns,
            )
        )

    feature_frame = pd.concat(all_features, ignore_index=True) if all_features else pd.DataFrame()
    metadata = train_isolation_forest(
        feature_frame,
        training_sessions,
        settings,
        training_decoder_versions=sorted(decoder_versions),
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        signal_columns=signal_columns,
        extra_metadata={"telemetry_schema_version": telemetry_schema_version},
    )
    if repository is not None:
        repository.write_model_version(metadata)
    return metadata


def train_from_raw_files(
    input_paths: list[str | Path],
    settings: Settings,
    *,
    sample_interval_ms: float | None = None,
    sampling_rate_hz: float | None = None,
    repository: FileBackedRepository | None = None,
) -> tuple[dict, dict[str, int]]:
    canonical_sessions: list[CanonicalTelemetrySession] = []
    training_names: list[str] = []
    report = {
        "frames_found": 0,
        "valid_checksum_frames": 0,
        "decoded_valid_frames": 0,
    }
    for input_path in input_paths:
        path = Path(input_path)
        raw_import = import_raw_file(
            path,
            settings,
            session_id=f"training_{path.stem}",
            sample_interval_ms=sample_interval_ms,
            sampling_rate_hz=sampling_rate_hz,
        )
        if raw_import.statistics["frames_found"] == 0:
            raise ValueError(f"No RAW frames found in {path}")
        canonical_sessions.append(raw_import.canonical_session)
        training_names.append(path.name)
        report["frames_found"] += raw_import.statistics["frames_found"]
        report["valid_checksum_frames"] += raw_import.statistics["checksum_valid_frames"]
        report["decoded_valid_frames"] += int(raw_import.frame_data["ml_eligible"].fillna(False).sum())

    metadata = train_from_canonical_sessions(
        canonical_sessions,
        training_names,
        settings,
        repository=repository,
    )
    report["windows_generated"] = int(metadata["training_window_count"])
    report["feature_count"] = len(metadata["feature_names"])
    return metadata, report
