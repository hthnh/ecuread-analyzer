from __future__ import annotations

from app.evaluation.contextual_review import (
    build_decision,
    intervals_overlap,
    is_context_transition_window,
    persisted_baseline_events,
)


def test_intervals_overlap_requires_actual_window_overlap() -> None:
    assert intervals_overlap(10, 12, 12, 14) is True
    assert intervals_overlap(10, 12, 13, 14) is False


def test_persisted_baseline_events_match_session_and_window_overlap() -> None:
    baseline = [
        {"event_id": "a", "session_id": "s1", "start_window_index": 10, "end_window_index": 12},
        {"event_id": "b", "session_id": "s2", "start_window_index": 4, "end_window_index": 5},
    ]
    variant = [
        {"session_id": "s1", "start_window_index": 11, "end_window_index": 13},
        {"session_id": "s2", "start_window_index": 6, "end_window_index": 8},
    ]

    assert persisted_baseline_events(baseline, variant) == {"a"}


def test_context_transition_window_uses_adjacent_context_changes() -> None:
    sequence = ["idle_closed_throttle", "idle_closed_throttle", "low_load_running", "low_load_running"]

    assert is_context_transition_window(sequence, 0) is False
    assert is_context_transition_window(sequence, 1) is True
    assert is_context_transition_window(sequence, 2) is True
    assert is_context_transition_window(sequence, 3) is False


def test_decision_revises_when_reference_or_boundary_artifacts_dominate() -> None:
    decision = build_decision(
        {"total_disagreement_windows": 10},
        {"contextual_event_count": 3},
        {"stability_interpretation": {"unstable_under_leave_one_out": True}},
        {"transition_artifact_assessment": {"context_boundaries_dominate_contextual_anomalies": False}},
    )

    assert decision["recommendation"] == "revise"
    assert decision["answers"]["reference_or_boundary_artifacts_dominate_results"] is True
