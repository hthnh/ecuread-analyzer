from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd

from app.config import MIN_SESSION_WINDOWS_FOR_STATUS


EPSILON = 1e-9


def clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def window_health_from_score(score: float, metadata: dict[str, Any]) -> float:
    """Map an Isolation Forest sample score to an internal 0..100 health score.

    This is an internal normalized score, not a mechanical-failure probability
    and not a diagnostic accuracy measure.
    """
    p01 = float(metadata.get("training_score_p01", score))
    p05 = float(metadata.get("training_score_p05", p01))
    median = float(metadata.get("training_score_median", p05))

    if score >= median:
        return 100.0
    if score >= p05:
        return clamp(60.0 + 40.0 * (score - p05) / max(median - p05, EPSILON))
    if score >= p01:
        return clamp(20.0 + 40.0 * (score - p01) / max(p05 - p01, EPSILON))

    lower_span = max(abs(p01), abs(p05 - p01), EPSILON)
    return clamp(20.0 * (1.0 - (p01 - score) / lower_span))


def session_health_score(window_health_scores: list[float], anomaly_ratio: float) -> float | None:
    if not window_health_scores:
        return None
    scores = pd.Series(window_health_scores, dtype=float)
    median_health = float(scores.median())
    lower_decile_health = float(scores.quantile(0.10))
    anomaly_penalty_component = 100.0 * (1.0 - min(1.0, anomaly_ratio))
    health = 0.65 * median_health + 0.20 * lower_decile_health + 0.15 * anomaly_penalty_component
    return round(clamp(health), 2)


def rank_unusual_features(feature_row: pd.Series, metadata: dict[str, Any], limit: int = 5) -> list[str]:
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


def session_most_unusual_features(window_feature_lists: list[list[str]], limit: int = 5) -> list[str]:
    counter: Counter[str] = Counter()
    for features in window_feature_lists:
        counter.update(features)
    return [feature for feature, _count in counter.most_common(limit)]


def status_from_scored_metrics(
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
