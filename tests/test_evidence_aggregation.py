from __future__ import annotations

import pandas as pd

from app.ml.evidence_aggregation import (
    ACTIVE_H5_DETECTOR_IDS,
    aggregate_window,
    build_aggregate_events,
    evidence_state_for,
    summarize_aggregate_windows,
)


def _finding(detector_id: str, finding: str, *, reason: str | None = None) -> dict:
    applicable = finding in {"positive", "negative"}
    available = finding != "unavailable"
    return {
        "detector_id": detector_id,
        "model_version": "v1",
        "execution_status": "ok" if available else "skipped",
        "available": available,
        "applicable": applicable,
        "applicability_reason": reason,
        "finding": finding,
        "prediction": -1 if finding == "positive" else 1 if finding == "negative" else None,
        "is_anomaly": True if finding == "positive" else False if finding == "negative" else None,
        "detector_local_score": {"value": 1.0, "direction": "higher_is_more_anomalous"},
        "detector_local_threshold": {"value": 2.0},
        "evidence": {},
        "missing_features": [],
        "error": None,
    }


def _window(number: int, findings: list[dict]) -> dict:
    return aggregate_window(
        session_id="s1",
        window_identity={
            "window_number": number,
            "window_index": number,
            "start_frame_index": number * 10,
            "end_frame_index": number * 10 + 9,
            "start_time_ms": float(number * 1000),
            "end_time_ms": float(number * 1000 + 900),
            "duration_ms": 900.0,
        },
        detector_findings=findings,
        telemetry={"rpm_median": 1200.0},
    )


def test_evidence_state_semantics_keep_no_evidence_distinct_from_health() -> None:
    assert evidence_state_for(0, 0, 0) == "insufficient_coverage"
    assert evidence_state_for(1, 0, 1) == "no_evidence"
    assert evidence_state_for(1, 1, 0) == "single_detector_evidence"
    assert evidence_state_for(2, 2, 0) == "multiple_detector_evidence"
    assert evidence_state_for(2, 1, 1) == "detector_disagreement"

    row = _window(
        0,
        [
            _finding("isolation_forest", "negative"),
            _finding("contextual_battery_voltage", "not_applicable", reason="context_not_modeled"),
        ],
    )

    assert row["aggregate_evidence_status"]["state"] == "no_evidence"
    assert row["aggregate_evidence_status"]["no_evidence_is_verified_healthy"] is False
    assert row["coverage"]["status"] == "partial"
    assert row["counts"]["negative_finding_count"] == 1
    assert row["counts"]["not_applicable_detector_count"] == 1


def test_skipped_detector_never_counts_as_negative_vote() -> None:
    row = _window(
        0,
        [
            _finding("isolation_forest", "positive"),
            _finding("contextual_battery_voltage", "unavailable", reason="missing_features"),
        ],
    )

    assert row["aggregate_evidence_status"]["state"] == "single_detector_evidence"
    assert row["counts"]["negative_finding_count"] == 0
    assert row["detector_ids"]["unavailable"] == ["contextual_battery_voltage"]


def test_event_aggregation_separates_finding_and_coverage_regions() -> None:
    rows = [
        _window(0, [_finding("isolation_forest", "positive"), _finding("contextual_battery_voltage", "negative")]),
        _window(1, [_finding("isolation_forest", "positive"), _finding("contextual_battery_voltage", "negative")]),
        _window(2, [_finding("isolation_forest", "positive"), _finding("contextual_battery_voltage", "positive")]),
        _window(3, [_finding("isolation_forest", "negative"), _finding("contextual_battery_voltage", "not_applicable")]),
    ]

    events = build_aggregate_events(rows)

    assert [event["event_type"] for event in events["finding_events"]] == [
        "disagreement_region",
        "overlapping_detector_event",
    ]
    assert len(events["coverage_gap_regions"]) == 1
    assert events["coverage_gap_regions"][0]["not_applicable_detector_ids"] == ["contextual_battery_voltage"]


def test_summary_counts_detector_specific_and_disagreement_windows() -> None:
    rows = [
        _window(0, [_finding("isolation_forest", "positive"), _finding("contextual_battery_voltage", "not_applicable")]),
        _window(1, [_finding("isolation_forest", "negative"), _finding("contextual_battery_voltage", "positive")]),
        _window(2, [_finding("isolation_forest", "positive"), _finding("contextual_battery_voltage", "positive")]),
    ]

    summary = summarize_aggregate_windows(rows)

    assert summary["if_only_finding_windows"] == 1
    assert summary["contextual_only_finding_windows"] == 1
    assert summary["disagreement_windows"] == 1
    assert summary["overlapping_finding_windows"] == 1
    assert summary["only_one_detector_applicable_windows"] == 1


def test_h5_active_set_excludes_rejected_temporal_candidates() -> None:
    assert ACTIVE_H5_DETECTOR_IDS == ("isolation_forest", "contextual_battery_voltage")
