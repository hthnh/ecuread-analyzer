from __future__ import annotations

import pytest

from scripts.verify_long_session_compatibility import expected_window_bounds, pipeline_checks


def summary(*, forest_status="ok", contextual_status="ok", contextual_reason=None, history="available"):
    return {
        "window_count": 1793,
        "detectors": [{"detector_id": "isolation_forest", "status": forest_status, "scored_window_count": 1793}],
        "diagnostic_evidence": {
            "capability_status": {"component_status": {
                "evidence_aggregation": "available", "rca_v2": "available", "historical_evidence": history,
            }},
            "detectors": {"contextual_battery_voltage": {
                "execution_status": contextual_status, "skip_or_failure_reason": contextual_reason,
                "evidence": {"metadata": {"applicable_window_count": 1785, "skipped_window_count": 8}},
            }},
            "aggregate": {"window_summary": {"window_count": 1793}},
        },
    }


def test_full_long_session_windows_cross_old_record_limit():
    bounds = expected_window_bounds(17976, 50, 10)
    assert len(bounds) == 1793
    assert bounds[0] == (0, 49)
    assert bounds[-1] == (17920, 17969)
    assert 17976 - bounds[-1][1] - 1 == 6
    for boundary in (5000, 10000, 15000):
        assert any(start < boundary <= end for start, end in bounds)


@pytest.mark.parametrize("count, expected", [(0, 0), (49, 0), (50, 1), (5000, 496), (5001, 496), (5010, 497)])
def test_full_windows_only(count, expected):
    assert len(expected_window_bounds(count, 50, 10)) == expected


@pytest.mark.parametrize("size, step", [(0, 10), (50, 0), (-1, 10)])
def test_window_settings_must_be_positive(size, step):
    with pytest.raises(ValueError):
        expected_window_bounds(17976, size, step)


def test_complete_shadow_pipeline_passes_checks():
    report = {}
    pipeline_checks(report, summary(), 1793)
    assert all(c["passed"] for c in report["checks"])


@pytest.mark.parametrize("changes", [
    {"forest_status": "skipped"},
    {"contextual_status": "failed"},
    {"contextual_status": "skipped", "contextual_reason": "contextual_reference_unavailable"},
    {"history": "unavailable"},
])
def test_no_success_from_soft_failure_or_missing_reference(changes):
    report = {}
    pipeline_checks(report, summary(**changes), 1793)
    assert any(not c["passed"] for c in report["checks"])


def test_explicitly_unmodeled_context_is_permitted_but_counted():
    payload = summary(contextual_status="skipped", contextual_reason="no_applicable_context")
    payload["diagnostic_evidence"]["detectors"]["contextual_battery_voltage"]["evidence"]["metadata"].update(
        applicable_window_count=0, skipped_window_count=1793,
    )
    report = {}
    pipeline_checks(report, payload, 1793)
    assert all(c["passed"] for c in report["checks"])


def test_incomplete_aggregation_cannot_pass():
    payload = summary()
    payload["diagnostic_evidence"]["aggregate"]["window_summary"]["window_count"] = 496
    report = {}
    pipeline_checks(report, payload, 1793)
    assert any(c["check"] == "all_windows_aggregated" and not c["passed"] for c in report["checks"])
