from __future__ import annotations

import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.evaluation.model_harness_evaluation import EvaluationPaths, default_paths, write_json


HYPOTHESIS_RATINGS = (
    "reasonable",
    "partially_reasonable",
    "unsupported",
    "insufficient_evidence",
)
OVERALL_RECOMMENDATIONS = (
    "validated_for_research_use",
    "retain_with_limitations",
    "revise",
    "reject",
    "insufficient_expert_evidence",
)
RULE_DECISIONS = (
    "retain",
    "revise",
    "remove",
    "insufficient_expert_evidence",
)
MIN_RULE_EXPERT_SAMPLES = 3
MIN_AGREEMENT_OVERLAP = 3
MIN_KAPPA_OVERLAP = 5


def default_h71_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h7_1")


def run_all(paths: EvaluationPaths, *, h7_dir: Path | None = None) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h7_dir = (h7_dir or paths.repo_root / "data" / "evaluation" / "h7").resolve()
    h7_review = read_json(resolved_h7_dir / "rca_review_manifest.json")
    h7_contract = read_json(resolved_h7_dir / "rca_contract.json")

    schema = expert_judgment_schema()
    dataset = build_expert_validation_dataset(h7_review, h7_contract)
    rule_eval = build_rule_level_evaluation(dataset, h7_contract)
    case_eval = build_case_level_evaluation(dataset)
    agreement = build_inter_expert_agreement(dataset)
    decision = build_decision_report(rule_eval, case_eval, agreement)

    outputs = {
        "schema": paths.output_dir / "expert_judgment_schema.json",
        "dataset": paths.output_dir / "expert_validation_dataset.json",
        "rule_level": paths.output_dir / "rule_level_evaluation.json",
        "case_level": paths.output_dir / "case_level_evaluation.json",
        "agreement": paths.output_dir / "inter_expert_agreement.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["schema"], schema)
    write_json(outputs["dataset"], dataset)
    write_json(outputs["rule_level"], rule_eval)
    write_json(outputs["case_level"], case_eval)
    write_json(outputs["agreement"], agreement)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], dataset, rule_eval, case_eval, agreement, decision)
    return outputs


def expert_judgment_schema() -> dict[str, Any]:
    return {
        "schema_version": "rca-expert-judgment-schema-v1",
        "hypothesis_ratings": list(HYPOTHESIS_RATINGS),
        "case_review_fields": {
            "reviewer_id": "stable reviewer identifier",
            "reviewed_at": "timestamp or null",
            "provided_reasonable_hypothesis": "boolean or null",
            "important_cause_missing": "boolean or null",
            "missing_hypothesis": "free text or structured hypothesis proposal",
            "produced_unsupported_hypotheses": "boolean or null",
            "recommended_useful_next_action": "boolean or null",
            "correctly_returned_insufficient_evidence": "boolean or null",
            "comments": "free text",
        },
        "hypothesis_review_fields": {
            "hypothesis_review_id": "identifier from the review case",
            "reviewer_id": "stable reviewer identifier",
            "rating": list(HYPOTHESIS_RATINGS),
            "important_cause_missing": "boolean",
            "evidence_sufficient_for_hypothesis": "boolean",
            "recommended_check_appropriate": "boolean",
            "hypothesis_too_broad": "boolean",
            "hypothesis_too_specific": "boolean",
            "missing_hypothesis": "optional expert-added hypothesis",
            "comments": "free text",
        },
        "policy": {
            "detector_prediction_is_ground_truth": False,
            "rca_hypothesis_is_ground_truth": False,
            "expert_opinion_is_physical_root_cause": False,
            "physical_root_cause_requires_independent_physical_evidence": True,
            "confidence_percentages_allowed": False,
            "multiple_hypotheses_allowed": True,
        },
    }


def build_expert_validation_dataset(h7_review: dict[str, Any], h7_contract: dict[str, Any]) -> dict[str, Any]:
    cases = [expert_validation_case(index, item) for index, item in enumerate(h7_review.get("items", []), start=1)]
    return {
        "schema_version": "rca-expert-validation-dataset-v1",
        "source": {
            "h7_rca_review_manifest": "data/evaluation/h7/rca_review_manifest.json",
            "h7_rca_contract": "data/evaluation/h7/rca_contract.json",
        },
        "selection_policy": {
            "strategy": "exhaustive_h7_review_manifest",
            "avoid_success_only_selection": True,
            "all_h7_review_items_included": True,
            "rule_identifiers_hidden_in_review_presentation": True,
            "event_session_provenance_separated_from_review_presentation": True,
        },
        "review_policy": {
            "hypotheses_are_not_confirmed_causes": True,
            "expert_judgments_stored_separately_from_rca_outputs": True,
            "independent_physical_root_cause_evidence_available": False,
            "mechanical_fault_confirmation_from_expert_opinion_only_allowed": False,
        },
        "coverage": dataset_coverage(cases, h7_contract),
        "case_count": len(cases),
        "cases": cases,
    }


def expert_validation_case(index: int, item: dict[str, Any]) -> dict[str, Any]:
    rca = item.get("rca_result") or {}
    hypotheses = rca.get("hypotheses") or []
    return {
        "case_id": f"h7-1-expert-case-{index:04d}",
        "source_review_item_id": item.get("review_item_id"),
        "source_validation_item_id": rca.get("source_validation_item_id"),
        "source_h5_review_item_id": rca.get("source_h5_review_item_id"),
        "selection_tags": selection_tags(item, rca),
        "review_presentation": {
            "observable_evidence": observable_evidence(item),
            "rca_output": {
                "observations": rca.get("observed_facts", []),
                "hypotheses": [
                    presented_hypothesis(index, hypothesis_index, hypothesis)
                    for hypothesis_index, hypothesis in enumerate(hypotheses, start=1)
                ],
                "recommended_checks": rca.get("recommended_checks", []),
                "evidence_limitations": rca.get("evidence_limitations", []),
            },
            "review_instructions": {
                "do_not_treat_hypotheses_as_confirmed_causes": True,
                "rule_identifiers_hidden_until_after_judgment": True,
                "allow_multiple_plausible_hypotheses": True,
            },
        },
        "provenance": {
            "session_id": item.get("session_id"),
            "source_event": item.get("source_event"),
            "source_validation_region_type": item.get("source_validation_region_type"),
            "validation_label": item.get("validation_label"),
            "source_provenance": (rca.get("provenance") or {}).get("source_provenance"),
        },
        "rule_provenance": {
            "applied_rules": rca.get("applied_rules", []),
            "hypotheses": [
                {
                    "hypothesis_review_id": hypothesis_review_id(index, hypothesis_index),
                    "hypothesis_id": hypothesis.get("hypothesis_id"),
                    "source_rule_id": hypothesis.get("source_rule_id"),
                    "rca_status": hypothesis.get("status"),
                }
                for hypothesis_index, hypothesis in enumerate(hypotheses, start=1)
            ],
        },
        "expert_judgments": {
            "case_judgments": [],
            "hypothesis_judgments": [],
        },
    }


def selection_tags(item: dict[str, Any], rca: dict[str, Any]) -> dict[str, Any]:
    hypotheses = rca.get("hypotheses") or []
    return {
        "source_validation_region_type": item.get("source_validation_region_type"),
        "rca_state": rca.get("rca_state"),
        "detector_evidence_state": (item.get("source_event") or {}).get("evidence_state"),
        "rule_ids_represented": sorted({hypothesis.get("source_rule_id") for hypothesis in hypotheses if hypothesis.get("source_rule_id")}),
        "hypothesis_ids_represented": sorted({hypothesis.get("hypothesis_id") for hypothesis in hypotheses if hypothesis.get("hypothesis_id")}),
        "has_hypotheses": bool(hypotheses),
        "is_insufficient_evidence_case": rca.get("rca_state") == "insufficient_evidence",
        "is_no_anomaly_evidence_case": rca.get("rca_state") == "no_anomaly_evidence",
    }


def observable_evidence(item: dict[str, Any]) -> dict[str, Any]:
    event = item.get("source_event") or {}
    telemetry = item.get("telemetry_evidence") or {}
    return {
        "event_timing": {
            "start_time_ms": event.get("start_time_ms"),
            "end_time_ms": event.get("end_time_ms"),
            "duration_ms": event.get("duration_ms"),
            "start_window_index": event.get("start_window_index"),
            "end_window_index": event.get("end_window_index"),
            "window_count": event.get("window_count"),
        },
        "operating_context": telemetry.get("operating_context"),
        "telemetry": telemetry.get("telemetry"),
        "detector_findings": summarize_detector_findings(item.get("detector_predictions") or {}),
        "unusual_feature_evidence": {
            "isolation_forest_most_unusual_features": telemetry.get("isolation_forest_most_unusual_features"),
            "contextual_top_deviation": telemetry.get("contextual_top_deviation"),
        },
    }


def summarize_detector_findings(detector_predictions: dict[str, Any]) -> dict[str, Any]:
    by_detector = detector_predictions.get("by_detector") or {}
    return {
        "positive_detector_ids": detector_predictions.get("positive_detector_ids", []),
        "negative_detector_ids": detector_predictions.get("negative_detector_ids", []),
        "unavailable_detector_ids": detector_predictions.get("unavailable_detector_ids", []),
        "not_applicable_detector_ids": detector_predictions.get("not_applicable_detector_ids", []),
        "by_detector": {
            detector_id: {
                "model_version": finding.get("model_version"),
                "execution_status": finding.get("execution_status"),
                "applicable": finding.get("applicable"),
                "finding": finding.get("finding"),
                "detector_local_score": finding.get("detector_local_score"),
                "detector_local_threshold": finding.get("detector_local_threshold"),
                "applicability_reason": finding.get("applicability_reason"),
                "missing_features": finding.get("missing_features", []),
            }
            for detector_id, finding in sorted(by_detector.items())
        },
    }


def presented_hypothesis(case_index: int, hypothesis_index: int, hypothesis: dict[str, Any]) -> dict[str, Any]:
    return {
        "hypothesis_review_id": hypothesis_review_id(case_index, hypothesis_index),
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "description": hypothesis.get("description"),
        "rca_status": hypothesis.get("status"),
        "supporting_evidence": hypothesis.get("triggering_evidence", []),
        "contradicting_evidence": hypothesis.get("evidence_against", []),
        "recommended_checks": hypothesis.get("recommended_checks", []),
        "rule_identifier_hidden": True,
        "review_form": blank_hypothesis_review_form(hypothesis_review_id(case_index, hypothesis_index)),
    }


def blank_hypothesis_review_form(hypothesis_id: str) -> dict[str, Any]:
    return {
        "hypothesis_review_id": hypothesis_id,
        "reviewer_id": None,
        "reviewed_at": None,
        "rating": None,
        "allowed_ratings": list(HYPOTHESIS_RATINGS),
        "important_cause_missing": None,
        "evidence_sufficient_for_hypothesis": None,
        "recommended_check_appropriate": None,
        "hypothesis_too_broad": None,
        "hypothesis_too_specific": None,
        "missing_hypothesis": None,
        "comments": None,
    }


def hypothesis_review_id(case_index: int, hypothesis_index: int) -> str:
    return f"h7-1-hypothesis-{case_index:04d}-{hypothesis_index:02d}"


def dataset_coverage(cases: list[dict[str, Any]], h7_contract: dict[str, Any]) -> dict[str, Any]:
    region_counts = Counter(case["selection_tags"]["source_validation_region_type"] for case in cases)
    state_counts = Counter(case["selection_tags"]["rca_state"] for case in cases)
    applied_rule_counts = Counter()
    hypothesis_rule_counts = Counter()
    for case in cases:
        hypothesis_rule_counts.update(case["selection_tags"]["rule_ids_represented"])
        for rule in case["rule_provenance"].get("applied_rules", []):
            if rule.get("applied"):
                applied_rule_counts[rule["rule_id"]] += 1
    contract_rule_ids = [rule["rule_id"] for rule in h7_contract.get("rules", [])]
    return {
        "source_validation_region_type_counts": dict(sorted(region_counts.items())),
        "rca_state_counts": dict(sorted(state_counts.items())),
        "applied_rule_fire_counts": dict(sorted(applied_rule_counts.items())),
        "hypothesis_rule_counts": dict(sorted(hypothesis_rule_counts.items())),
        "contract_rule_ids": contract_rule_ids,
        "all_contract_rules_represented": set(contract_rule_ids).issubset(set(applied_rule_counts)),
        "major_case_families_present": {
            "if_only": region_counts.get("isolation_forest_only_event", 0) > 0,
            "contextual_only": region_counts.get("contextual_only_event", 0) > 0,
            "overlapping": region_counts.get("overlapping_event", 0) > 0,
            "detector_disagreement": region_counts.get("disagreement_region", 0) > 0,
            "insufficient_evidence": state_counts.get("insufficient_evidence", 0) > 0,
            "no_anomaly_evidence": state_counts.get("no_anomaly_evidence", 0) > 0,
        },
    }


def build_rule_level_evaluation(dataset: dict[str, Any], h7_contract: dict[str, Any]) -> dict[str, Any]:
    rule_ids = [rule["rule_id"] for rule in h7_contract.get("rules", [])]
    rule_payloads = {rule["rule_id"]: rule for rule in h7_contract.get("rules", [])}
    judgments = completed_hypothesis_judgments(dataset)
    by_rule: dict[str, list[dict[str, Any]]] = {rule_id: [] for rule_id in rule_ids}
    invalid_judgments = []
    for judgment in judgments:
        rule_id = judgment.get("source_rule_id")
        if rule_id in by_rule:
            by_rule[rule_id].append(judgment)
        else:
            invalid_judgments.append(judgment)

    rules = []
    for rule_id in rule_ids:
        rule_judgments = by_rule[rule_id]
        rating_counts = Counter(judgment["rating"] for judgment in rule_judgments)
        missing_cause_count = sum(1 for judgment in rule_judgments if judgment.get("important_cause_missing") is True)
        inappropriate_check_count = sum(1 for judgment in rule_judgments if judgment.get("recommended_check_appropriate") is False)
        too_broad_count = sum(1 for judgment in rule_judgments if judgment.get("hypothesis_too_broad") is True)
        too_specific_count = sum(1 for judgment in rule_judgments if judgment.get("hypothesis_too_specific") is True)
        evidence_insufficient_count = sum(1 for judgment in rule_judgments if judgment.get("evidence_sufficient_for_hypothesis") is False)
        decision = classify_rule_decision(
            evaluated_count=len(rule_judgments),
            rating_counts=rating_counts,
            missing_cause_count=missing_cause_count,
            inappropriate_check_count=inappropriate_check_count,
            too_broad_count=too_broad_count,
            too_specific_count=too_specific_count,
            evidence_insufficient_count=evidence_insufficient_count,
        )
        rules.append(
            {
                "rule_id": rule_id,
                "rule_description": rule_payloads.get(rule_id, {}).get("description"),
                "evaluated_count": len(rule_judgments),
                "rating_counts": rating_dict(rating_counts),
                "missing_cause_feedback_count": missing_cause_count,
                "inappropriate_recommended_check_count": inappropriate_check_count,
                "hypothesis_too_broad_count": too_broad_count,
                "hypothesis_too_specific_count": too_specific_count,
                "evidence_insufficient_count": evidence_insufficient_count,
                "decision": decision["decision"],
                "decision_reason": decision["reason"],
            }
        )
    return {
        "schema_version": "rca-expert-rule-level-evaluation-v1",
        "policy": {
            "fault_probability_values_computed": False,
            "minimum_samples_for_rule_decision": MIN_RULE_EXPERT_SAMPLES,
            "rules_with_too_few_samples_remain_inconclusive": True,
        },
        "summary": {
            "rule_count": len(rules),
            "total_completed_hypothesis_judgments": len(judgments),
            "invalid_or_unmapped_judgment_count": len(invalid_judgments),
            "rule_decision_counts": dict(sorted(Counter(rule["decision"] for rule in rules).items())),
        },
        "rules": rules,
    }


def classify_rule_decision(
    *,
    evaluated_count: int,
    rating_counts: Counter[str],
    missing_cause_count: int,
    inappropriate_check_count: int,
    too_broad_count: int,
    too_specific_count: int,
    evidence_insufficient_count: int,
) -> dict[str, str]:
    if evaluated_count < MIN_RULE_EXPERT_SAMPLES:
        return {
            "decision": "insufficient_expert_evidence",
            "reason": f"fewer than {MIN_RULE_EXPERT_SAMPLES} completed expert hypothesis judgments",
        }
    unsupported = rating_counts.get("unsupported", 0)
    insufficient = rating_counts.get("insufficient_evidence", 0)
    reasonable = rating_counts.get("reasonable", 0)
    partial = rating_counts.get("partially_reasonable", 0)
    if unsupported / evaluated_count >= 0.5 or inappropriate_check_count / evaluated_count >= 0.5:
        return {"decision": "remove", "reason": "unsupported or inappropriate-check feedback dominates"}
    if (
        unsupported > 0
        or insufficient / evaluated_count >= 0.33
        or missing_cause_count > 0
        or too_broad_count / evaluated_count >= 0.33
        or too_specific_count / evaluated_count >= 0.33
        or evidence_insufficient_count / evaluated_count >= 0.33
    ):
        return {"decision": "revise", "reason": "expert feedback indicates support or utility limitations"}
    if reasonable + partial == evaluated_count:
        return {"decision": "retain", "reason": "all completed expert judgments were reasonable or partially reasonable"}
    return {"decision": "revise", "reason": "mixed expert feedback"}


def build_case_level_evaluation(dataset: dict[str, Any]) -> dict[str, Any]:
    cases = []
    for case in dataset.get("cases", []):
        case_judgments = completed_case_judgments(case)
        hypothesis_judgments = completed_case_hypothesis_judgments(case)
        rating_counts = Counter(judgment["rating"] for judgment in hypothesis_judgments)
        omitted = any(judgment.get("important_cause_missing") is True for judgment in [*case_judgments, *hypothesis_judgments])
        unsupported = rating_counts.get("unsupported", 0) > 0
        useful_next_action = any(judgment.get("recommended_check_appropriate") is True for judgment in hypothesis_judgments) or any(
            judgment.get("recommended_useful_next_action") is True for judgment in case_judgments
        )
        reasonable = rating_counts.get("reasonable", 0) > 0
        insufficient_correct = any(
            judgment.get("correctly_returned_insufficient_evidence") is True for judgment in case_judgments
        )
        status = "unreviewed" if not case_judgments and not hypothesis_judgments else "reviewed"
        cases.append(
            {
                "case_id": case["case_id"],
                "source_review_item_id": case.get("source_review_item_id"),
                "review_status": status,
                "reviewer_ids": sorted({judgment["reviewer_id"] for judgment in [*case_judgments, *hypothesis_judgments]}),
                "hypothesis_judgment_count": len(hypothesis_judgments),
                "case_judgment_count": len(case_judgments),
                "rating_counts": rating_dict(rating_counts),
                "provided_at_least_one_reasonable_hypothesis": reasonable if status == "reviewed" else None,
                "omitted_important_hypothesis": omitted if status == "reviewed" else None,
                "produced_unsupported_hypotheses": unsupported if status == "reviewed" else None,
                "recommended_useful_next_action": useful_next_action if status == "reviewed" else None,
                "correctly_returned_insufficient_evidence": (
                    insufficient_correct if case["selection_tags"].get("is_insufficient_evidence_case") and status == "reviewed" else None
                ),
                "missing_hypotheses": [
                    judgment.get("missing_hypothesis")
                    for judgment in [*case_judgments, *hypothesis_judgments]
                    if judgment.get("missing_hypothesis")
                ],
                "expert_judgments_preserved_separately": {
                    "case_judgments": case_judgments,
                    "hypothesis_judgments": hypothesis_judgments,
                },
            }
        )
    return {
        "schema_version": "rca-expert-case-level-evaluation-v1",
        "policy": {
            "multiple_expert_judgments_preserved": True,
            "expert_disagreement_collapsed": False,
            "expert_opinion_is_physical_ground_truth": False,
        },
        "summary": {
            "case_count": len(cases),
            "reviewed_case_count": sum(1 for case in cases if case["review_status"] == "reviewed"),
            "unreviewed_case_count": sum(1 for case in cases if case["review_status"] == "unreviewed"),
            "cases_with_reasonable_hypothesis": sum(1 for case in cases if case["provided_at_least_one_reasonable_hypothesis"] is True),
            "cases_with_omitted_important_hypothesis": sum(1 for case in cases if case["omitted_important_hypothesis"] is True),
            "cases_with_unsupported_hypotheses": sum(1 for case in cases if case["produced_unsupported_hypotheses"] is True),
            "cases_with_useful_next_action": sum(1 for case in cases if case["recommended_useful_next_action"] is True),
        },
        "cases": cases,
    }


def build_inter_expert_agreement(dataset: dict[str, Any]) -> dict[str, Any]:
    judgments = completed_hypothesis_judgments(dataset)
    by_hypothesis: dict[str, dict[str, str]] = defaultdict(dict)
    for judgment in judgments:
        by_hypothesis[judgment["hypothesis_review_id"]][judgment["reviewer_id"]] = judgment["rating"]
    overlapping = {
        hypothesis_id: reviewer_ratings
        for hypothesis_id, reviewer_ratings in by_hypothesis.items()
        if len(reviewer_ratings) >= 2
    }
    if len(overlapping) < MIN_AGREEMENT_OVERLAP:
        return {
            "schema_version": "rca-expert-inter-expert-agreement-v1",
            "computed": False,
            "reason": f"fewer than {MIN_AGREEMENT_OVERLAP} overlapping hypothesis reviews",
            "overlapping_hypothesis_count": len(overlapping),
            "agreement_measures_physical_ground_truth": False,
        }

    exact_count = sum(1 for ratings in overlapping.values() if len(set(ratings.values())) == 1)
    reviewers = sorted({reviewer for ratings in overlapping.values() for reviewer in ratings})
    pairwise = pairwise_agreement(overlapping, reviewers)
    return {
        "schema_version": "rca-expert-inter-expert-agreement-v1",
        "computed": True,
        "agreement_measures_physical_ground_truth": False,
        "overlapping_hypothesis_count": len(overlapping),
        "reviewer_ids": reviewers,
        "exact_all_reviewer_agreement": {
            "count": exact_count,
            "total": len(overlapping),
            "value": round(exact_count / len(overlapping), 6),
        },
        "pairwise_agreement": pairwise,
        "cohen_kappa": cohen_kappa_if_justified(overlapping, reviewers),
    }


def pairwise_agreement(overlapping: dict[str, dict[str, str]], reviewers: list[str]) -> list[dict[str, Any]]:
    rows = []
    for left, right in itertools.combinations(reviewers, 2):
        comparable = [
            ratings
            for ratings in overlapping.values()
            if left in ratings and right in ratings
        ]
        if not comparable:
            continue
        agree = sum(1 for ratings in comparable if ratings[left] == ratings[right])
        rows.append(
            {
                "reviewer_pair": [left, right],
                "overlap_count": len(comparable),
                "exact_agreement_count": agree,
                "exact_agreement_value": round(agree / len(comparable), 6),
            }
        )
    return rows


def cohen_kappa_if_justified(overlapping: dict[str, dict[str, str]], reviewers: list[str]) -> dict[str, Any]:
    if len(reviewers) != 2:
        return {"computed": False, "reason": "requires exactly two reviewers"}
    left, right = reviewers
    comparable = [
        ratings
        for ratings in overlapping.values()
        if left in ratings and right in ratings
    ]
    if len(comparable) < MIN_KAPPA_OVERLAP:
        return {"computed": False, "reason": f"fewer than {MIN_KAPPA_OVERLAP} overlapping reviewed hypotheses"}
    labels = sorted(set(HYPOTHESIS_RATINGS))
    left_counts = Counter(ratings[left] for ratings in comparable)
    right_counts = Counter(ratings[right] for ratings in comparable)
    observed = sum(1 for ratings in comparable if ratings[left] == ratings[right]) / len(comparable)
    expected = sum((left_counts[label] / len(comparable)) * (right_counts[label] / len(comparable)) for label in labels)
    if math.isclose(1.0, expected):
        return {"computed": False, "reason": "label marginal distribution makes kappa undefined"}
    return {
        "computed": True,
        "reviewer_pair": [left, right],
        "overlap_count": len(comparable),
        "value": round((observed - expected) / (1.0 - expected), 6),
        "interpretation": "agreement consistency only; not physical ground truth",
    }


def build_decision_report(
    rule_eval: dict[str, Any],
    case_eval: dict[str, Any],
    agreement: dict[str, Any],
) -> dict[str, Any]:
    rule_decisions = {rule["rule_id"]: rule["decision"] for rule in rule_eval.get("rules", [])}
    decision_counts = Counter(rule_decisions.values())
    reviewed_cases = case_eval["summary"]["reviewed_case_count"]
    completed_hypothesis_judgments = rule_eval["summary"]["total_completed_hypothesis_judgments"]
    if completed_hypothesis_judgments == 0 or reviewed_cases == 0:
        recommendation = "insufficient_expert_evidence"
    elif decision_counts.get("remove", 0) > 0:
        recommendation = "reject" if decision_counts.get("remove", 0) == len(rule_decisions) else "revise"
    elif decision_counts.get("revise", 0) > 0:
        recommendation = "revise"
    elif decision_counts.get("insufficient_expert_evidence", 0) > 0:
        recommendation = "retain_with_limitations"
    elif decision_counts.get("retain", 0) == len(rule_decisions):
        recommendation = "validated_for_research_use"
    else:
        recommendation = "retain_with_limitations"
    return {
        "schema_version": "rca-expert-validation-decision-v1",
        "overall_recommendation": recommendation,
        "production_behavior_changed": False,
        "drive_safe_behavior_changed": False,
        "per_rule_decisions": rule_decisions,
        "answers": {
            "completed_hypothesis_judgment_count": completed_hypothesis_judgments,
            "reviewed_case_count": reviewed_cases,
            "rule_decision_counts": dict(sorted(decision_counts.items())),
            "inter_expert_agreement_computed": agreement.get("computed", False),
            "independently_confirmed_physical_root_cause_count": 0,
            "detector_evidence_rca_output_expert_judgment_separated": True,
        },
        "limitations": [
            "No completed expert judgments are present until reviewers fill the H7.1 dataset.",
            "Expert judgment validates reasoning quality and utility, not physical root cause by itself.",
            "Rules with insufficient expert samples remain inconclusive.",
        ],
    }


def completed_hypothesis_judgments(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for case in dataset.get("cases", []):
        provenance_by_id = {
            item["hypothesis_review_id"]: item
            for item in case.get("rule_provenance", {}).get("hypotheses", [])
        }
        for judgment in case.get("expert_judgments", {}).get("hypothesis_judgments", []):
            if not is_complete_hypothesis_judgment(judgment):
                continue
            provenance = provenance_by_id.get(judgment["hypothesis_review_id"], {})
            output.append(
                {
                    **judgment,
                    "case_id": case["case_id"],
                    "source_review_item_id": case.get("source_review_item_id"),
                    "source_rule_id": provenance.get("source_rule_id"),
                    "hypothesis_id": provenance.get("hypothesis_id"),
                }
            )
    return output


def completed_case_hypothesis_judgments(case: dict[str, Any]) -> list[dict[str, Any]]:
    dataset = {"cases": [case]}
    return completed_hypothesis_judgments(dataset)


def completed_case_judgments(case: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for judgment in case.get("expert_judgments", {}).get("case_judgments", []):
        if judgment.get("reviewer_id"):
            output.append(judgment)
    return output


def is_complete_hypothesis_judgment(judgment: dict[str, Any]) -> bool:
    return bool(
        judgment.get("reviewer_id")
        and judgment.get("hypothesis_review_id")
        and judgment.get("rating") in HYPOTHESIS_RATINGS
    )


def rating_dict(counter: Counter[str]) -> dict[str, int]:
    return {rating: int(counter.get(rating, 0)) for rating in HYPOTHESIS_RATINGS}


def write_markdown_report(
    path: Path,
    dataset: dict[str, Any],
    rule_eval: dict[str, Any],
    case_eval: dict[str, Any],
    agreement: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    coverage = dataset["coverage"]
    lines = [
        "# H7.1 Expert Validation of RCA Hypotheses",
        "",
        "## Dataset",
        "",
        f"- Review cases: `{dataset['case_count']}`",
        f"- Source region types: `{coverage['source_validation_region_type_counts']}`",
        f"- RCA states: `{coverage['rca_state_counts']}`",
        f"- All contract rules represented: `{coverage['all_contract_rules_represented']}`",
        "- Review presentations hide rule identifiers; provenance keeps them traceable after judgment.",
        "",
        "## Expert Judgments",
        "",
        f"- Completed hypothesis judgments: `{rule_eval['summary']['total_completed_hypothesis_judgments']}`",
        f"- Reviewed cases: `{case_eval['summary']['reviewed_case_count']}`",
        f"- Inter-expert agreement computed: `{agreement.get('computed', False)}`",
        "",
        "## Rule Decisions",
        "",
        f"- Rule decision counts: `{rule_eval['summary']['rule_decision_counts']}`",
        "",
        "## Case Decisions",
        "",
        f"- Case-level summary: `{case_eval['summary']}`",
        "",
        "## Overall Decision",
        "",
        f"- Recommendation: `{decision['overall_recommendation']}`",
        "- No physical root cause is confirmed by RCA or expert judgment alone.",
        "",
        "## Compatibility",
        "",
        "- Production inference remains unchanged.",
        "- DriveSafe behavior was not modified.",
        "- H1-H7 outputs are reused rather than rewritten.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
