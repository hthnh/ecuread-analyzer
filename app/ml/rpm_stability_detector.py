from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from app.ml.harness import DetectorContext, DetectorIdentity, DetectorResult


RPM_STABILITY_DETECTOR_ID = "temporal_rpm_stability"
RPM_STABILITY_MODEL_NAME = "temporal-rpm-stability"
RPM_STABILITY_MODEL_FAMILY = "RobustTemporalStability"
RPM_STABILITY_REQUIRED_FEATURES = (
    "window_index",
    "start_frame_index",
    "end_frame_index",
    "rpm_median",
    "rpm_std",
    "rpm_range",
    "rpm_mean_absolute_diff",
    "tps_raw_median",
    "tps_voltage_median",
)


@dataclass(frozen=True, slots=True)
class RpmStabilityConfig:
    min_engine_running_rpm: float = 500.0
    closed_tps_raw_max: float = 5.0
    closed_tps_voltage_max: float = 0.65
    min_warmup_windows: int = 8
    min_persistence_windows: int = 4
    ewma_alpha: float = 0.3
    robust_z_threshold: float = 3.0
    min_dispersion_increase_rpm: float = 250.0
    dispersion_scale_floor_rpm: float = 100.0
    max_start_frame_step: int = 15

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def model_version(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()[:12]
        return f"temporal-rpm-stability-v1-{digest}"


class RpmStabilityDetector:
    def __init__(self, config: RpmStabilityConfig | None = None, *, enabled: bool = True):
        self.config = config or RpmStabilityConfig()
        self._enabled = enabled
        self._identity = DetectorIdentity(
            detector_id=RPM_STABILITY_DETECTOR_ID,
            model_name=RPM_STABILITY_MODEL_NAME,
            model_family=RPM_STABILITY_MODEL_FAMILY,
            model_version=self.config.model_version,
        )

    @property
    def identity(self) -> DetectorIdentity:
        return self._identity

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def required_features(self) -> tuple[str, ...]:
        return RPM_STABILITY_REQUIRED_FEATURES

    def infer(self, feature_frame: pd.DataFrame, context: DetectorContext) -> DetectorResult:
        windows = feature_frame.copy()
        if feature_frame.empty:
            return self._skipped_result(windows, reason="no_windows")

        state = _RpmStabilityState()
        rows: list[dict[str, Any]] = []
        event_counter = 0
        active_event_id: str | None = None

        for _, row in feature_frame.iterrows():
            eligible = self._eligible(row)
            observed_dispersion = _float_or_nan(row.get("rpm_std"))
            observed_center = _float_or_nan(row.get("rpm_median"))
            start_frame = _int_or_none(row.get("start_frame_index"))
            if not eligible:
                state = _RpmStabilityState()
                active_event_id = None
                rows.append(
                    self._row_payload(
                        temporal_status="skipped",
                        reason="not_closed_throttle_running",
                        observed_center=observed_center,
                        observed_dispersion=observed_dispersion,
                        closed_throttle_running=False,
                    )
                )
                continue
            if not math.isfinite(observed_dispersion) or not math.isfinite(observed_center):
                state = _RpmStabilityState()
                active_event_id = None
                rows.append(
                    self._row_payload(
                        temporal_status="skipped",
                        reason="rpm_signal_unavailable",
                        observed_center=observed_center,
                        observed_dispersion=observed_dispersion,
                        closed_throttle_running=True,
                    )
                )
                continue

            reset_reason = None
            if state.last_start_frame is not None and start_frame is not None:
                if start_frame - state.last_start_frame > self.config.max_start_frame_step:
                    state = _RpmStabilityState()
                    active_event_id = None
                    reset_reason = "gap_reset"
            state.last_start_frame = start_frame
            state.run_window_count += 1

            if len(state.baseline_dispersions) < self.config.min_warmup_windows:
                state.baseline_dispersions.append(observed_dispersion)
                state.baseline_centers.append(observed_center)
                state.ewma_dispersion = (
                    observed_dispersion
                    if state.ewma_dispersion is None
                    else _ewma(state.ewma_dispersion, observed_dispersion, self.config.ewma_alpha)
                )
                rows.append(
                    self._row_payload(
                        temporal_status="warming",
                        reason=reset_reason or "history_building",
                        observed_center=observed_center,
                        observed_dispersion=observed_dispersion,
                        closed_throttle_running=True,
                        baseline_center=_median(state.baseline_centers),
                        baseline_dispersion=_median(state.baseline_dispersions),
                        baseline_dispersion_scale=_robust_scale(
                            state.baseline_dispersions,
                            self.config.dispersion_scale_floor_rpm,
                        ),
                        ewma_dispersion=state.ewma_dispersion,
                        run_window_count=state.run_window_count,
                        warmup_window_count=len(state.baseline_dispersions),
                    )
                )
                continue

            baseline_center = _median(state.baseline_centers)
            baseline_dispersion = _median(state.baseline_dispersions)
            baseline_scale = _robust_scale(state.baseline_dispersions, self.config.dispersion_scale_floor_rpm)
            state.ewma_dispersion = (
                observed_dispersion
                if state.ewma_dispersion is None
                else _ewma(state.ewma_dispersion, observed_dispersion, self.config.ewma_alpha)
            )
            dispersion_increase = state.ewma_dispersion - baseline_dispersion
            robust_z = dispersion_increase / max(baseline_scale, 1e-9)
            statistic = max(0.0, robust_z)
            shifted = (
                statistic >= self.config.robust_z_threshold
                and dispersion_increase >= self.config.min_dispersion_increase_rpm
            )
            if shifted:
                state.persistence_count += 1
            else:
                state.persistence_count = 0
                active_event_id = None

            is_anomaly = state.persistence_count >= self.config.min_persistence_windows
            if is_anomaly and active_event_id is None:
                event_counter += 1
                active_event_id = f"rpm-stability-change-{event_counter:03d}"

            rows.append(
                self._row_payload(
                    temporal_status="applicable",
                    reason=None,
                    observed_center=observed_center,
                    observed_dispersion=observed_dispersion,
                    closed_throttle_running=True,
                    baseline_center=baseline_center,
                    baseline_dispersion=baseline_dispersion,
                    baseline_dispersion_scale=baseline_scale,
                    ewma_dispersion=state.ewma_dispersion,
                    dispersion_increase=dispersion_increase,
                    change_statistic=statistic,
                    run_window_count=state.run_window_count,
                    warmup_window_count=len(state.baseline_dispersions),
                    persistence_count=state.persistence_count,
                    prediction=-1 if is_anomaly else 1,
                    is_anomaly=is_anomaly,
                    event_id=active_event_id if is_anomaly else None,
                )
            )

        for column in rows[0].keys() if rows else ():
            values = [item[column] for item in rows]
            if column in {"prediction", "is_anomaly", "rpm_stability_event_id"}:
                windows[column] = pd.Series(values, index=windows.index, dtype=object)
            else:
                windows[column] = values

        applicable_count = sum(1 for item in rows if item["rpm_stability_status"] == "applicable")
        warmup_count = sum(1 for item in rows if item["rpm_stability_status"] == "warming")
        skipped_count = sum(1 for item in rows if item["rpm_stability_status"] == "skipped")
        if applicable_count == 0:
            result = self._skipped_result(windows, reason="insufficient_temporal_history")
            result.metadata.update(
                {
                    "configuration": self.config.to_dict(),
                    "applicable_window_count": 0,
                    "warmup_window_count": warmup_count,
                    "skipped_window_count": skipped_count,
                }
            )
            return result

        anomaly_count = sum(1 for item in rows if item["is_anomaly"] is True)
        return DetectorResult(
            identity=self.identity,
            status="ok",
            required_features=self.required_features,
            windows=windows,
            anomaly_score_column="rpm_stability_change_statistic",
            score_direction="higher_is_more_anomalous",
            prediction_column="prediction",
            is_anomaly_column="is_anomaly",
            evidence_columns=(
                "rpm_stability_status",
                "rpm_stability_reason",
                "rpm_closed_throttle_running",
                "rpm_observed_center",
                "rpm_observed_dispersion",
                "rpm_baseline_center",
                "rpm_baseline_dispersion",
                "rpm_baseline_dispersion_scale",
                "rpm_ewma_dispersion",
                "rpm_dispersion_increase",
                "rpm_stability_change_statistic",
                "rpm_stability_threshold",
                "rpm_stability_persistence_count",
                "rpm_stability_context_window_count",
                "rpm_stability_event_id",
            ),
            scored_window_count=applicable_count,
            anomaly_window_count=anomaly_count,
            anomaly_ratio=round(anomaly_count / max(1, applicable_count), 6),
            metadata={
                "configuration": self.config.to_dict(),
                "applicable_window_count": applicable_count,
                "warmup_window_count": warmup_count,
                "skipped_window_count": skipped_count,
                "state_scope": "within-session only; resets after non-closed-throttle windows, gaps, and every infer() call",
                "interpretation": "RPM instability is a temporal statistical pattern, not a confirmed mechanical fault.",
            },
        )

    def _eligible(self, row: pd.Series) -> bool:
        rpm = _float_or_nan(row.get("rpm_median"))
        tps_raw = _float_or_nan(row.get("tps_raw_median"))
        tps_voltage = _float_or_nan(row.get("tps_voltage_median"))
        return (
            math.isfinite(rpm)
            and math.isfinite(tps_raw)
            and math.isfinite(tps_voltage)
            and rpm >= self.config.min_engine_running_rpm
            and tps_raw <= self.config.closed_tps_raw_max
            and tps_voltage <= self.config.closed_tps_voltage_max
        )

    def _row_payload(
        self,
        *,
        temporal_status: str,
        reason: str | None,
        observed_center: float,
        observed_dispersion: float,
        closed_throttle_running: bool,
        baseline_center: float | None = None,
        baseline_dispersion: float | None = None,
        baseline_dispersion_scale: float | None = None,
        ewma_dispersion: float | None = None,
        dispersion_increase: float | None = None,
        change_statistic: float | None = None,
        run_window_count: int = 0,
        warmup_window_count: int = 0,
        persistence_count: int = 0,
        prediction: int | None = None,
        is_anomaly: bool | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "rpm_stability_status": temporal_status,
            "rpm_stability_reason": reason,
            "rpm_closed_throttle_running": closed_throttle_running,
            "rpm_observed_center": _round_or_none(observed_center),
            "rpm_observed_dispersion": _round_or_none(observed_dispersion),
            "rpm_baseline_center": _round_or_none(baseline_center),
            "rpm_baseline_dispersion": _round_or_none(baseline_dispersion),
            "rpm_baseline_dispersion_scale": _round_or_none(baseline_dispersion_scale),
            "rpm_ewma_dispersion": _round_or_none(ewma_dispersion),
            "rpm_dispersion_increase": _round_or_none(dispersion_increase),
            "rpm_stability_change_statistic": _round_or_none(change_statistic),
            "rpm_stability_threshold": self.config.robust_z_threshold if temporal_status == "applicable" else None,
            "rpm_stability_persistence_count": persistence_count,
            "rpm_stability_context_window_count": run_window_count,
            "rpm_stability_warmup_window_count": warmup_window_count,
            "rpm_stability_event_id": event_id,
            "prediction": prediction,
            "is_anomaly": is_anomaly,
        }

    def _skipped_result(self, windows: pd.DataFrame, *, reason: str) -> DetectorResult:
        return DetectorResult(
            identity=self.identity,
            status="skipped",
            reason=reason,
            required_features=self.required_features,
            windows=windows,
            evidence_columns=(
                "rpm_stability_status",
                "rpm_stability_reason",
                "rpm_closed_throttle_running",
                "rpm_observed_center",
                "rpm_observed_dispersion",
            ),
            scored_window_count=0,
            metadata={
                "configuration": self.config.to_dict(),
                "state_scope": "within-session only; resets after non-closed-throttle windows, gaps, and every infer() call",
            },
        )


@dataclass(slots=True)
class _RpmStabilityState:
    baseline_dispersions: list[float] = None  # type: ignore[assignment]
    baseline_centers: list[float] = None  # type: ignore[assignment]
    ewma_dispersion: float | None = None
    persistence_count: int = 0
    run_window_count: int = 0
    last_start_frame: int | None = None

    def __post_init__(self) -> None:
        if self.baseline_dispersions is None:
            self.baseline_dispersions = []
        if self.baseline_centers is None:
            self.baseline_centers = []


def _ewma(previous: float, observed: float, alpha: float) -> float:
    return alpha * observed + (1.0 - alpha) * previous


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


def _robust_scale(values: list[float], floor: float) -> float:
    if not values:
        return floor
    median = _median(values)
    mad = statistics.median(abs(value - median) for value in values)
    return max(float(mad) * 1.4826, floor)


def _float_or_nan(value: Any) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return math.nan
    return resolved if math.isfinite(resolved) else math.nan


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), 10)
