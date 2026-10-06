from __future__ import annotations

from app.evaluation.temporal_candidate_revision import detector_overlap, event_persists, select_temporal_candidate


def _benchmark(
    *,
    rpm_core3_only: int,
    rpm_applicable_windows: int = 10,
) -> dict:
    return {
        "summary": {
            "rpm_event_overlap": {
                "rpm_event_count": 1,
                "core3_only_event_count": rpm_core3_only,
            },
            "rpm_candidate_coverage": {
                "applicable_window_count": rpm_applicable_windows,
                "event_count": 1,
            },
        }
    }


def _review(*, event_count: int = 1, categories: list[str] | None = None) -> dict:
    return {
        "rpm_event_count": event_count,
        "representative_categories": {},
        "items": [{"categories": categories or ["sustained RPM variability increase"]} for _ in range(event_count)],
    }


def _sensitivity(*, sensitive: bool = False) -> dict:
    return {
        "sensitivity_interpretation": {
            "sensitive_to_reasonable_parameter_changes": sensitive,
        }
    }


def test_detector_overlap_counts_named_detector_anomalies_only() -> None:
    rows = [
        {
            "window_number": 1,
            "detectors": {
                "temporal_rpm_stability": {"status": "ok", "is_anomaly": True},
                "temporal_battery_shift": {"status": "ok", "is_anomaly": False},
            },
        },
        {
            "window_number": 2,
            "detectors": {
                "temporal_rpm_stability": {"status": "skipped", "is_anomaly": True},
                "temporal_battery_shift": {"status": "ok", "is_anomaly": True},
            },
        },
    ]

    event = {"start_window_index": 1, "end_window_index": 2}

    assert detector_overlap(rows, event, "temporal_rpm_stability") == 1
    assert detector_overlap(rows, event, "temporal_battery_shift") == 1


def test_event_persists_by_session_and_window_overlap() -> None:
    event = {"session_id": "s1", "start_window_index": 10, "end_window_index": 12}
    candidates = [
        {"session_id": "s1", "start_window_index": 12, "end_window_index": 14},
        {"session_id": "s2", "start_window_index": 10, "end_window_index": 12},
    ]

    assert event_persists(event, candidates) is True


def test_selection_advances_rpm_when_distinct_stable_and_feasible() -> None:
    decision = select_temporal_candidate(
        {"available": True, "event_count": 1, "core3_only_event_count": 0, "parameter_sensitive": False},
        _benchmark(rpm_core3_only=1),
        _review(event_count=1),
        _sensitivity(sensitive=False),
        {"feasible": True},
    )

    assert decision["selection"] == "rpm_candidate_advance"
    assert decision["production_registration_changed"] is False


def test_selection_rejects_when_rpm_is_artifact_dominated_and_battery_is_not_distinct() -> None:
    decision = select_temporal_candidate(
        {"available": True, "event_count": 1, "core3_only_event_count": 0, "parameter_sensitive": False},
        _benchmark(rpm_core3_only=1),
        _review(event_count=2, categories=["transient"]),
        _sensitivity(sensitive=False),
        {"feasible": True},
    )

    assert decision["selection"] == "both_reject"


def test_selection_retains_battery_when_rpm_is_sensitive() -> None:
    decision = select_temporal_candidate(
        {"available": True, "event_count": 2, "core3_only_event_count": 1, "parameter_sensitive": False},
        _benchmark(rpm_core3_only=1),
        _review(event_count=1),
        _sensitivity(sensitive=True),
        {"feasible": True},
    )

    assert decision["selection"] == "battery_candidate_retain"


def test_selection_reports_insufficient_data_when_target_not_feasible() -> None:
    decision = select_temporal_candidate(
        {"available": False},
        _benchmark(rpm_core3_only=0, rpm_applicable_windows=0),
        _review(event_count=0),
        _sensitivity(sensitive=False),
        {"feasible": False},
    )

    assert decision["selection"] == "insufficient_data"
