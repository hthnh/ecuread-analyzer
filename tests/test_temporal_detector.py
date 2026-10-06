from __future__ import annotations

import pandas as pd

from app.ml.harness import ModelHarness
from app.ml.temporal_detector import TemporalBatteryShiftConfig, TemporalBatteryShiftDetector


def _frame(values: list[float], *, context: str = "idle") -> pd.DataFrame:
    if context == "idle":
        rpm = [1400.0] * len(values)
        tps_raw = [0.0] * len(values)
        tps_voltage = [0.488281] * len(values)
    elif context == "low":
        rpm = [3200.0] * len(values)
        tps_raw = [18.0] * len(values)
        tps_voltage = [0.95] * len(values)
    else:
        rpm = [8000.0] * len(values)
        tps_raw = [140.0] * len(values)
        tps_voltage = [4.0] * len(values)
    return pd.DataFrame(
        {
            "window_index": list(range(len(values))),
            "start_frame_index": [index * 10 for index in range(len(values))],
            "end_frame_index": [index * 10 + 49 for index in range(len(values))],
            "rpm_median": rpm,
            "tps_raw_median": tps_raw,
            "tps_voltage_median": tps_voltage,
            "battery_voltage_median": values,
        }
    )


def _detector() -> TemporalBatteryShiftDetector:
    return TemporalBatteryShiftDetector(
        TemporalBatteryShiftConfig(
            ewma_alpha=1.0,
            min_warmup_windows=3,
            min_persistence_windows=2,
            shift_threshold_volts=0.4,
        )
    )


def test_temporal_detector_requires_warmup_and_persistence() -> None:
    detector = _detector()
    features = _frame([14.5, 14.5, 14.5, 14.0, 14.5, 14.0, 14.0])

    result = detector.infer(features, context=None)  # type: ignore[arg-type]

    assert result.status == "ok"
    assert result.scored_window_count == 4
    assert result.anomaly_window_count == 1
    assert result.windows["temporal_status"].tolist() == [
        "warming",
        "warming",
        "warming",
        "applicable",
        "applicable",
        "applicable",
        "applicable",
    ]
    assert result.windows["prediction"].tolist() == [None, None, None, 1, 1, 1, -1]
    assert result.windows["is_anomaly"].tolist() == [None, None, None, False, False, False, True]
    assert result.windows["temporal_persistence_count"].tolist() == [0, 0, 0, 1, 0, 1, 2]


def test_temporal_detector_resets_on_context_change() -> None:
    detector = _detector()
    idle = _frame([14.5, 14.5, 14.5, 14.0], context="idle")
    low = _frame([14.0, 14.0, 14.0, 13.5], context="low")
    low["window_index"] += len(idle)
    low["start_frame_index"] += len(idle) * 10
    low["end_frame_index"] += len(idle) * 10
    features = pd.concat([idle, low], ignore_index=True)

    result = detector.infer(features, context=None)  # type: ignore[arg-type]

    assert result.windows["temporal_context"].tolist()[:4] == ["idle_closed_throttle"] * 4
    assert result.windows["temporal_context"].tolist()[4:] == ["low_load_running"] * 4
    assert result.windows["temporal_status"].tolist()[4:7] == ["warming", "warming", "warming"]
    assert result.windows["is_anomaly"].tolist()[-1] is False


def test_temporal_detector_resets_between_infer_calls() -> None:
    detector = _detector()

    first = detector.infer(_frame([14.5, 14.5, 14.5, 14.0, 14.0]), context=None)  # type: ignore[arg-type]
    second = detector.infer(_frame([14.0, 14.0]), context=None)  # type: ignore[arg-type]

    assert first.status == "ok"
    assert second.status == "skipped"
    assert second.reason == "insufficient_temporal_history"


def test_temporal_detector_missing_features_are_harness_skips() -> None:
    detector = _detector()
    features = _frame([14.5, 14.5, 14.5]).drop(columns=["battery_voltage_median"])

    result = ModelHarness([detector]).run(features).results[0]

    assert result.status == "skipped"
    assert result.reason == "missing_features"
    assert result.missing_features == ("battery_voltage_median",)
