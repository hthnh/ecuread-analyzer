from __future__ import annotations

from typing import Any

import pandas as pd

from app.domain.model import DEFAULT_MODEL_FAMILY, DEFAULT_MODEL_NAME
from app.ml.artifacts import ModelBundle
from app.ml.harness import DetectorContext, DetectorIdentity, DetectorResult
from app.ml.scoring import (
    rank_unusual_features,
    session_health_score,
    session_most_unusual_features,
    status_from_scored_metrics,
    window_health_from_score,
)


ISOLATION_FOREST_DETECTOR_ID = "isolation_forest"


def isolation_forest_identity(metadata: dict[str, Any] | None = None) -> DetectorIdentity:
    resolved = metadata or {}
    model_version = (
        resolved.get("model_version_id")
        or resolved.get("version")
        or resolved.get("model_version")
    )
    return DetectorIdentity(
        detector_id=ISOLATION_FOREST_DETECTOR_ID,
        model_name=resolved.get("model_name") or DEFAULT_MODEL_NAME,
        model_family=resolved.get("model_family") or resolved.get("model_type") or DEFAULT_MODEL_FAMILY,
        model_version=model_version,
    )


class IsolationForestDetector:
    def __init__(self, bundle: ModelBundle, *, enabled: bool = True):
        self.bundle = bundle
        self._enabled = enabled
        self._identity = isolation_forest_identity(bundle.metadata)
        self._required_features = tuple(bundle.metadata.get("feature_names", []))

    @property
    def identity(self) -> DetectorIdentity:
        return self._identity

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def required_features(self) -> tuple[str, ...]:
        return self._required_features

    def infer(self, feature_frame: pd.DataFrame, context: DetectorContext) -> DetectorResult:
        x = feature_frame[list(self.required_features)].astype(float)
        x_scaled = self.bundle.scaler.transform(x)
        score_samples = self.bundle.model.score_samples(x_scaled)
        decision_scores = self.bundle.model.decision_function(x_scaled)
        predictions = self.bundle.model.predict(x_scaled)

        windows = feature_frame.copy()
        windows["score_sample"] = score_samples
        windows["decision_score"] = decision_scores
        windows["prediction"] = predictions
        windows["is_anomaly"] = predictions == -1
        windows["window_health_score"] = [
            window_health_from_score(float(score), self.bundle.metadata)
            for score in score_samples
        ]

        unusual_lists: list[list[str]] = []
        for _, row in windows.iterrows():
            unusual = rank_unusual_features(row, self.bundle.metadata, limit=5)
            unusual_lists.append(unusual)
        windows["most_unusual_features"] = [";".join(items) for items in unusual_lists]

        anomaly_windows = int((predictions == -1).sum())
        anomaly_ratio = anomaly_windows / max(1, len(windows))
        health = session_health_score(windows["window_health_score"].astype(float).tolist(), anomaly_ratio)
        minimum_windows = context.minimum_windows_for_status or 0
        overall_status = status_from_scored_metrics(
            window_count=len(windows),
            anomaly_ratio=anomaly_ratio,
            health_score=health,
            minimum_windows_for_status=minimum_windows,
        )

        anomaly_feature_lists = [
            items
            for items, prediction in zip(unusual_lists, predictions, strict=True)
            if prediction == -1
        ]
        most_unusual = session_most_unusual_features(anomaly_feature_lists or unusual_lists, limit=5)
        note = "Health score is an internal normalized score, not a failure probability."
        if overall_status == "limited_data":
            note = (
                f"Limited data: only {len(windows)} scored windows; at least "
                f"{minimum_windows} are required for a strong session-level status. "
                "Raw anomaly metrics and per-window scores are preserved."
            )

        return DetectorResult(
            identity=self.identity,
            status="ok",
            required_features=self.required_features,
            windows=windows,
            anomaly_score_column="score_sample",
            score_direction="lower_is_more_anomalous",
            prediction_column="prediction",
            is_anomaly_column="is_anomaly",
            evidence_columns=(
                "score_sample",
                "decision_score",
                "window_health_score",
                "most_unusual_features",
            ),
            scored_window_count=len(windows),
            anomaly_window_count=anomaly_windows,
            anomaly_ratio=round(float(anomaly_ratio), 6),
            metadata={
                "health_score": health,
                "overall_status": overall_status,
                "most_unusual_features": most_unusual,
                "note": note,
            },
        )
