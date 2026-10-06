from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pandas as pd

from app.ml.harness import DetectorContext, DetectorIdentity, DetectorResult


CONTEXTUAL_BATTERY_DETECTOR_ID = "contextual_battery_voltage"
CONTEXTUAL_BATTERY_MODEL_NAME = "contextual-battery-voltage"
CONTEXTUAL_BATTERY_MODEL_FAMILY = "RobustContextualStats"
CONTEXTUAL_REFERENCE_SCHEMA_VERSION = "contextual-battery-reference-v1"

CONTEXT_FEATURES = (
    "rpm_median",
    "tps_raw_median",
    "tps_voltage_median",
)
SCORED_FEATURES = (
    "battery_voltage_median",
    "battery_voltage_mean",
    "battery_voltage_min",
)
CONTEXTUAL_REQUIRED_FEATURES = CONTEXT_FEATURES + SCORED_FEATURES

MODELED_CONTEXTS = (
    "idle_closed_throttle",
    "low_load_running",
    "mid_load_running",
)
DEFAULT_MIN_CONTEXT_WINDOWS = 50
DEFAULT_ROBUST_Z_THRESHOLD = 4.5
BATTERY_VOLTAGE_SCALE_FLOOR = 0.35


def assign_operating_context(row: pd.Series | dict[str, Any]) -> str:
    rpm = _float_or_nan(row.get("rpm_median"))
    tps_raw = _float_or_nan(row.get("tps_raw_median"))
    tps_voltage = _float_or_nan(row.get("tps_voltage_median"))
    if not all(math.isfinite(value) for value in (rpm, tps_raw, tps_voltage)):
        return "signals_unavailable"

    if rpm < 500.0:
        return "engine_off_or_transition"
    if rpm < 1800.0 and tps_raw <= 15.0 and tps_voltage <= 0.85:
        return "idle_closed_throttle"
    if rpm < 4500.0 and tps_raw <= 45.0 and tps_voltage <= 1.65:
        return "low_load_running"
    if rpm < 7500.0 and tps_raw <= 115.0 and tps_voltage <= 3.50:
        return "mid_load_running"
    if rpm >= 4500.0 and (tps_raw > 85.0 or tps_voltage > 2.60):
        return "high_load_running"
    return "unmodeled"


def build_contextual_battery_reference(
    feature_frames_by_session: dict[str, pd.DataFrame],
    *,
    provenance: dict[str, Any],
    min_context_windows: int = DEFAULT_MIN_CONTEXT_WINDOWS,
    robust_z_threshold: float = DEFAULT_ROBUST_Z_THRESHOLD,
) -> dict[str, Any]:
    rows: list[pd.DataFrame] = []
    observed_context_counts: dict[str, int] = {}
    for session_id, feature_frame in sorted(feature_frames_by_session.items()):
        frame = feature_frame.copy()
        frame["reference_session_id"] = session_id
        frame["operating_context"] = frame.apply(assign_operating_context, axis=1)
        for context, count in frame["operating_context"].value_counts().items():
            observed_context_counts[str(context)] = observed_context_counts.get(str(context), 0) + int(count)
        rows.append(frame)

    combined = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    contexts: dict[str, Any] = {}
    for context_name in MODELED_CONTEXTS:
        chunk = combined[combined["operating_context"] == context_name] if not combined.empty else pd.DataFrame()
        if len(chunk) < min_context_windows:
            continue
        features: dict[str, Any] = {}
        for feature in SCORED_FEATURES:
            features[feature] = _robust_stats(chunk[feature], scale_floor=BATTERY_VOLTAGE_SCALE_FLOOR)
        contexts[context_name] = {
            "window_count": int(len(chunk)),
            "reference_session_count": int(chunk["reference_session_id"].nunique()),
            "reference_session_ids": sorted(chunk["reference_session_id"].dropna().astype(str).unique().tolist()),
            "features": features,
        }

    payload: dict[str, Any] = {
        "schema_version": CONTEXTUAL_REFERENCE_SCHEMA_VERSION,
        "detector_id": CONTEXTUAL_BATTERY_DETECTOR_ID,
        "model_name": CONTEXTUAL_BATTERY_MODEL_NAME,
        "model_family": CONTEXTUAL_BATTERY_MODEL_FAMILY,
        "configuration": {
            "context_features": list(CONTEXT_FEATURES),
            "scored_features": list(SCORED_FEATURES),
            "required_features": list(CONTEXTUAL_REQUIRED_FEATURES),
            "modeled_contexts": list(MODELED_CONTEXTS),
            "min_context_windows": int(min_context_windows),
            "robust_z_threshold": float(robust_z_threshold),
            "battery_voltage_scale_floor": BATTERY_VOLTAGE_SCALE_FLOOR,
            "context_policy": (
                "RPM, TPS raw and TPS voltage define operating context; battery voltage is scored "
                "against the matched context. RPM is not inferred from TPS."
            ),
        },
        "provenance": provenance,
        "reference_session_ids": sorted(feature_frames_by_session),
        "observed_context_counts": dict(sorted(observed_context_counts.items())),
        "contexts": contexts,
        "limitations": [
            "Detector score is a context-conditioned robust z-score, not a failure probability.",
            "A positive result is a statistical deviation requiring manual review, not a confirmed mechanical failure.",
            "Contexts below the minimum reference-window count are not modeled and are skipped.",
        ],
    }
    payload["model_version"] = _reference_version(payload)
    return payload


class ContextualBatteryVoltageDetector:
    def __init__(self, reference: dict[str, Any], *, enabled: bool = True):
        self.reference = reference
        self._enabled = enabled
        self._identity = DetectorIdentity(
            detector_id=reference.get("detector_id", CONTEXTUAL_BATTERY_DETECTOR_ID),
            model_name=reference.get("model_name", CONTEXTUAL_BATTERY_MODEL_NAME),
            model_family=reference.get("model_family", CONTEXTUAL_BATTERY_MODEL_FAMILY),
            model_version=reference.get("model_version"),
        )
        configuration = reference.get("configuration", {})
        self._required_features = tuple(configuration.get("required_features", CONTEXTUAL_REQUIRED_FEATURES))
        self._threshold = float(configuration.get("robust_z_threshold", DEFAULT_ROBUST_Z_THRESHOLD))
        self._contexts: dict[str, Any] = dict(reference.get("contexts", {}))

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
        windows = feature_frame.copy()
        if feature_frame.empty:
            return self._skipped_result(windows, reason="no_windows")

        context_names: list[str] = []
        statuses: list[str] = []
        scores: list[float | None] = []
        thresholds: list[float | None] = []
        predictions: list[int | None] = []
        anomalies: list[bool | None] = []
        top_features: list[str | None] = []
        evidence_rows: list[dict[str, Any]] = []

        for _, row in feature_frame.iterrows():
            context_name = assign_operating_context(row)
            context_names.append(context_name)
            context_stats = self._contexts.get(context_name)
            if not context_stats:
                statuses.append("skipped_context")
                scores.append(None)
                thresholds.append(None)
                predictions.append(None)
                anomalies.append(None)
                top_features.append(None)
                evidence_rows.append(
                    {
                        "applicable": False,
                        "status": "skipped_context",
                        "operating_context": context_name,
                        "reason": "context_not_modeled",
                    }
                )
                continue

            scored = self._score_row(row, context_name, context_stats)
            score = scored["score"]
            is_anomaly = score >= self._threshold
            statuses.append("ok")
            scores.append(score)
            thresholds.append(self._threshold)
            predictions.append(-1 if is_anomaly else 1)
            anomalies.append(is_anomaly)
            top_features.append(scored["top_feature"])
            evidence_rows.append(scored["evidence"])

        windows["operating_context"] = context_names
        windows["contextual_status"] = statuses
        windows["contextual_applicable"] = [status == "ok" for status in statuses]
        windows["contextual_anomaly_score"] = scores
        windows["contextual_threshold"] = thresholds
        windows["contextual_top_feature"] = top_features
        windows["contextual_evidence"] = evidence_rows
        windows["prediction"] = pd.Series(predictions, index=windows.index, dtype=object)
        windows["is_anomaly"] = pd.Series(anomalies, index=windows.index, dtype=object)

        applicable_count = sum(1 for status in statuses if status == "ok")
        if applicable_count == 0:
            return self._skipped_result(windows, reason="no_applicable_context")

        anomaly_count = sum(1 for value in anomalies if value is True)
        return DetectorResult(
            identity=self.identity,
            status="ok",
            required_features=self.required_features,
            windows=windows,
            anomaly_score_column="contextual_anomaly_score",
            score_direction="higher_is_more_anomalous",
            prediction_column="prediction",
            is_anomaly_column="is_anomaly",
            evidence_columns=(
                "operating_context",
                "contextual_status",
                "contextual_applicable",
                "contextual_threshold",
                "contextual_top_feature",
                "contextual_evidence",
            ),
            scored_window_count=applicable_count,
            anomaly_window_count=anomaly_count,
            anomaly_ratio=round(anomaly_count / max(1, applicable_count), 6),
            metadata={
                "threshold": self._threshold,
                "applicable_window_count": applicable_count,
                "skipped_window_count": len(windows) - applicable_count,
                "modeled_contexts": sorted(self._contexts),
                "statistical_interpretation": (
                    "Contextual score is max robust z-score across verified battery-voltage features. "
                    "Anomalies are statistical deviations for manual review, not confirmed failures."
                ),
            },
        )

    def _score_row(self, row: pd.Series, context_name: str, context_stats: dict[str, Any]) -> dict[str, Any]:
        feature_scores: list[dict[str, Any]] = []
        for feature in SCORED_FEATURES:
            stats = context_stats["features"][feature]
            observed = _float_or_nan(row.get(feature))
            reference_median = float(stats["median"])
            robust_scale = float(stats["robust_scale"])
            signed_delta = observed - reference_median
            robust_z = abs(signed_delta) / max(robust_scale, 1e-9)
            feature_scores.append(
                {
                    "feature": feature,
                    "observed": _round(observed),
                    "reference_median": _round(reference_median),
                    "robust_scale": _round(robust_scale),
                    "signed_delta": _round(signed_delta),
                    "robust_z": _round(robust_z),
                    "direction": "higher" if signed_delta > 0 else "lower" if signed_delta < 0 else "matched",
                }
            )
        top = max(feature_scores, key=lambda item: float(item["robust_z"]))
        return {
            "score": float(top["robust_z"]),
            "top_feature": str(top["feature"]),
            "evidence": {
                "applicable": True,
                "status": "ok",
                "operating_context": context_name,
                "threshold": self._threshold,
                "top_deviation": top,
                "feature_scores": feature_scores,
                "interpretation": "statistical deviation; not a confirmed mechanical failure",
            },
        }

    def _skipped_result(self, windows: pd.DataFrame, *, reason: str) -> DetectorResult:
        return DetectorResult(
            identity=self.identity,
            status="skipped",
            reason=reason,
            required_features=self.required_features,
            windows=windows,
            evidence_columns=(
                "operating_context",
                "contextual_status",
                "contextual_applicable",
                "contextual_evidence",
            ),
            scored_window_count=0,
            metadata={
                "threshold": self._threshold,
                "applicable_window_count": 0,
                "skipped_window_count": int(len(windows)),
                "modeled_contexts": sorted(self._contexts),
            },
        )


def _robust_stats(series: pd.Series, *, scale_floor: float) -> dict[str, Any]:
    clean = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    median = float(clean.median())
    mad = float((clean - median).abs().median())
    q1 = float(clean.quantile(0.25))
    q3 = float(clean.quantile(0.75))
    iqr = q3 - q1
    robust_scale = max(mad * 1.4826, iqr / 1.349 if iqr > 0 else 0.0, scale_floor)
    return {
        "count": int(clean.count()),
        "median": _round(median),
        "mad": _round(mad),
        "iqr": _round(iqr),
        "robust_scale": _round(robust_scale),
        "scale_floor": scale_floor,
        "p10": _round(float(clean.quantile(0.10))),
        "p90": _round(float(clean.quantile(0.90))),
    }


def _reference_version(reference: dict[str, Any]) -> str:
    version_input = {key: value for key, value in reference.items() if key != "model_version"}
    encoded = json.dumps(version_input, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"contextual-battery-robust-v1-{digest}"


def _float_or_nan(value: Any) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return math.nan
    return resolved if math.isfinite(resolved) else math.nan


def _round(value: float) -> float:
    return round(float(value), 10)
