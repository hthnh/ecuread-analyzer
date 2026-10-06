from __future__ import annotations

from app.evaluation.temporal_shadow import build_decision, event_overlap, event_persists


def test_event_overlap_counts_each_detector_inside_temporal_event() -> None:
    rows = [
        {
            "window_number": 4,
            "detectors": {
                "isolation_forest": {"status": "ok", "is_anomaly": True},
                "contextual_battery_voltage": {"status": "ok", "is_anomaly": False},
                "temporal_battery_shift": {"status": "ok", "is_anomaly": True},
            },
        },
        {
            "window_number": 5,
            "detectors": {
                "isolation_forest": {"status": "ok", "is_anomaly": False},
                "contextual_battery_voltage": {"status": "ok", "is_anomaly": True},
                "temporal_battery_shift": {"status": "ok", "is_anomaly": True},
            },
        },
    ]

    overlap = event_overlap(rows, {"start_window_index": 4, "end_window_index": 5})

    assert overlap["iforest_anomaly_window_count"] == 1
    assert overlap["contextual_anomaly_window_count"] == 1
    assert overlap["temporal_anomaly_window_count"] == 2


def test_event_persists_by_session_and_window_overlap() -> None:
    event = {"session_id": "s1", "start_window_index": 10, "end_window_index": 12}
    candidates = [
        {"session_id": "s1", "start_window_index": 12, "end_window_index": 14},
        {"session_id": "s2", "start_window_index": 10, "end_window_index": 12},
    ]

    assert event_persists(event, candidates) is True


def test_decision_revises_when_parameter_sensitive() -> None:
    decision = build_decision(
        {"feasible": True},
        {
            "summary": {
                "by_detector": {
                    "temporal_battery_shift": {
                        "scored_window_count": 10,
                    }
                },
                "temporal_event_overlap": {
                    "core3_only_event_count": 1,
                },
            }
        },
        {
            "temporal_event_count": 2,
            "items": [],
        },
        {
            "sensitivity_interpretation": {
                "sensitive_to_reasonable_parameter_changes": True,
            }
        },
    )

    assert decision["recommendation"] == "revise"
    assert decision["answers"]["events_stable_under_parameter_variation"] is False
