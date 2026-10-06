from __future__ import annotations

from app.evaluation.rca_surrogate_validation import (
    build_candidate_v2_catalog,
    build_case_surrogate_validation,
    build_counterexample_report,
    build_decision_report,
    build_rule_grounding_matrix,
    build_structural_verification_report,
    evidence_source_registry,
)


def _contract() -> dict:
    return {
        "rules": [
            {"rule_id": "rca.iforest.multivariate_pattern", "description": "IF outside reference"},
            {"rule_id": "rca.electrical.contextual_voltage_deviation", "description": "Voltage deviation"},
            {"rule_id": "rca.operating_state.throttle_related_pattern", "description": "RPM/TPS pattern"},
            {"rule_id": "rca.data_quality.constant_signal_pattern", "description": "Constant signal"},
            {"rule_id": "rca.detectors.disagreement_preservation", "description": "Disagreement"},
            {"rule_id": "rca.coverage.insufficient_evidence", "description": "Coverage"},
            {"rule_id": "rca.no_evidence.no_hypothesis", "description": "No evidence"},
        ]
    }


def _h7_review() -> dict:
    return {
        "summary": {
            "rule_fire_counts": {
                "rca.iforest.multivariate_pattern": 1,
                "rca.operating_state.throttle_related_pattern": 1,
                "rca.data_quality.constant_signal_pattern": 1,
            }
        }
    }


def _dataset() -> dict:
    return {
        "coverage": {
            "contract_rule_ids": [rule["rule_id"] for rule in _contract()["rules"]],
        },
        "cases": [
            {
                "case_id": "case-1",
                "source_review_item_id": "h7-rca-review-0001",
                "selection_tags": {
                    "source_validation_region_type": "isolation_forest_only_event",
                    "rca_state": "hypotheses_generated",
                    "rule_ids_represented": [
                        "rca.iforest.multivariate_pattern",
                        "rca.operating_state.throttle_related_pattern",
                        "rca.data_quality.constant_signal_pattern",
                    ],
                },
                "review_presentation": {
                    "observable_evidence": {
                        "telemetry": {
                            "rpm_median": 1800.0,
                            "tps_raw_median": 0.0,
                            "battery_voltage_min": 12.8,
                        },
                        "unusual_feature_evidence": {
                            "isolation_forest_most_unusual_features": "rpm_median;tps_raw_min;battery_voltage_constant_signal",
                        },
                    }
                },
                "provenance": {
                    "validation_label": {
                        "label": "inconclusive",
                        "source_label": None,
                    },
                    "source_event": {
                        "evidence_state": "detector_disagreement",
                        "duration_ms": 2500.0,
                    },
                },
                "rule_provenance": {
                    "hypotheses": [
                        {
                            "hypothesis_review_id": "hyp-1",
                            "hypothesis_id": "h.multivariate_pattern_outside_reference",
                            "source_rule_id": "rca.iforest.multivariate_pattern",
                        },
                        {
                            "hypothesis_review_id": "hyp-2",
                            "hypothesis_id": "h.operating_state_or_throttle_input_variation",
                            "source_rule_id": "rca.operating_state.throttle_related_pattern",
                        },
                        {
                            "hypothesis_review_id": "hyp-3",
                            "hypothesis_id": "h.possible_acquisition_or_constant_signal_artifact",
                            "source_rule_id": "rca.data_quality.constant_signal_pattern",
                        },
                    ]
                },
            }
        ],
    }


def test_source_registry_has_tiers_and_no_synthetic_expert_policy() -> None:
    registry = evidence_source_registry()

    assert registry["policy"]["long_copyrighted_excerpts_stored"] is False
    assert registry["policy"]["expert_judgments_populated"] is False
    assert {"Tier A", "Tier B", "Tier C", "Tier D"}.issubset(
        {source["source_tier"] for source in registry["sources"]}
    )
    assert any(source["source_id"] == "src.iso.13379-1-2025" for source in registry["sources"])


def test_grounding_matrix_reclassifies_iforest_and_constant_signal_claims() -> None:
    counterexamples = build_counterexample_report(_dataset())
    matrix = build_rule_grounding_matrix(_contract(), _h7_review(), _dataset(), counterexamples)
    by_rule = {rule["rule_id"]: rule for rule in matrix["rules"]}

    assert by_rule["rca.iforest.multivariate_pattern"]["maximum_defensible_claim_level"] == "observation"
    assert by_rule["rca.iforest.multivariate_pattern"]["provisional_decision"] == "revise"
    assert by_rule["rca.data_quality.constant_signal_pattern"]["maximum_defensible_claim_level"] == "observation"
    assert by_rule["rca.data_quality.constant_signal_pattern"]["provisional_decision"] == "revise"
    assert by_rule["rca.electrical.contextual_voltage_deviation"]["maximum_defensible_claim_level"] == "symptom"


def test_counterexamples_flag_constant_signal_without_integrity_evidence() -> None:
    report = build_counterexample_report(_dataset())

    assert report["summary"]["rule_counterexample_counts"]["rca.data_quality.constant_signal_pattern"] == 1
    assert report["by_rule"]["rca.data_quality.constant_signal_pattern"][0]["reason"].startswith(
        "Constant-signal feature appears without independent"
    )


def test_case_surrogate_validation_uses_separate_namespace_and_does_not_touch_expert_fields() -> None:
    counterexamples = build_counterexample_report(_dataset())
    matrix = build_rule_grounding_matrix(_contract(), _h7_review(), _dataset(), counterexamples)
    case_review = build_case_surrogate_validation(_dataset(), matrix, counterexamples)
    case = case_review["cases"][0]

    assert case_review["namespace"] == "surrogate_validation"
    assert case["surrogate_validation"]["expert_judgment_populated"] is False
    assert case["surrogate_validation"]["expert_rating_populated"] is False
    assert case["surrogate_validation"]["reviewer_id_populated"] is False
    assert case["surrogate_validation"]["hypotheses"][0]["maximum_supported_claim_level"] == "observation"


def test_candidate_v2_is_inactive_and_blocks_overclaims() -> None:
    counterexamples = build_counterexample_report(_dataset())
    matrix = build_rule_grounding_matrix(_contract(), _h7_review(), _dataset(), counterexamples)
    candidate = build_candidate_v2_catalog(matrix)
    by_rule = {rule["rule_id"]: rule for rule in candidate["candidate_rules"]}

    assert candidate["replaces_h7_v1"] is False
    assert candidate["activation_status"] == "candidate_only_not_registered"
    assert "acquisition failure from constant signal alone" in by_rule[
        "rca.v2.data_quality.constant_signal_integrity_gate"
    ]["blocked_claims"]
    assert "throttle fault" in by_rule["rca.v2.operating_state.rpm_tps_observation"]["blocked_claims"]


def test_structural_and_decision_report_request_revision_without_rejecting_candidate() -> None:
    counterexamples = build_counterexample_report(_dataset())
    matrix = build_rule_grounding_matrix(_contract(), _h7_review(), _dataset(), counterexamples)
    case_review = build_case_surrogate_validation(_dataset(), matrix, counterexamples)
    candidate = build_candidate_v2_catalog(matrix)
    structural = build_structural_verification_report(candidate, matrix)
    decision = build_decision_report(matrix, counterexamples, structural, case_review)

    assert structural["summary"]["blocking_error_count"] == 0
    assert structural["summary"]["warning_count"] == 0
    assert decision["overall_recommendation"] == "revise"
    assert decision["expert_review_fields_modified"] is False
    assert decision["candidate_v2_replaces_h7_v1"] is False
