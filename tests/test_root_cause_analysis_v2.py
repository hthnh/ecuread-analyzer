from __future__ import annotations

from app.evaluation.root_cause_analysis_v2 import build_decision_report, build_v1_v2_comparison
from app.ml.contextual_detector import CONTEXTUAL_BATTERY_DETECTOR_ID
from app.ml.iforest_detector import ISOLATION_FOREST_DETECTOR_ID
from app.ml.root_cause_analysis_v2 import RootCauseAnalyzerV2, rca_v2_contract


def _detector_finding(detector_id: str, finding: str, *, status: str = "ok") -> dict:
    applicable = status == "ok" and finding in {"positive", "negative"}
    return {
        "detector_id": detector_id,
        "model_version": f"{detector_id}-test",
        "execution_status": status,
        "applicable": applicable,
        "applicability_reason": None if applicable else "missing_features",
        "finding": finding,
        "is_anomaly": True if finding == "positive" else False if finding == "negative" else None,
        "prediction": -1 if finding == "positive" else 1 if finding == "negative" else None,
        "detector_local_score": {"value": 8.0},
        "detector_local_threshold": {"value": 4.5},
        "missing_features": [] if applicable else ["tps_raw_median"],
        "error": None,
    }


def _case(
    *,
    iforest_finding: str = "negative",
    contextual_finding: str = "negative",
    iforest_features: str | list[str] = "",
    contextual_top_deviation: dict | None = None,
    evidence_state: str | None = None,
    coverage_status: str = "full",
    h7_hypothesis_count: int = 1,
    additional_causal_evidence: dict | None = None,
    no_evidence: bool = False,
) -> dict:
    by_detector = {
        ISOLATION_FOREST_DETECTOR_ID: _detector_finding(ISOLATION_FOREST_DETECTOR_ID, iforest_finding),
        CONTEXTUAL_BATTERY_DETECTOR_ID: _detector_finding(CONTEXTUAL_BATTERY_DETECTOR_ID, contextual_finding),
    }
    positive = [
        detector_id
        for detector_id, finding in by_detector.items()
        if finding["execution_status"] == "ok" and finding["finding"] == "positive"
    ]
    negative = [
        detector_id
        for detector_id, finding in by_detector.items()
        if finding["execution_status"] == "ok" and finding["finding"] == "negative"
    ]
    if evidence_state is None:
        evidence_state = "no_evidence" if no_evidence or not positive else "single_detector_evidence"
        if positive and negative:
            evidence_state = "detector_disagreement"
    return {
        "case_id": "h7-1-expert-case-test",
        "source_review_item_id": "h7-rca-review-test",
        "selection_tags": {
            "is_no_anomaly_evidence_case": no_evidence,
            "is_insufficient_evidence_case": coverage_status == "none",
        },
        "review_presentation": {
            "observable_evidence": {
                "event_timing": {
                    "start_time_ms": 1000.0,
                    "end_time_ms": 3500.0,
                    "start_window_index": 2,
                    "end_window_index": 3,
                    "window_count": 2,
                },
                "operating_context": "idle_closed_throttle",
                "telemetry": {
                    "rpm_median": 1700.0,
                    "tps_raw_median": 0.0,
                    "tps_voltage_median": 0.48,
                    "battery_voltage_min": 10.9,
                    "battery_voltage_median": 14.1,
                },
                "detector_findings": {
                    "positive_detector_ids": positive,
                    "negative_detector_ids": negative,
                    "unavailable_detector_ids": [],
                    "by_detector": by_detector,
                },
                "unusual_feature_evidence": {
                    "isolation_forest_most_unusual_features": iforest_features,
                    "contextual_top_deviation": contextual_top_deviation,
                },
            },
            "rca_output": {
                "hypotheses": [
                    {"hypothesis_id": f"h7-v1-hypothesis-{index}"}
                    for index in range(h7_hypothesis_count)
                ],
            },
        },
        "provenance": {
            "session_id": "session-test",
            "source_event": {
                "event_id": "event-test",
                "evidence_state": evidence_state,
                "coverage_status": coverage_status,
                "duration_ms": 2500.0,
            },
        },
        "additional_causal_evidence": additional_causal_evidence or {},
    }


def _ids(items: list[dict], key: str) -> set[str]:
    return {item[key] for item in items}


def test_contract_structurally_separates_semantic_sections() -> None:
    contract = rca_v2_contract()

    assert set(contract["semantic_sections"]) == {
        "observations",
        "symptoms",
        "possible_causes",
        "recommended_checks",
    }
    assert contract["policy"]["production_behavior_changed"] is False
    assert contract["policy"]["drive_safe_behavior_changed"] is False
    assert contract["policy"]["empty_possible_cause_list_allowed"] is True


def test_iforest_anomaly_alone_is_observation_without_root_cause_claim() -> None:
    result = RootCauseAnalyzerV2().analyze_case(
        _case(iforest_finding="positive", contextual_finding="negative", iforest_features="battery_voltage_mean")
    )

    assert "obs.v2.iforest_outside_reference" in _ids(result["observations"], "observation_id")
    assert result["symptoms"] == []
    assert result["possible_causes"] == []
    assert "check.v2.iforest_feature_review" in _ids(result["recommended_checks"], "check_id")
    assert "limit.v2.no_possible_cause" in _ids(result["evidence_limitations"], "limitation_id")


def test_constant_signal_alone_is_not_a_data_quality_fault_claim() -> None:
    result = RootCauseAnalyzerV2().analyze_case(
        _case(
            iforest_finding="positive",
            contextual_finding="negative",
            iforest_features="battery_voltage_constant_signal",
        )
    )

    assert "obs.v2.constant_signal_feature" in _ids(result["observations"], "observation_id")
    assert result["possible_causes"] == []
    assert "check.v2.raw_frame_integrity" in _ids(result["recommended_checks"], "check_id")
    assert "check.v2.known_stimulus_response" in _ids(result["recommended_checks"], "check_id")


def test_constant_signal_requires_integrity_evidence_for_data_quality_possible_cause() -> None:
    result = RootCauseAnalyzerV2().analyze_case(
        _case(
            iforest_finding="positive",
            contextual_finding="negative",
            iforest_features="battery_voltage_constant_signal",
            additional_causal_evidence={"repeated_raw_frames": True},
        )
    )

    causes = result["possible_causes"]
    assert [cause["cause_type"] for cause in causes] == ["data_quality_candidate"]
    assert causes[0]["maximum_claim_level"] == "possible_hypothesis"


def test_rpm_tps_pattern_alone_is_not_a_throttle_fault_claim() -> None:
    result = RootCauseAnalyzerV2().analyze_case(
        _case(iforest_finding="positive", contextual_finding="negative", iforest_features="rpm_median;tps_raw_min")
    )

    assert "obs.v2.rpm_tps_feature_involvement" in _ids(result["observations"], "observation_id")
    assert result["possible_causes"] == []
    assert "check.v2.rpm_tps_reproduction" in _ids(result["recommended_checks"], "check_id")
    assert "check.v2.rpm_tps_repeatability" in _ids(result["recommended_checks"], "check_id")


def test_voltage_deviation_remains_symptom_without_electrical_cause() -> None:
    top = {"feature": "battery_voltage_min", "observed": 10.9, "reference_median": 14.2, "robust_z": 8.1}
    result = RootCauseAnalyzerV2().analyze_case(
        _case(
            iforest_finding="negative",
            contextual_finding="positive",
            contextual_top_deviation=top,
        )
    )

    assert "obs.v2.contextual_voltage_deviation" in _ids(result["observations"], "observation_id")
    assert "sym.v2.contextual_supply_voltage_deviation" in _ids(result["symptoms"], "symptom_id")
    assert result["possible_causes"] == []
    assert "check.v2.external_supply_measurement" in _ids(result["recommended_checks"], "check_id")


def test_disagreements_remain_visible_without_consensus_diagnosis() -> None:
    result = RootCauseAnalyzerV2().analyze_case(
        _case(iforest_finding="positive", contextual_finding="negative", iforest_features="battery_voltage_mean")
    )

    assert "obs.v2.detector_disagreement" in _ids(result["observations"], "observation_id")
    assert "check.v2.detector_scope_review" in _ids(result["recommended_checks"], "check_id")
    assert "limit.v2.detector_disagreement" in _ids(result["evidence_limitations"], "limitation_id")
    assert result["possible_causes"] == []


def test_no_evidence_case_produces_no_speculative_diagnosis() -> None:
    result = RootCauseAnalyzerV2().analyze_case(
        _case(iforest_finding="negative", contextual_finding="negative", h7_hypothesis_count=0, no_evidence=True)
    )

    assert result["possible_causes"] == []
    assert "check.v2.no_evidence_boundary" in _ids(result["recommended_checks"], "check_id")
    assert "limit.v2.no_anomaly_evidence" in _ids(result["evidence_limitations"], "limitation_id")


def test_v1_v2_comparison_and_decision_promote_research_layer() -> None:
    cases = [
        _case(iforest_finding="positive", contextual_finding="negative", iforest_features="battery_voltage_mean"),
        _case(iforest_finding="negative", contextual_finding="negative", h7_hypothesis_count=0, no_evidence=True),
    ]
    cases[0]["case_id"] = "case-1"
    cases[1]["case_id"] = "case-2"
    results = [RootCauseAnalyzerV2().analyze_case(case) for case in cases]
    comparison = build_v1_v2_comparison(
        {"summary": {"hypothesis_count": 1, "result_count": 2, "results_insufficient_evidence": 0}},
        {"cases": cases},
        results,
    )
    decision = build_decision_report(comparison, results)

    assert comparison["comparison"]["case_coverage_retained"] == 2
    assert comparison["comparison"]["case_coverage_lost"] == []
    assert comparison["comparison"]["checks_without_causes_supported"] >= 1
    assert decision["recommendation"] == "promote_v2_for_research"
    assert decision["replace_h7_v1_as_default_offline_research_interpretation"] is True
