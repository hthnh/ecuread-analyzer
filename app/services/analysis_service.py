from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings
from app.domain.analysis import AnalysisServiceOutput
from app.domain.model import FEATURE_SCHEMA_VERSION
from app.domain.telemetry import CanonicalTelemetrySession, get_required_signal_columns
from app.integration.diagnostic_evidence import build_diagnostic_evidence
from app.ml.features import extract_window_features
from app.ml.inference import InferenceOutput, run_inference
from app.ml.validation import MLInputValidationError, validate_canonical_session_for_ml
from app.ml.windowing import create_windows
from app.repositories.file_repository import FileBackedRepository
from app.utils.ids import new_analysis_run_id, new_session_id


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


class AnalysisService:
    def __init__(
        self,
        settings: Settings,
        repository: FileBackedRepository | None = None,
        *,
        model_dir: str | Path | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.model_dir = Path(model_dir or settings.model_dir)

    def analyze(
        self,
        canonical_session: CanonicalTelemetrySession,
        *,
        process_with_model: bool = True,
        persist: bool = True,
        allow_empty_analysis: bool = False,
    ) -> AnalysisServiceOutput:
        started_at = datetime.now(UTC).isoformat()
        session = canonical_session
        if session.session_id is None:
            session = session.with_session_id(new_session_id())

        signal_columns = get_required_signal_columns(session.telemetry_schema_version)
        validation_warnings: list[str] = []
        try:
            validate_canonical_session_for_ml(session, self.settings, feature_schema_version=FEATURE_SCHEMA_VERSION)
        except MLInputValidationError as exc:
            if not allow_empty_analysis or not _insufficient_data_errors_only(exc.errors):
                raise
            validation_warnings = exc.errors
        frame_data = canonical_session_to_frame_data(session, signal_columns)
        feature_frame = build_feature_frame(frame_data, self.settings, signal_columns)
        if process_with_model:
            inference = run_inference(
                feature_frame,
                self.model_dir,
                expected_telemetry_schema_version=session.telemetry_schema_version,
                expected_signal_columns=signal_columns,
                minimum_windows_for_status=self.settings.min_session_windows_for_status,
            )
        else:
            inference = _empty_inference(
                feature_frame,
                "not_scored",
                "process_with_model=false; inference was skipped.",
                self.settings.min_session_windows_for_status,
            )

        warnings = [*validation_warnings, *inference.warnings]
        model_metadata = inference.model_metadata or {}
        training_decoder_versions = model_metadata.get("training_decoder_versions")
        if inference.model_loaded:
            if not training_decoder_versions:
                warnings.append("model metadata does not record training decoder versions; decoder compatibility is unknown")
            elif session.decoder_version_key not in training_decoder_versions:
                warnings.append(
                    f"telemetry decoder {session.decoder_version_key!r} was not present in model training provenance"
                )

        analysis_run_id = new_analysis_run_id()
        model_version_id = model_metadata.get("model_version_id") or model_metadata.get("version")
        diagnostic_evidence = build_diagnostic_evidence(
            analysis_run_id=analysis_run_id,
            session=session,
            model_version=model_version_id,
            telemetry_schema_version=session.telemetry_schema_version,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            signal_columns=signal_columns,
            detector_results=inference.detector_results,
            warnings=warnings,
        )
        summary = {
            "analysis_run_id": analysis_run_id,
            "session_id": session.session_id,
            "model_version": model_version_id,
            "telemetry_schema_version": session.telemetry_schema_version,
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
            "diagnostic_evidence": diagnostic_evidence,
            "warnings": warnings,
            "note": inference.note,
        }

        if persist and self.repository is not None:
            completed_at = datetime.now(UTC).isoformat()
            valid_frame_count = sum(1 for sample in session.samples if sample.frame_valid)
            checksum_error_count = sum(1 for sample in session.samples if not sample.checksum_valid)
            self.repository.upsert_decoder_version(session)
            self.repository.upsert_session(
                session,
                processing_status="completed",
                raw_frame_count=(
                    len(session.samples)
                    if session.source_type in {"raw_compatibility_adapter", "raw_decode_analyze"}
                    else 0
                ),
                valid_frame_count=valid_frame_count,
                invalid_frame_count=len(session.samples) - valid_frame_count,
                checksum_error_count=checksum_error_count,
            )
            self.repository.write_telemetry(session)
            if inference.model_loaded and model_metadata:
                self.repository.write_model_version(model_metadata)
            analysis_run = {
                "id": analysis_run_id,
                "session_id": session.session_id,
                "model_version_id": model_version_id,
                "telemetry_schema_version": session.telemetry_schema_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "signal_columns": signal_columns,
                "status": "completed",
                "window_size_samples": self.settings.window_size_samples,
                "window_step_samples": self.settings.window_step_samples,
                "anomaly_window_count": inference.anomaly_windows,
                "total_window_count": int(len(feature_frame)),
                "anomaly_ratio": inference.anomaly_ratio,
                "health_score": inference.health_score,
                "overall_status": inference.overall_status,
                "evidence_window_count": inference.evidence_window_count,
                "minimum_windows_for_status": inference.minimum_windows_for_status,
                "evidence_sufficient": inference.evidence_sufficient,
                "detectors": inference.detector_results,
                "diagnostic_evidence": diagnostic_evidence,
                "warnings": warnings,
                "started_at": started_at,
                "completed_at": completed_at,
                "created_at": started_at,
            }
            self.repository.write_analysis_run(analysis_run)
            self.repository.write_analysis_windows(analysis_run_id, inference.windows)

        return AnalysisServiceOutput(
            analysis_run_id=analysis_run_id,
            session_id=session.session_id or "",
            frame_data=frame_data,
            feature_frame=feature_frame,
            windows=inference.windows,
            summary=summary,
            warnings=warnings,
        )
