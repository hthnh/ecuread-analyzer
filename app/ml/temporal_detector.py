from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from app.ml.contextual_detector import MODELED_CONTEXTS, assign_operating_context
from app.ml.harness import DetectorContext, DetectorIdentity, DetectorResult


TEMPORAL_BATTERY_DETECTOR_ID = "temporal_battery_shift"
TEMPORAL_BATTERY_MODEL_NAME = "temporal-battery-shift"
TEMPORAL_BATTERY_MODEL_FAMILY = "EWMAChangeDetector"
TEMPORAL_REQUIRED_FEATURES = (
    "window_index",
    "start_frame_index",
    "end_frame_index",
    "rpm_median",
    "tps_raw_median",
    "tps_voltage_median",
    "battery_voltage_median",
)


@dataclass(frozen=True, slots=True)
class TemporalBatteryShiftConfig:
    ewma_alpha: float = 0.3
    min_warmup_windows: int = 8
    min_persistence_windows: int = 4
    shift_threshold_volts: float = 0.4
    max_start_frame_step: int = 15
    modeled_contexts: tuple[str, ...] = MODELED_CONTEXTS

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["modeled_contexts"] = list(self.modeled_contexts)
        return payload

    @property
    def model_version(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()[:12]
        return f"temporal-battery-ewma-v1-{digest}"


class TemporalBatteryShiftDetector:
    def __init__(self, config: TemporalBatteryShiftConfig | None = None, *, enabled: bool = True):
        self.config = config or TemporalBatteryShiftConfig()
        self._enabled = enabled
        self._identity = DetectorIdentity(
            detector_id=TEMPORAL_BATTERY_DETECTOR_ID,
            model_name=TEMPORAL_BATTERY_MODEL_NAME,
            model_family=TEMPORAL_BATTERY_MODEL_FAMILY,
            model_version=self.config.model_version,
        )
        self._required_features = TEMPORAL_REQUIRED_FEATURES

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

        state = _TemporalState()
        rows: list[dict[str, Any]] = []
        event_counter = 0
        active_event_id: str | None = None
        modeled_contexts = set(self.config.modeled_contexts)

        for _, row in feature_frame.iterrows():
            operating_context = assign_operating_context(row)
            observed = _float_or_nan(row.get("battery_voltage_median"))
            start_frame = _int_or_none(row.get("start_frame_index"))
            reset_reason = None

            if operating_context not in modeled_contexts:
                state = _TemporalState()
                active_event_id = None
                rows.append(
                    self._row_payload(
                        operating_context=operating_context,
                        observed=observed,
                        temporal_status="skipped",
                        reason="context_not_modeled",
                    )
                )
                continue
            if not math.isfinite(observed):
                state = _TemporalState()
                active_event_id = None
                rows.append(
                    self._row_payload(
                        operating_context=operating_context,
                        observed=observed,
                        temporal_status="skipped",
                        reason="signal_unavailable",
                    )
                )
                continue

            if state.context != operating_context:
                state = _TemporalState(context=operating_context)
                active_event_id = None
                reset_reason = "context_reset"
            elif (
                state.last_start_frame is not None
                and start_frame is not None
                and start_frame - state.last_start_frame > self.config.max_start_frame_step
            ):
                state = _TemporalState(context=operating_context)
                active_event_id = None
                reset_reason = "gap_reset"

            state.last_start_frame = start_frame
            state.run_window_count += 1

            if len(state.baseline_values) < self.config.min_warmup_windows:
                state.baseline_values.append(observed)
                state.ewma = observed if state.ewma is None else _ewma(state.ewma, observed, self.config.ewma_alpha)
                baseline = _median(state.baseline_values)
                rows.append(
                    self._row_payload(
                        operating_context=operating_context,
                        observed=observed,
                        temporal_status="warming",
                        reason=reset_reason or "history_building",
                        baseline=baseline,
                        ewma=state.ewma,
                        run_window_count=state.run_window_count,
                        warmup_window_count=len(state.baseline_values),
                    )
                )
                continue

            baseline = _median(state.baseline_values)
            state.ewma = observed if state.ewma is None else _ewma(state.ewma, observed, self.config.ewma_alpha)
            deviation = state.ewma - baseline
            change_statistic = abs(deviation) / max(self.config.shift_threshold_volts, 1e-9)
            if abs(deviation) >= self.config.shift_threshold_volts:
                state.persistence_count += 1
            else:
                state.persistence_count = 0
                active_event_id = None

            is_anomaly = state.persistence_count >= self.config.min_persistence_windows
            if is_anomaly and active_event_id is None:
                event_counter += 1
                active_event_id = f"temporal-change-{event_counter:03d}"

            rows.append(
                self._row_payload(
                    operating_context=operating_context,
                    observed=observed,
                    temporal_status="applicable",
                    reason=None,
                    baseline=baseline,
                    ewma=state.ewma,
                    deviation=deviation,
                    change_statistic=change_statistic,
                    run_window_count=state.run_window_count,
                    warmup_window_count=len(state.baseline_values),
                    persistence_count=state.persistence_count,
                    prediction=-1 if is_anomaly else 1,
                    is_anomaly=is_anomaly,
                    event_id=active_event_id if is_anomaly else None,
                )
            )

        for column in rows[0].keys() if rows else ():
            values = [item[column] for item in rows]
            if column in {"prediction", "is_anomaly", "temporal_event_id"}:
                windows[column] = pd.Series(values, index=windows.index, dtype=object)
            else:
                windows[column] = values

        applicable_count = sum(1 for item in rows if item["temporal_status"] == "applicable")
        warmup_count = sum(1 for item in rows if item["temporal_status"] == "warming")
        skipped_count = sum(1 for item in rows if item["temporal_status"] == "skipped")
        if applicable_count == 0:
            result = self._skipped_result(windows, reason="insufficient_temporal_history")
            result.metadata.update(
                {
                    "configuration": self.config.to_dict(),
                    "warmup_window_count": warmup_count,
                    "skipped_window_count": skipped_count,
                    "applicable_window_count": 0,
                }
            )
            return result

        anomaly_count = sum(1 for item in rows if item["is_anomaly"] is True)
        return DetectorResult(
            identity=self.identity,
            status="ok",
            required_features=self.required_features,
            windows=windows,
            anomaly_score_column="temporal_change_statistic",
            score_direction="higher_is_more_anomalous",
            prediction_column="prediction",
            is_anomaly_column="is_anomaly",
            evidence_columns=(
                "temporal_status",
                "temporal_reason",
                "temporal_context",
                "temporal_observed_value",
                "temporal_baseline_value",
                "temporal_ewma_value",
                "temporal_deviation",
                "temporal_change_statistic",
                "temporal_threshold",
                "temporal_persistence_count",
                "temporal_run_window_count",
                "temporal_event_id",
            ),
            scored_window_count=applicable_count,
            anomaly_window_count=anomaly_count,
            anomaly_ratio=round(anomaly_count / max(1, applicable_count), 6),
            metadata={
                "configuration": self.config.to_dict(),
                "warmup_window_count": warmup_count,
                "skipped_window_count": skipped_count,
                "applicable_window_count": applicable_count,
                "temporal_state_scope": "within-session only; resets on context changes, window gaps and every infer() call",
                "interpretation": "EWMA shift is a temporal statistical change, not a confirmed fault.",
            },
        )

    def _row_payload(
        self,
        *,
        operating_context: str,
        observed: float,
        temporal_status: str,
        reason: str | None,
        baseline: float | None = None,
        ewma: float | None = None,
        deviation: float | None = None,
        change_statistic: float | None = None,
        run_window_count: int = 0,
        warmup_window_count: int = 0,
        persistence_count: int = 0,
        prediction: int | None = None,
        is_anomaly: bool | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "temporal_status": temporal_status,
            "temporal_reason": reason,
            "temporal_context": operating_context,
            "temporal_observed_value": _round_or_none(observed),
            "temporal_baseline_value": _round_or_none(baseline),
            "temporal_ewma_value": _round_or_none(ewma),
            "temporal_deviation": _round_or_none(deviation),
            "temporal_change_statistic": _round_or_none(change_statistic),
            "temporal_threshold": self.config.shift_threshold_volts if temporal_status == "applicable" else None,
            "temporal_persistence_count": persistence_count,
            "temporal_run_window_count": run_window_count,
            "temporal_warmup_window_count": warmup_window_count,
            "temporal_event_id": event_id,
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
                "temporal_status",
                "temporal_reason",
                "temporal_context",
                "temporal_observed_value",
                "temporal_baseline_value",
                "temporal_ewma_value",
            ),
            scored_window_count=0,
            metadata={
                "configuration": self.config.to_dict(),
                "temporal_state_scope": "within-session only; resets on context changes, window gaps and every infer() call",
            },
        )


@dataclass(slots=True)
class _TemporalState:
    context: str | None = None
    baseline_values: list[float] = None  # type: ignore[assignment]
    ewma: float | None = None
    persistence_count: int = 0
    run_window_count: int = 0
    last_start_frame: int | None = None

    def __post_init__(self) -> None:
        if self.baseline_values is None:
            self.baseline_values = []


def _ewma(previous: float, observed: float, alpha: float) -> float:
    return alpha * observed + (1.0 - alpha) * previous


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


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
