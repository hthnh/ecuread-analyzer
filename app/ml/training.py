from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from app.config import Settings
from app.domain.model import DEFAULT_MODEL_FAMILY, DEFAULT_MODEL_NAME, FEATURE_SCHEMA_VERSION
from app.ml.features import get_feature_names


MODEL_VERSION = "0.1.0"
MODEL_FILENAME = "isolation_forest.joblib"
SCALER_FILENAME = "robust_scaler.joblib"
METADATA_FILENAME = "model_metadata.json"


def model_paths(model_dir: str | Path) -> dict[str, Path]:
    root = Path(model_dir)
    return {
        "model": root / MODEL_FILENAME,
        "scaler": root / SCALER_FILENAME,
        "metadata": root / METADATA_FILENAME,
    }


def model_artifacts_exist(model_dir: str | Path) -> bool:
    paths = model_paths(model_dir)
    return all(path.exists() for path in paths.values())


def _atomic_json_dump(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def _atomic_joblib_dump(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    joblib.dump(payload, tmp_path)
    os.replace(tmp_path, path)


def _feature_distribution(features: pd.DataFrame, feature_names: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    medians: dict[str, float] = {}
    iqrs: dict[str, float] = {}
    for feature_name in feature_names:
        series = pd.to_numeric(features[feature_name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        q25 = float(series.quantile(0.25))
        q75 = float(series.quantile(0.75))
        medians[feature_name] = float(series.median())
        iqrs[feature_name] = q75 - q25
    return medians, iqrs


def train_isolation_forest(
    feature_frame: pd.DataFrame,
    training_sessions: list[str],
    settings: Settings,
    model_dir: str | Path | None = None,
    notes: str = "Not validated for mechanical fault diagnosis",
    training_decoder_versions: list[str] | None = None,
    feature_schema_version: str = FEATURE_SCHEMA_VERSION,
    signal_columns: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    output_dir = Path(model_dir or settings.model_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    feature_names = get_feature_names(signal_columns)
    missing = [feature for feature in feature_names if feature not in feature_frame.columns]
    if missing:
        raise ValueError(f"feature frame is missing required features: {missing}")
    if feature_frame.empty:
        raise ValueError("cannot train Isolation Forest without feature rows")
    if feature_schema_version != FEATURE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported feature_schema_version {feature_schema_version!r}; "
            f"expected {FEATURE_SCHEMA_VERSION!r}"
        )

    x_train = feature_frame[feature_names].astype(float)
    scaler = RobustScaler()
    x_scaled = scaler.fit_transform(x_train)

    model = IsolationForest(
        n_estimators=settings.isolation_n_estimators,
        max_samples="auto",
        contamination=settings.isolation_contamination,
        random_state=settings.isolation_random_state,
        n_jobs=-1,
    )
    model.fit(x_scaled)

    score_samples = model.score_samples(x_scaled)
    decision_scores = model.decision_function(x_scaled)
    medians, iqrs = _feature_distribution(x_train, feature_names)

    model_version = f"iforest-baseline-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    metadata: dict[str, Any] = {
        "model_name": DEFAULT_MODEL_NAME,
        "model_family": DEFAULT_MODEL_FAMILY,
        "model_version_id": model_version,
        "version": model_version,
        "model_type": "IsolationForest",
        "model_version": MODEL_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "feature_names": feature_names,
        "signal_columns": signal_columns,
        "feature_schema_version": feature_schema_version,
        "training_sessions": training_sessions,
        "training_decoder_versions": sorted(set(training_decoder_versions or [])),
        "window_size_samples": settings.window_size_samples,
        "window_step_samples": settings.window_step_samples,
        "contamination": settings.isolation_contamination,
        "n_estimators": settings.isolation_n_estimators,
        "hyperparameters": {
            "n_estimators": settings.isolation_n_estimators,
            "contamination": settings.isolation_contamination,
            "random_state": settings.isolation_random_state,
        },
        "training_scope": "experimental ECU logs",
        "training_data_note": "experimental baseline training data",
        "notes": notes,
        "training_score_p01": float(np.percentile(score_samples, 1)),
        "training_score_p05": float(np.percentile(score_samples, 5)),
        "training_score_median": float(np.percentile(score_samples, 50)),
        "training_score_p95": float(np.percentile(score_samples, 95)),
        "training_decision_p01": float(np.percentile(decision_scores, 1)),
        "training_decision_p05": float(np.percentile(decision_scores, 5)),
        "training_decision_median": float(np.percentile(decision_scores, 50)),
        "feature_medians": medians,
        "feature_iqrs": iqrs,
        "training_window_count": int(len(feature_frame)),
        "training_seconds": round(perf_counter() - started, 4),
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    paths = model_paths(output_dir)
    _atomic_joblib_dump(model, paths["model"])
    _atomic_joblib_dump(scaler, paths["scaler"])
    _atomic_json_dump(metadata, paths["metadata"])
    return metadata
