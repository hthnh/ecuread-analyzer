from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.ml.harness import DetectorContext, DetectorIdentity, DetectorResult, ModelHarness
from app.ml.inference import load_model_bundle, run_inference
from app.ml.scoring import (
    rank_unusual_features,
    session_health_score,
    session_most_unusual_features,
    window_health_from_score,
)
from app.ml.training import METADATA_FILENAME, MODEL_FILENAME, SCALER_FILENAME


class IdentityScaler:
    def transform(self, values):
        return values


class FixedPredictionModel:
    def __init__(self, anomaly_count: int = 0, score: float = -0.25):
        self.anomaly_count = anomaly_count
        self.score = score

    def score_samples(self, values):
        return np.array([self.score] * len(values))

    def decision_function(self, values):
        return np.array([self.score] * len(values))

    def predict(self, values):
        return np.array([-1 if index < self.anomaly_count else 1 for index in range(len(values))])


def write_fixed_model(model_dir: Path, *, anomaly_count: int = 0, score: float = -0.25) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(FixedPredictionModel(anomaly_count=anomaly_count, score=score), model_dir / MODEL_FILENAME)
    joblib.dump(IdentityScaler(), model_dir / SCALER_FILENAME)
    metadata = {
        "model_name": "fixed-test-model",
        "model_family": "IsolationForest",
        "model_version_id": "fixed-test-model-v1",
        "version": "fixed-test-model-v1",
        "feature_schema_version": "ecu-window-features-v1",
        "feature_names": ["rpm_mean"],
        "feature_medians": {"rpm_mean": 0.0},
        "feature_iqrs": {"rpm_mean": 1.0},
        "training_score_p01": -1.0,
        "training_score_p05": -0.5,
        "training_score_median": 0.0,
    }
    (model_dir / METADATA_FILENAME).write_text(json.dumps(metadata), encoding="utf-8")


def feature_frame(window_count: int) -> pd.DataFrame:
    return pd.DataFrame({"window_index": list(range(window_count)), "rpm_mean": [float(index) for index in range(window_count)]})


def legacy_iforest_projection(feature_frame: pd.DataFrame, model_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    bundle = load_model_bundle(model_dir)
    feature_names = bundle.metadata["feature_names"]
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
    windows["window_health_score"] = [window_health_from_score(float(score), bundle.metadata) for score in score_samples]

    unusual_lists: list[list[str]] = []
    for _, row in windows.iterrows():
        unusual_lists.append(rank_unusual_features(row, bundle.metadata, limit=5))
    windows["most_unusual_features"] = [";".join(items) for items in unusual_lists]

    anomaly_windows = int((predictions == -1).sum())
    anomaly_ratio = anomaly_windows / max(1, len(windows))
    anomaly_feature_lists = [items for items, prediction in zip(unusual_lists, predictions, strict=True) if prediction == -1]
    return windows, {
        "anomaly_windows": anomaly_windows,
        "anomaly_ratio": round(float(anomaly_ratio), 6),
        "health_score": session_health_score(windows["window_health_score"].astype(float).tolist(), anomaly_ratio),
        "most_unusual_features": session_most_unusual_features(anomaly_feature_lists or unusual_lists, limit=5),
    }


def test_iforest_harness_regression_matches_legacy_projection(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    write_fixed_model(model_dir, anomaly_count=3, score=-0.25)
    features = feature_frame(12)

    output = run_inference(features, model_dir)
    expected_windows, expected_summary = legacy_iforest_projection(features, model_dir)

    pd.testing.assert_series_equal(output.windows["score_sample"], expected_windows["score_sample"], check_names=False)
    pd.testing.assert_series_equal(output.windows["decision_score"], expected_windows["decision_score"], check_names=False)
    pd.testing.assert_series_equal(output.windows["prediction"], expected_windows["prediction"], check_names=False)
    pd.testing.assert_series_equal(output.windows["is_anomaly"], expected_windows["is_anomaly"], check_names=False)
    pd.testing.assert_series_equal(
        output.windows["window_health_score"],
        expected_windows["window_health_score"],
        check_names=False,
    )
    pd.testing.assert_series_equal(
        output.windows["most_unusual_features"],
        expected_windows["most_unusual_features"],
        check_names=False,
    )
    assert output.anomaly_windows == expected_summary["anomaly_windows"]
    assert output.anomaly_ratio == expected_summary["anomaly_ratio"]
    assert output.health_score == expected_summary["health_score"]
    assert output.most_unusual_features == expected_summary["most_unusual_features"]
    assert output.detector_results[0]["status"] == "ok"
    assert output.detector_results[0]["anomaly_score"]["direction"] == "lower_is_more_anomalous"
    assert output.detector_results[0]["prediction"]["column"] == "prediction"
    assert output.detector_results[0]["available_evidence"] == [
        "score_sample",
        "decision_score",
        "window_health_score",
        "most_unusual_features",
    ]


class ToyDetector:
    def __init__(
        self,
        detector_id: str,
        *,
        required_features: tuple[str, ...] = ("rpm_mean",),
        fail: bool = False,
    ):
        self._identity = DetectorIdentity(
            detector_id=detector_id,
            model_name=f"{detector_id}-model",
            model_version="1",
            model_family="toy",
        )
        self._required_features = required_features
        self.fail = fail
        self.called = False

    @property
    def identity(self) -> DetectorIdentity:
        return self._identity

    @property
    def enabled(self) -> bool:
        return True

    @property
    def required_features(self) -> tuple[str, ...]:
        return self._required_features

    def infer(self, feature_frame: pd.DataFrame, context: DetectorContext) -> DetectorResult:
        self.called = True
        if self.fail:
            raise RuntimeError("boom")
        windows = feature_frame.copy()
        windows[f"{self.identity.detector_id}_score"] = [0.1] * len(windows)
        windows[f"{self.identity.detector_id}_prediction"] = [1] * len(windows)
        windows[f"{self.identity.detector_id}_is_anomaly"] = [False] * len(windows)
        return DetectorResult(
            identity=self.identity,
            status="ok",
            required_features=self.required_features,
            windows=windows,
            anomaly_score_column=f"{self.identity.detector_id}_score",
            score_direction="higher_is_more_anomalous",
            prediction_column=f"{self.identity.detector_id}_prediction",
            is_anomaly_column=f"{self.identity.detector_id}_is_anomaly",
            evidence_columns=(f"{self.identity.detector_id}_score",),
            scored_window_count=len(windows),
            anomaly_window_count=0,
            anomaly_ratio=0.0,
        )


def test_harness_registers_multiple_detectors_and_isolates_failures() -> None:
    first = ToyDetector("first")
    second = ToyDetector("second")
    failing = ToyDetector("failing", fail=True)
    harness = ModelHarness([first])
    harness.register(second)
    harness.register(failing)

    result = harness.run(feature_frame(2))

    assert [item.identity.detector_id for item in result.results] == ["first", "second", "failing"]
    assert [item.status for item in result.results] == ["ok", "ok", "failed"]
    assert first.called is True
    assert second.called is True
    assert failing.called is True
    payloads = result.window_payloads(window_count=2)
    assert payloads[0][0]["prediction"] == 1
    assert payloads[0][1]["prediction"] == 1
    assert payloads[0][2]["status"] == "failed"
    assert payloads[0][2]["prediction"] is None
    assert result.results[2].error_type == "RuntimeError"


def test_harness_marks_missing_features_as_skipped_without_prediction() -> None:
    detector = ToyDetector("needs-missing-feature", required_features=("missing_feature",))

    result = ModelHarness([detector]).run(feature_frame(2))

    skipped = result.results[0]
    assert skipped.status == "skipped"
    assert skipped.reason == "missing_features"
    assert skipped.missing_features == ("missing_feature",)
    assert detector.called is False
    payload = result.window_payloads(window_count=2)[0][0]
    assert payload["status"] == "skipped"
    assert payload["prediction"] is None
    assert payload["anomaly_score"] is None
