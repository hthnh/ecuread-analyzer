from __future__ import annotations

import pandas as pd

from app.ml.contextual_detector import (
    CONTEXTUAL_BATTERY_DETECTOR_ID,
    ContextualBatteryVoltageDetector,
    assign_operating_context,
    build_contextual_battery_reference,
)
from app.ml.harness import ModelHarness


def _reference_frame(count: int = 80) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "window_index": list(range(count)),
            "start_frame_index": [index * 10 for index in range(count)],
            "end_frame_index": [index * 10 + 49 for index in range(count)],
            "duration_ms": [2500.0] * count,
            "rpm_median": [1450.0] * count,
            "tps_raw_median": [0.0] * count,
            "tps_voltage_median": [0.488281] * count,
            "battery_voltage_median": [14.5] * count,
            "battery_voltage_mean": [14.5] * count,
            "battery_voltage_min": [14.4] * count,
        }
    )


def test_assign_operating_context_uses_rpm_and_tps_measurements() -> None:
    assert assign_operating_context({"rpm_median": 0, "tps_raw_median": 0, "tps_voltage_median": 0.488}) == (
        "engine_off_or_transition"
    )
    assert assign_operating_context({"rpm_median": 1400, "tps_raw_median": 0, "tps_voltage_median": 0.488}) == (
        "idle_closed_throttle"
    )
    assert assign_operating_context({"rpm_median": 3600, "tps_raw_median": 18, "tps_voltage_median": 0.95}) == (
        "low_load_running"
    )
    assert assign_operating_context({"rpm_median": 6100, "tps_raw_median": 50, "tps_voltage_median": 1.8}) == (
        "mid_load_running"
    )


def test_contextual_detector_scores_applicable_windows_and_skips_unmodeled_contexts() -> None:
    reference = build_contextual_battery_reference(
        {"reference-session": _reference_frame()},
        provenance={"reference_split": "normal_train"},
        min_context_windows=50,
        robust_z_threshold=4.5,
    )
    detector = ContextualBatteryVoltageDetector(reference)
    features = pd.DataFrame(
        [
            {
                "window_index": 0,
                "start_frame_index": 0,
                "end_frame_index": 49,
                "duration_ms": 2500.0,
                "rpm_median": 1450.0,
                "tps_raw_median": 0.0,
                "tps_voltage_median": 0.488281,
                "battery_voltage_median": 14.5,
                "battery_voltage_mean": 14.5,
                "battery_voltage_min": 14.4,
            },
            {
                "window_index": 1,
                "start_frame_index": 50,
                "end_frame_index": 99,
                "duration_ms": 2500.0,
                "rpm_median": 1450.0,
                "tps_raw_median": 0.0,
                "tps_voltage_median": 0.488281,
                "battery_voltage_median": 12.4,
                "battery_voltage_mean": 12.4,
                "battery_voltage_min": 12.3,
            },
            {
                "window_index": 2,
                "start_frame_index": 100,
                "end_frame_index": 149,
                "duration_ms": 2500.0,
                "rpm_median": 8000.0,
                "tps_raw_median": 140.0,
                "tps_voltage_median": 4.0,
                "battery_voltage_median": 14.5,
                "battery_voltage_mean": 14.5,
                "battery_voltage_min": 14.4,
            },
        ]
    )

    result = detector.infer(features, context=None)  # type: ignore[arg-type]

    assert result.identity.detector_id == CONTEXTUAL_BATTERY_DETECTOR_ID
    assert result.status == "ok"
    assert result.scored_window_count == 2
    assert result.anomaly_window_count == 1
    assert result.windows["prediction"].tolist() == [1, -1, None]
    assert result.windows["is_anomaly"].tolist() == [False, True, None]
    assert result.windows["contextual_status"].tolist() == ["ok", "ok", "skipped_context"]
    skipped_payload = result.window_payload(2)
    assert skipped_payload["prediction"] is None
    assert skipped_payload["is_anomaly"] is None
    assert skipped_payload["evidence"]["contextual_status"] == "skipped_context"


def test_contextual_detector_returns_skipped_when_no_context_is_applicable() -> None:
    reference = build_contextual_battery_reference(
        {"reference-session": _reference_frame()},
        provenance={"reference_split": "normal_train"},
        min_context_windows=50,
    )
    detector = ContextualBatteryVoltageDetector(reference)
    features = pd.DataFrame(
        {
            "window_index": [0],
            "rpm_median": [0.0],
            "tps_raw_median": [0.0],
            "tps_voltage_median": [0.488281],
            "battery_voltage_median": [12.0],
            "battery_voltage_mean": [12.0],
            "battery_voltage_min": [11.9],
        }
    )

    result = detector.infer(features, context=None)  # type: ignore[arg-type]

    assert result.status == "skipped"
    assert result.reason == "no_applicable_context"
    assert result.scored_window_count == 0


def test_contextual_detector_missing_features_are_harness_skips() -> None:
    reference = build_contextual_battery_reference(
        {"reference-session": _reference_frame()},
        provenance={"reference_split": "normal_train"},
        min_context_windows=50,
    )
    detector = ContextualBatteryVoltageDetector(reference)
    features = _reference_frame(2).drop(columns=["tps_raw_median"])

    result = ModelHarness([detector]).run(features).results[0]

    assert result.status == "skipped"
    assert result.reason == "missing_features"
    assert result.missing_features == ("tps_raw_median",)
