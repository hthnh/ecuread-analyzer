from __future__ import annotations

import pandas as pd

from app.ml.harness import ModelHarness
from app.ml.rpm_stability_detector import RpmStabilityConfig, RpmStabilityDetector


def _frame(
    rpm_std_values: list[float],
    *,
    rpm_median: float = 1400.0,
    tps_raw: float = 0.0,
    tps_voltage: float = 0.488281,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "window_index": list(range(len(rpm_std_values))),
            "start_frame_index": [index * 10 for index in range(len(rpm_std_values))],
            "end_frame_index": [index * 10 + 49 for index in range(len(rpm_std_values))],
            "rpm_median": [rpm_median] * len(rpm_std_values),
            "rpm_std": rpm_std_values,
            "rpm_range": [value * 4.0 for value in rpm_std_values],
            "rpm_mean_absolute_diff": [value / 5.0 for value in rpm_std_values],
            "tps_raw_median": [tps_raw] * len(rpm_std_values),
            "tps_voltage_median": [tps_voltage] * len(rpm_std_values),
        }
    )


def _detector() -> RpmStabilityDetector:
    return RpmStabilityDetector(
        RpmStabilityConfig(
            min_warmup_windows=3,
            min_persistence_windows=2,
            ewma_alpha=1.0,
            robust_z_threshold=2.0,
            min_dispersion_increase_rpm=100.0,
            dispersion_scale_floor_rpm=50.0,
        )
    )


def test_rpm_stability_detector_requires_warmup_and_persistence() -> None:
    detector = _detector()
    features = _frame([40.0, 45.0, 50.0, 170.0, 45.0, 170.0, 180.0])

    result = detector.infer(features, context=None)  # type: ignore[arg-type]

    assert result.status == "ok"
    assert result.scored_window_count == 4
    assert result.anomaly_window_count == 1
    assert result.windows["rpm_stability_status"].tolist() == [
        "warming",
        "warming",
        "warming",
        "applicable",
        "applicable",
        "applicable",
        "applicable",
    ]
    assert result.windows["prediction"].tolist() == [None, None, None, 1, 1, 1, -1]
    assert result.windows["rpm_stability_persistence_count"].tolist() == [0, 0, 0, 1, 0, 1, 2]


def test_rpm_stability_detector_skips_open_throttle_and_rebuilds_history() -> None:
    detector = _detector()
    first = _frame([40.0, 45.0, 50.0, 180.0])
    open_throttle = _frame([200.0], tps_raw=40.0, tps_voltage=1.2)
    second = _frame([45.0, 45.0, 45.0, 180.0])
    second["window_index"] += len(first) + len(open_throttle)
    second["start_frame_index"] += (len(first) + len(open_throttle)) * 10
    second["end_frame_index"] += (len(first) + len(open_throttle)) * 10
    features = pd.concat([first, open_throttle, second], ignore_index=True)

    result = detector.infer(features, context=None)  # type: ignore[arg-type]

    assert result.windows["rpm_stability_status"].tolist()[4] == "skipped"
    assert result.windows["rpm_stability_status"].tolist()[5:8] == ["warming", "warming", "warming"]
    assert result.windows["is_anomaly"].tolist()[-1] is False


def test_rpm_stability_detector_resets_between_sessions() -> None:
    detector = _detector()

    first = detector.infer(_frame([40.0, 45.0, 50.0, 180.0, 180.0]), context=None)  # type: ignore[arg-type]
    second = detector.infer(_frame([180.0, 180.0]), context=None)  # type: ignore[arg-type]

    assert first.status == "ok"
    assert second.status == "skipped"
    assert second.reason == "insufficient_temporal_history"


def test_rpm_stability_missing_features_are_harness_skips() -> None:
    detector = _detector()
    features = _frame([40.0, 45.0, 50.0]).drop(columns=["rpm_std"])

    result = ModelHarness([detector]).run(features).results[0]

    assert result.status == "skipped"
    assert result.reason == "missing_features"
    assert result.missing_features == ("rpm_std",)
