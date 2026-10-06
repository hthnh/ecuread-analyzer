from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from app.config import MIN_SESSION_WINDOWS_FOR_STATUS, Settings
from app.domain.model import FEATURE_SCHEMA_VERSION
from app.domain.telemetry import CanonicalTelemetrySession, get_required_signal_columns
from app.ml.features import extract_window_features
from app.ml.inference import InferenceOutput, run_inference
from app.ml.validation import MLInputValidationError, validate_canonical_session_for_ml
from app.ml.windowing import create_windows


def canonical_session_to_frame_data(
    session: CanonicalTelemetrySession,
    signal_columns: list[str] | None = None,
) -> pd.DataFrame:
    columns_for_schema = signal_columns or get_required_signal_columns(session.telemetry_schema_version)
    rows: list[dict[str, Any]] = []
    for sample in session.samples:
        row: dict[str, Any] = {
            "frame_index": sample.sequence,
            "sequence": sample.sequence,
            "relative_time_ms": sample.timestamp_ms,
            "checksum_valid": sample.checksum_valid,
            "is_valid": sample.frame_valid,
            "ml_eligible": bool(sample.frame_valid and sample.checksum_valid),
        }
        for column in columns_for_schema:
            row[column] = getattr(sample, column)
        rows.append(row)

    columns = [
        "frame_index",
        "sequence",
        "relative_time_ms",
        "checksum_valid",
        "is_valid",
        "ml_eligible",
        *columns_for_schema,
    ]
    return pd.DataFrame(rows, columns=columns)


def build_feature_frame(
    frame_data: pd.DataFrame,
    settings: Settings,
    signal_columns: list[str] | None = None,
) -> pd.DataFrame:
    windows = create_windows(
        frame_data,
        window_size_samples=settings.window_size_samples,
        step_size_samples=settings.window_step_samples,
        max_checksum_error_ratio=settings.max_window_checksum_error_ratio,
        max_invalid_decoded_ratio=settings.max_window_invalid_decoded_ratio,
    )
    return extract_window_features(frame_data, windows, signal_columns=signal_columns)


def _empty_inference(
    feature_frame: pd.DataFrame,
    status: str,
    note: str,
    minimum_windows_for_status: int,
) -> InferenceOutput:
    evidence_window_count = int(len(feature_frame))
    return InferenceOutput(
        model_loaded=False,
        windows=feature_frame.copy(),
        anomaly_ratio=0.0,
        anomaly_windows=0,
        health_score=None,
        overall_status=status,
        most_unusual_features=[],
        note=note,
        evidence_window_count=evidence_window_count,
        minimum_windows_for_status=minimum_windows_for_status,
        evidence_sufficient=evidence_window_count >= minimum_windows_for_status,
    )


def _insufficient_data_errors_only(errors: list[str]) -> bool:
    return bool(errors) and all(
        error.startswith("insufficient samples") or error.startswith("insufficient ML-eligible samples")
        for error in errors
    )


def analyze_canonical_session(
    canonical_session: CanonicalTelemetrySession,
    *,
    settings: Settings | None = None,
    model_dir: str | Path,
    process_with_model: bool = True,
    allow_empty_analysis: bool = False,
) -> dict[str, Any]:
    settings = settings or Settings()
    signal_columns = get_required_signal_columns(canonical_session.telemetry_schema_version)
    validation_warnings: list[str] = []
    try:
        validate_canonical_session_for_ml(
            canonical_session,
            settings,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
        )
    except MLInputValidationError as exc:
        if not allow_empty_analysis or not _insufficient_data_errors_only(exc.errors):
            raise
        validation_warnings = exc.errors

    frame_data = canonical_session_to_frame_data(canonical_session, signal_columns)
    feature_frame = build_feature_frame(frame_data, settings, signal_columns)
    if process_with_model:
        inference = run_inference(
            feature_frame,
            model_dir,
            expected_telemetry_schema_version=canonical_session.telemetry_schema_version,
            expected_signal_columns=signal_columns,
            minimum_windows_for_status=settings.min_session_windows_for_status,
        )
    else:
        inference = _empty_inference(
            feature_frame,
            "not_scored",
            "process_with_model=false; inference was skipped.",
            settings.min_session_windows_for_status,
        )

    warnings = [*validation_warnings, *inference.warnings]
    model_metadata = inference.model_metadata or {}
    training_decoder_versions = model_metadata.get("training_decoder_versions")
    if inference.model_loaded:
        if not training_decoder_versions:
            warnings.append("model metadata does not record training decoder versions; decoder compatibility is unknown")
        elif canonical_session.decoder_version_key not in training_decoder_versions:
            warnings.append(
                f"telemetry decoder {canonical_session.decoder_version_key!r} was not present in model training provenance"
            )

    model_version_id = model_metadata.get("model_version_id") or model_metadata.get("version")
    summary = {
        "model_version": model_version_id,
        "telemetry_schema_version": canonical_session.telemetry_schema_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "signal_columns": signal_columns,
        "window_count": int(len(feature_frame)),
        "anomaly_window_count": inference.anomaly_windows,
        "anomaly_ratio": inference.anomaly_ratio,
        "health_score": inference.health_score,
        "overall_status": inference.overall_status,
        "evidence_window_count": inference.evidence_window_count,
        "minimum_windows_for_status": inference.minimum_windows_for_status,
        "evidence_sufficient": inference.evidence_sufficient,
        "most_unusual_features": inference.most_unusual_features,
        "model_loaded": inference.model_loaded,
        "detectors": inference.detector_results,
        "warnings": warnings,
        "note": inference.note,
    }
    return {
        "session_id": canonical_session.session_id or "",
        "frame_data": frame_data,
        "feature_frame": feature_frame,
        "windows": inference.windows,
        "summary": summary,
        "warnings": warnings,
    }
