from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import MIN_SESSION_WINDOWS_FOR_STATUS
from app.domain.model import FEATURE_SCHEMA_VERSION
from app.ml.artifacts import ModelBundle, load_model_bundle
from app.ml.harness import DetectorContext, DetectorResult, DetectorStatus, ModelHarness
from app.ml.iforest_detector import (
    ISOLATION_FOREST_DETECTOR_ID,
    IsolationForestDetector,
    isolation_forest_identity,
)
from app.ml.scoring import status_from_scored_metrics
from app.ml.training import model_artifacts_exist, model_paths


@dataclass(slots=True)
class InferenceOutput:
    model_loaded: bool
    windows: pd.DataFrame
    anomaly_ratio: float
    anomaly_windows: int
    health_score: float | None
    overall_status: str
    most_unusual_features: list[str]
    note: str
    evidence_window_count: int
    minimum_windows_for_status: int
    evidence_sufficient: bool
    model_metadata: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    detector_results: list[dict[str, Any]] = field(default_factory=list)


def _detector_result(
    *,
    model_metadata: dict[str, Any] | None,
    status: DetectorStatus,
    reason: str,
    feature_frame: pd.DataFrame,
    enabled: bool = True,
    warnings: list[str] | None = None,
) -> DetectorResult:
    resolved_metadata = model_metadata or {}
    return DetectorResult(
        identity=isolation_forest_identity(resolved_metadata),
        status=status,
        enabled=enabled,
        reason=reason,
        required_features=tuple(resolved_metadata.get("feature_names", [])),
        windows=feature_frame.copy(),
        warnings=warnings or [],
    )


def _validate_model_compatibility(
    metadata: dict[str, Any],
    *,
    expected_telemetry_schema_version: str | None,
    expected_signal_columns: list[str] | None,
) -> None:
    model_feature_schema = metadata.get("feature_schema_version", FEATURE_SCHEMA_VERSION)
    if model_feature_schema != FEATURE_SCHEMA_VERSION:
        raise ValueError(
            f"feature schema mismatch: model expects {model_feature_schema!r}, "
            f"runtime provides {FEATURE_SCHEMA_VERSION!r}"
        )
    model_telemetry_schema = metadata.get("telemetry_schema_version")
    if (
        expected_telemetry_schema_version is not None
        and model_telemetry_schema is not None
        and model_telemetry_schema != expected_telemetry_schema_version
    ):
        raise ValueError(
            f"telemetry schema mismatch: model expects {model_telemetry_schema!r}, "
            f"runtime provides {expected_telemetry_schema_version!r}"
        )
    model_signal_columns = metadata.get("signal_columns")
    if expected_signal_columns is not None and model_signal_columns is not None:
        if list(model_signal_columns) != list(expected_signal_columns):
            raise ValueError(
                f"signal column mismatch: model expects {model_signal_columns!r}, "
                f"runtime provides {expected_signal_columns!r}"
            )


def run_inference(
    feature_frame: pd.DataFrame,
    model_dir: str | Path,
    *,
    expected_telemetry_schema_version: str | None = None,
    expected_signal_columns: list[str] | None = None,
    minimum_windows_for_status: int = MIN_SESSION_WINDOWS_FOR_STATUS,
) -> InferenceOutput:
    evidence_window_count = int(len(feature_frame))
    evidence_sufficient = evidence_window_count >= minimum_windows_for_status
    if feature_frame.empty:
        model_metadata = None
        model_loaded = model_artifacts_exist(model_dir)
        if model_loaded:
            model_metadata = json.loads(model_paths(model_dir)["metadata"].read_text(encoding="utf-8"))
        detector_result = _detector_result(
            model_metadata=model_metadata,
            status="skipped",
            reason="no_windows",
            feature_frame=feature_frame,
        )
        return InferenceOutput(
            model_loaded=model_loaded,
            windows=feature_frame.copy(),
            anomaly_ratio=0.0,
            anomaly_windows=0,
            health_score=None,
            overall_status="no_windows",
            most_unusual_features=[],
            note="No windows were generated for inference.",
            evidence_window_count=evidence_window_count,
            minimum_windows_for_status=minimum_windows_for_status,
            evidence_sufficient=evidence_sufficient,
            model_metadata=model_metadata,
            detector_results=[detector_result.to_summary()],
        )

    if not model_artifacts_exist(model_dir):
        detector_result = _detector_result(
            model_metadata=None,
            status="skipped",
            reason="model_unavailable",
            feature_frame=feature_frame,
        )
        return InferenceOutput(
            model_loaded=False,
            windows=feature_frame.copy(),
            anomaly_ratio=0.0,
            anomaly_windows=0,
            health_score=None,
            overall_status="model_unavailable",
            most_unusual_features=[],
            note="No trained model was available; run the CLI training script first.",
            evidence_window_count=evidence_window_count,
            minimum_windows_for_status=minimum_windows_for_status,
            evidence_sufficient=evidence_sufficient,
            detector_results=[detector_result.to_summary()],
        )

    bundle = load_model_bundle(model_dir)
    _validate_model_compatibility(
        bundle.metadata,
        expected_telemetry_schema_version=expected_telemetry_schema_version,
        expected_signal_columns=expected_signal_columns,
    )

    harness = ModelHarness([IsolationForestDetector(bundle)])
    harness_result = harness.run(
        feature_frame,
        DetectorContext(
            telemetry_schema_version=expected_telemetry_schema_version,
            signal_columns=tuple(expected_signal_columns) if expected_signal_columns is not None else None,
            minimum_windows_for_status=minimum_windows_for_status,
        ),
    )
    primary = harness_result.result_for(ISOLATION_FOREST_DETECTOR_ID)
    if primary is None:
        raise RuntimeError("Isolation Forest detector was not registered")
    if primary.status == "skipped" and primary.reason == "missing_features":
        raise ValueError(f"inference feature frame is missing model features: {list(primary.missing_features)}")
    if primary.status != "ok":
        windows = feature_frame.copy()
        warning = f"detector {primary.identity.detector_id!r} did not produce a prediction: {primary.reason}"
        if primary.error_message:
            warning = f"{warning}: {primary.error_message}"
        return InferenceOutput(
            model_loaded=True,
            windows=windows,
            anomaly_ratio=0.0,
            anomaly_windows=0,
            health_score=None,
            overall_status="model_unavailable",
            most_unusual_features=[],
            note=warning,
            evidence_window_count=evidence_window_count,
            minimum_windows_for_status=minimum_windows_for_status,
            evidence_sufficient=evidence_sufficient,
            model_metadata=bundle.metadata,
            warnings=[warning],
            detector_results=harness_result.summaries(),
        )

    windows = primary.windows.copy()
    return InferenceOutput(
        model_loaded=True,
        windows=windows,
        anomaly_ratio=primary.anomaly_ratio or 0.0,
        anomaly_windows=primary.anomaly_window_count or 0,
        health_score=primary.metadata.get("health_score"),
        overall_status=primary.metadata.get("overall_status", "model_unavailable"),
        most_unusual_features=primary.metadata.get("most_unusual_features", []),
        note=primary.metadata.get("note", ""),
        evidence_window_count=primary.scored_window_count,
        minimum_windows_for_status=minimum_windows_for_status,
        evidence_sufficient=primary.scored_window_count >= minimum_windows_for_status,
        model_metadata=bundle.metadata,
        detector_results=harness_result.summaries(),
    )
