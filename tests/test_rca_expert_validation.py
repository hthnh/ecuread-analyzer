from __future__ import annotations

from app.evaluation.rca_expert_validation import (
    build_case_level_evaluation,
    build_decision_report,
    build_expert_validation_dataset,
    build_inter_expert_agreement,
    build_rule_level_evaluation,
    expert_judgment_schema,
)


def _contract() -> dict:
    return {
        "rules": [
            {"rule_id": "rca.iforest.multivariate_pattern", "description": "IF pattern"},
            {"rule_id": "rca.electrical.contextual_voltage_deviation", "description": "Voltage pattern"},
        ]
    }


def _h7_item() -> dict:
    return {
        "review_item_id": "h7-rca-review-0001",
        "source_validation_region_type": "isolation_forest_only_event",
        "source_event": {
            "event_id": "event-1",
            "evidence_state": "single_detector_evidence",
            "coverage_status": "partial",
            "start_time_ms": 0.0,
            "end_time_ms": 2500.0,
            "duration_ms": 2500.0,
            "start_window_index": 0,
            "end_window_index": 0,
            "window_count": 1,
        },
        "telemetry_evidence": {
            "operating_context": "low_load_running",
            "telemetry": {"rpm_median": 2000.0, "battery_voltage_min": 12.8},
            "isolation_forest_most_unusual_features": "battery_voltage_constant_signal",
            "contextual_top_deviation": None,
        },
        "detector_predictions": {
            "positive_detector_ids": ["isolation_forest"],
            "negative_detector_ids": [],
            "unavailable_detector_ids": ["contextual_battery_voltage"],
            "not_applicable_detector_ids": [],
            "by_detector": {
                "isolation_forest": {
                    "model_version": "iforest-test",
                    "execution_status": "ok",
                    "applicable": True,
                    "finding": "positive",
                    "detector_local_score": {"value": -0.6},
                    "detector_local_threshold": {"value": 0.0},
                    "missing_features": [],
                },
                "contextual_battery_voltage": {
                    "model_version": "ctx-test",
                    "execution_status": "skipped",
                    "applicable": False,
                    "finding": "unavailable",
                    "applicability_reason": "missing_features",
                    "missing_features": ["tps_raw_median"],
                },
            },
        },
        "validation_label": {"label": "inconclusive"},
        "rca_result": {
            "source_validation_item_id": "h6-validation-0001",
            "source_h5_review_item_id": "h5-aggregate-event-0001",
            "session_id": "session-1",
            "rca_state": "hypotheses_generated",
            "observed_facts": [{"observation_id": "obs.iforest_multivariate_anomaly"}],
            "recommended_checks": [{"check_id": "check.iforest_feature_review"}],
            "evidence_limitations": [{"limitation_id": "limit.partial_or_missing_detector_coverage"}],
            "applied_rules": [
                {"rule_id": "rca.iforest.multivariate_pattern", "applied": True},
                {"rule_id": "rca.electrical.contextual_voltage_deviation", "applied": False},
            ],
            "hypotheses": [
                {
                    "hypothesis_id": "h.multivariate_pattern_outside_reference",
                    "description": "Outside IF reference distribution.",
                    "status": "possible",
                    "source_rule_id": "rca.iforest.multivariate_pattern",
                    "triggering_evidence": [{"evidence_id": "e.iforest_positive"}],
                    "evidence_against": [],
                    "recommended_checks": [{"check_id": "check.iforest_feature_review"}],
                }
            ],
            "provenance": {"source_provenance": {"path": "data/telemetry/session-1"}},
        },
    }


def _dataset() -> dict:
    return build_expert_validation_dataset({"items": [_h7_item()]}, _contract())


def test_expert_schema_keeps_judgments_independent_from_rca_and_detectors() -> None:
    schema = expert_judgment_schema()

    assert schema["policy"]["detector_prediction_is_ground_truth"] is False
    assert schema["policy"]["rca_hypothesis_is_ground_truth"] is False
    assert schema["policy"]["expert_opinion_is_physical_root_cause"] is False
    assert "reasonable" in schema["hypothesis_ratings"]


def test_review_presentation_masks_rule_ids_but_provenance_preserves_traceability() -> None:
    dataset = _dataset()
    case = dataset["cases"][0]
    hypothesis = case["review_presentation"]["rca_output"]["hypotheses"][0]

    assert "source_rule_id" not in hypothesis
    assert hypothesis["rule_identifier_hidden"] is True
    assert case["rule_provenance"]["hypotheses"][0]["source_rule_id"] == "rca.iforest.multivariate_pattern"
    assert case["provenance"]["source_provenance"]["path"] == "data/telemetry/session-1"


def test_blank_expert_dataset_reports_insufficient_expert_evidence() -> None:
    dataset = _dataset()
    rule_eval = build_rule_level_evaluation(dataset, _contract())
    case_eval = build_case_level_evaluation(dataset)
    agreement = build_inter_expert_agreement(dataset)
    decision = build_decision_report(rule_eval, case_eval, agreement)

    assert rule_eval["summary"]["total_completed_hypothesis_judgments"] == 0
    assert case_eval["summary"]["unreviewed_case_count"] == 1
    assert agreement["computed"] is False
    assert decision["overall_recommendation"] == "insufficient_expert_evidence"


def test_rule_and_case_evaluation_preserve_unsupported_feedback() -> None:
    dataset = _dataset()
    case = dataset["cases"][0]
    hypothesis_review_id = case["rule_provenance"]["hypotheses"][0]["hypothesis_review_id"]
    case["expert_judgments"]["hypothesis_judgments"] = [
        {
            "hypothesis_review_id": hypothesis_review_id,
            "reviewer_id": "expert-a",
            "rating": "unsupported",
            "important_cause_missing": True,
            "evidence_sufficient_for_hypothesis": False,
            "recommended_check_appropriate": False,
            "hypothesis_too_broad": True,
            "hypothesis_too_specific": False,
            "missing_hypothesis": "Check acquisition artifact first.",
            "comments": "Too broad for the evidence.",
        },
        {
            "hypothesis_review_id": hypothesis_review_id,
            "reviewer_id": "expert-b",
            "rating": "unsupported",
            "important_cause_missing": False,
            "evidence_sufficient_for_hypothesis": False,
            "recommended_check_appropriate": False,
            "hypothesis_too_broad": True,
            "hypothesis_too_specific": False,
            "comments": "Not useful enough.",
        },
        {
            "hypothesis_review_id": hypothesis_review_id,
            "reviewer_id": "expert-c",
            "rating": "unsupported",
            "important_cause_missing": False,
            "evidence_sufficient_for_hypothesis": False,
            "recommended_check_appropriate": False,
            "hypothesis_too_broad": True,
            "hypothesis_too_specific": False,
        },
    ]

    rule_eval = build_rule_level_evaluation(dataset, _contract())
    case_eval = build_case_level_evaluation(dataset)

    rule = next(item for item in rule_eval["rules"] if item["rule_id"] == "rca.iforest.multivariate_pattern")
    assert rule["evaluated_count"] == 3
    assert rule["rating_counts"]["unsupported"] == 3
    assert rule["inappropriate_recommended_check_count"] == 3
    assert rule["decision"] == "remove"
    assert case_eval["cases"][0]["produced_unsupported_hypotheses"] is True
    assert case_eval["cases"][0]["omitted_important_hypothesis"] is True
    assert case_eval["cases"][0]["missing_hypotheses"] == ["Check acquisition artifact first."]


def test_inter_expert_agreement_requires_enough_overlap() -> None:
    dataset = _dataset()
    case = dataset["cases"][0]
    hypothesis_review_id = case["rule_provenance"]["hypotheses"][0]["hypothesis_review_id"]
    case["expert_judgments"]["hypothesis_judgments"] = [
        {"hypothesis_review_id": hypothesis_review_id, "reviewer_id": "expert-a", "rating": "reasonable"},
        {"hypothesis_review_id": hypothesis_review_id, "reviewer_id": "expert-b", "rating": "reasonable"},
    ]

    agreement = build_inter_expert_agreement(dataset)

    assert agreement["computed"] is False
    assert agreement["overlapping_hypothesis_count"] == 1


def test_inter_expert_exact_agreement_when_overlap_is_adequate() -> None:
    dataset = _dataset()
    template = dataset["cases"][0]
    dataset["cases"] = []
    for index in range(3):
        case = {**template, "case_id": f"case-{index}"}
        hypothesis_review_id = f"hyp-{index}"
        case["rule_provenance"] = {
            "hypotheses": [
                {
                    "hypothesis_review_id": hypothesis_review_id,
                    "source_rule_id": "rca.iforest.multivariate_pattern",
                    "hypothesis_id": "h.multivariate_pattern_outside_reference",
                }
            ]
        }
        case["expert_judgments"] = {
            "case_judgments": [],
            "hypothesis_judgments": [
                {"hypothesis_review_id": hypothesis_review_id, "reviewer_id": "expert-a", "rating": "reasonable"},
                {"hypothesis_review_id": hypothesis_review_id, "reviewer_id": "expert-b", "rating": "reasonable"},
            ],
        }
        dataset["cases"].append(case)

    agreement = build_inter_expert_agreement(dataset)

    assert agreement["computed"] is True
    assert agreement["exact_all_reviewer_agreement"]["value"] == 1.0
    assert agreement["pairwise_agreement"][0]["exact_agreement_value"] == 1.0
