from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from app.config import MIN_SESSION_WINDOWS_FOR_STATUS
from app.domain.model import FEATURE_SCHEMA_VERSION
from app.ml.training import MODEL_FILENAME, SCALER_FILENAME, METADATA_FILENAME


EPSILON = 1e-9


@dataclass(slots=True)
class LegacyModelBundle:
    model: Any
    scaler: Any
    metadata: dict[str, Any]


@dataclass(slots=True)
class LegacyInferenceOutput:
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


def legacy_model_paths(model_dir: str | Path) -> dict[str, Path]:
    root = Path(model_dir)
    return {
        "model": root / MODEL_FILENAME,
        "scaler": root / SCALER_FILENAME,
        "metadata": root / METADATA_FILENAME,
    }


def legacy_model_artifacts_exist(model_dir: str | Path) -> bool:
    paths = legacy_model_paths(model_dir)
    return all(path.exists() for path in paths.values())


def legacy_load_model_bundle(model_dir: str | Path) -> LegacyModelBundle:
    paths = legacy_model_paths(model_dir)
    if not legacy_model_artifacts_exist(model_dir):
        raise FileNotFoundError(f"model artifacts are not complete in {model_dir}")
    metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    return LegacyModelBundle(
        model=joblib.load(paths["model"]),
        scaler=joblib.load(paths["scaler"]),
        metadata=metadata,
    )


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def legacy_window_health_from_score(score: float, metadata: dict[str, Any]) -> float:
    p01 = float(metadata.get("training_score_p01", score))
    p05 = float(metadata.get("training_score_p05", p01))
    median = float(metadata.get("training_score_median", p05))

    if score >= median:
        return 100.0
    if score >= p05:
        return _clamp(60.0 + 40.0 * (score - p05) / max(median - p05, EPSILON))
    if score >= p01:
        return _clamp(20.0 + 40.0 * (score - p01) / max(p05 - p01, EPSILON))

    lower_span = max(abs(p01), abs(p05 - p01), EPSILON)
    return _clamp(20.0 * (1.0 - (p01 - score) / lower_span))


def legacy_session_health_score(window_health_scores: list[float], anomaly_ratio: float) -> float | None:
    if not window_health_scores:
        return None
    scores = pd.Series(window_health_scores, dtype=float)
    median_health = float(scores.median())
    lower_decile_health = float(scores.quantile(0.10))
    anomaly_penalty_component = 100.0 * (1.0 - min(1.0, anomaly_ratio))
    health = 0.65 * median_health + 0.20 * lower_decile_health + 0.15 * anomaly_penalty_component
    return round(_clamp(health), 2)


def legacy_rank_unusual_features(feature_row: pd.Series, metadata: dict[str, Any], limit: int = 5) -> list[str]:
    medians = metadata.get("feature_medians", {})
    iqrs = metadata.get("feature_iqrs", {})
    ranked: list[tuple[str, float]] = []
    for feature_name, median in medians.items():
        if feature_name not in feature_row:
            continue
        iqr = max(float(iqrs.get(feature_name, 0.0)), EPSILON)
        robust_z = abs(float(feature_row[feature_name]) - float(median)) / iqr
        ranked.append((feature_name, robust_z))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return [name for name, value in ranked[:limit] if value > 0]


def legacy_session_most_unusual_features(window_feature_lists: list[list[str]], limit: int = 5) -> list[str]:
    counter: Counter[str] = Counter()
    for features in window_feature_lists:
        counter.update(features)
    return [feature for feature, _count in counter.most_common(limit)]


def legacy_status_from_scored_metrics(
    *,
    window_count: int,
    anomaly_ratio: float,
    health_score: float | None,
    minimum_windows_for_status: int = MIN_SESSION_WINDOWS_FOR_STATUS,
) -> str:
    if health_score is None:
        return "no_windows"
    if window_count < minimum_windows_for_status:
        return "limited_data"
    if anomaly_ratio >= 0.15 or health_score < 50:
        return "attention"
    if anomaly_ratio >= 0.02 or health_score < 80:
        return "monitor"
    return "ok"


def legacy_run_inference(
    feature_frame: pd.DataFrame,
    model_dir: str | Path,
    *,
    expected_telemetry_schema_version: str | None = None,
    expected_signal_columns: list[str] | None = None,
    minimum_windows_for_status: int = MIN_SESSION_WINDOWS_FOR_STATUS,
) -> LegacyInferenceOutput:
    evidence_window_count = int(len(feature_frame))
    evidence_sufficient = evidence_window_count >= minimum_windows_for_status
    if feature_frame.empty:
        model_metadata = None
        model_loaded = legacy_model_artifacts_exist(model_dir)
        if model_loaded:
            model_metadata = json.loads(legacy_model_paths(model_dir)["metadata"].read_text(encoding="utf-8"))
        return LegacyInferenceOutput(
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
        )

    if not legacy_model_artifacts_exist(model_dir):
        return LegacyInferenceOutput(
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
        )

    bundle = legacy_load_model_bundle(model_dir)
    model_feature_schema = bundle.metadata.get("feature_schema_version", FEATURE_SCHEMA_VERSION)
    if model_feature_schema != FEATURE_SCHEMA_VERSION:
        raise ValueError(
            f"feature schema mismatch: model expects {model_feature_schema!r}, "
            f"runtime provides {FEATURE_SCHEMA_VERSION!r}"
        )
    model_telemetry_schema = bundle.metadata.get("telemetry_schema_version")
    if (
        expected_telemetry_schema_version is not None
        and model_telemetry_schema is not None
        and model_telemetry_schema != expected_telemetry_schema_version
    ):
        raise ValueError(
            f"telemetry schema mismatch: model expects {model_telemetry_schema!r}, "
            f"runtime provides {expected_telemetry_schema_version!r}"
        )
    model_signal_columns = bundle.metadata.get("signal_columns")
    if expected_signal_columns is not None and model_signal_columns is not None:
        if list(model_signal_columns) != list(expected_signal_columns):
            raise ValueError(
                f"signal column mismatch: model expects {model_signal_columns!r}, "
                f"runtime provides {expected_signal_columns!r}"
            )
    feature_names = bundle.metadata.get("feature_names", [])
    missing = [feature for feature in feature_names if feature not in feature_frame.columns]
    if missing:
        raise ValueError(f"inference feature frame is missing model features: {missing}")

    x = feature_frame[feature_names].astype(float)
    x_scaled = bundle.scaler.transform(x)
    score_samples = bundle.model.score_samples(x_scaled)
    decision_scores = bundle.model.decision_function(x_scaled)
    predictions = bundle.model.predict(x_scaled)

    windows = feature_frame.copy()
    windows["score_sample"] = score_samples
    windows["decision_score"] = decision_scores
    windows["prediction"] = predictions
    windows["is_anomaly"] = predictions == -1
    windows["window_health_score"] = [
        legacy_window_health_from_score(float(score), bundle.metadata)
        for score in score_samples
    ]

    unusual_lists: list[list[str]] = []
    for _, row in windows.iterrows():
        unusual = legacy_rank_unusual_features(row, bundle.metadata, limit=5)
        unusual_lists.append(unusual)
    windows["most_unusual_features"] = [";".join(items) for items in unusual_lists]

    anomaly_windows = int((predictions == -1).sum())
    anomaly_ratio = anomaly_windows / max(1, len(windows))
    health = legacy_session_health_score(windows["window_health_score"].astype(float).tolist(), anomaly_ratio)
    overall_status = legacy_status_from_scored_metrics(
        window_count=len(windows),
        anomaly_ratio=anomaly_ratio,
        health_score=health,
        minimum_windows_for_status=minimum_windows_for_status,
    )

    anomaly_feature_lists = [
        items
        for items, prediction in zip(unusual_lists, predictions, strict=True)
        if prediction == -1
    ]
    most_unusual = legacy_session_most_unusual_features(anomaly_feature_lists or unusual_lists, limit=5)
    note = "Health score is an internal normalized score, not a failure probability."
    if overall_status == "limited_data":
        note = (
            f"Limited data: only {len(windows)} scored windows; at least "
            f"{minimum_windows_for_status} are required for a strong session-level status. "
            "Raw anomaly metrics and per-window scores are preserved."
        )
    return LegacyInferenceOutput(
        model_loaded=True,
        windows=windows,
        anomaly_ratio=round(float(anomaly_ratio), 6),
        anomaly_windows=anomaly_windows,
        health_score=health,
        overall_status=overall_status,
        most_unusual_features=most_unusual,
        note=note,
        evidence_window_count=len(windows),
        minimum_windows_for_status=minimum_windows_for_status,
        evidence_sufficient=len(windows) >= minimum_windows_for_status,
        model_metadata=bundle.metadata,
    )
