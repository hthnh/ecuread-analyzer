from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.evaluation.model_harness_evaluation import EvaluationPaths, default_paths, write_json
from app.ml.root_cause_analysis import RootCauseAnalyzer, rca_contract, summarize_rca_results


EXPERT_REVIEW_LABELS = (
    "reasonable",
    "partially_reasonable",
    "unsupported",
    "important_cause_missing",
    "insufficient_evidence",
)


def default_h7_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h7")


def run_all(paths: EvaluationPaths, *, h6_dir: Path | None = None, h5_dir: Path | None = None) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h6_dir = (h6_dir or paths.repo_root / "data" / "evaluation" / "h6").resolve()
    resolved_h5_dir = (h5_dir or paths.repo_root / "data" / "evaluation" / "h5").resolve()

    h6_manifest = read_json(resolved_h6_dir / "detector_validation_manifest.json")
    h5_review = read_json(resolved_h5_dir / "aggregate_event_review.json") if (resolved_h5_dir / "aggregate_event_review.json").exists() else {}
    contract = rca_contract()
    analyzer = RootCauseAnalyzer()
    results = [analyzer.analyze_item(item) for item in h6_manifest.get("items", [])]
    review_manifest = build_rca_review_manifest(h6_manifest, results, h5_review)
    expert_packet = build_expert_review_packet(review_manifest)
    decision = build_decision_report(contract, review_manifest)

    outputs = {
        "contract": paths.output_dir / "rca_contract.json",
        "review_manifest": paths.output_dir / "rca_review_manifest.json",
        "expert_review": paths.output_dir / "expert_review_packet.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["contract"], contract)
    write_json(outputs["review_manifest"], review_manifest)
    write_json(outputs["expert_review"], expert_packet)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], review_manifest, decision)
    return outputs


def build_rca_review_manifest(
    h6_manifest: dict[str, Any],
    results: list[dict[str, Any]],
    h5_review: dict[str, Any],
) -> dict[str, Any]:
    h6_items = {item.get("validation_item_id"): item for item in h6_manifest.get("items", [])}
    items = []
    for index, result in enumerate(results, start=1):
        source = h6_items.get(result.get("source_validation_item_id"), {})
        items.append(
            {
                "review_item_id": f"h7-rca-review-{index:04d}",
                "source_validation_item_id": result.get("source_validation_item_id"),
                "source_h5_review_item_id": result.get("source_h5_review_item_id"),
                "session_id": result.get("session_id"),
                "source_event": source.get("event"),
                "source_validation_region_type": source.get("validation_region_type"),
                "telemetry_evidence": source.get("telemetry_evidence"),
                "detector_predictions": source.get("detector_outputs"),
                "validation_label": source.get("independent_label"),
                "rca_result": result,
                "review_annotation": empty_review_annotation(),
            }
        )
    summary = summarize_review_items(items)
    return {
        "schema_version": "evidence-based-rca-review-manifest-v1",
        "source": {
            "h6_validation_manifest": "data/evaluation/h6/detector_validation_manifest.json",
            "h5_event_review": "data/evaluation/h5/aggregate_event_review.json",
        },
        "h5_event_review_count": len(h5_review.get("items", [])),
        "policy": {
            "production_behavior_changed": False,
            "drive_safe_behavior_changed": False,
            "llm_used": False,
            "agents_used": False,
            "score_fusion_used": False,
            "physical_fault_claims_made": False,
            "detector_outputs_modified": False,
            "rejected_temporal_candidates_excluded": True,
        },
        "summary": summary,
        "items": items,
    }


def summarize_review_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    rca_results = [item["rca_result"] for item in items]
    rca_summary = summarize_rca_results(rca_results)
    region_counts = Counter(item.get("source_validation_region_type") for item in items)
    label_counts = Counter((item.get("validation_label") or {}).get("label") for item in items)
    hypothesis_items = sum(1 for item in items if item["rca_result"].get("hypotheses"))
    return {
        **rca_summary,
        "review_item_count": len(items),
        "source_validation_region_type_counts": dict(sorted(region_counts.items())),
        "source_validation_label_counts": dict(sorted(label_counts.items())),
        "review_items_with_hypotheses": hypothesis_items,
        "review_items_without_hypotheses": len(items) - hypothesis_items,
    }


def build_expert_review_packet(review_manifest: dict[str, Any]) -> dict[str, Any]:
    items = []
    for index, item in enumerate(review_manifest.get("items", []), start=1):
        rca = item["rca_result"]
        items.append(
            {
                "expert_review_id": f"h7-expert-review-{index:04d}",
                "source_review_item_id": item["review_item_id"],
                "session_id": item["session_id"],
                "event": item["source_event"],
                "telemetry_evidence": item["telemetry_evidence"],
                "observed_facts": rca.get("observed_facts", []),
                "rca_hypotheses": rca.get("hypotheses", []),
                "recommended_checks": rca.get("recommended_checks", []),
                "evidence_limitations": rca.get("evidence_limitations", []),
                "detector_predictions_available_in_source_manifest": True,
                "review_form": {
                    "allowed_labels": list(EXPERT_REVIEW_LABELS),
                    "reviewer_judgment": None,
                    "missing_cause_notes": None,
                    "supporting_evidence": None,
                    "contradicting_evidence": None,
                    "uncertainty": None,
                    "comments": None,
                    "physical_fault_claim_validated": False,
                },
            }
        )
    return {
        "schema_version": "evidence-based-rca-expert-review-packet-v1",
        "purpose": "Review RCA hypotheses and checks without treating detector output or expert opinion as physical ground truth.",
        "review_label_policy": {
            "expert_opinion_is_physical_ground_truth": False,
            "detector_prediction_is_ground_truth": False,
            "rca_hypothesis_is_ground_truth": False,
        },
        "item_count": len(items),
        "items": items,
    }


def build_decision_report(contract: dict[str, Any], review_manifest: dict[str, Any]) -> dict[str, Any]:
    summary = review_manifest["summary"]
    disagreement_source_count = sum(
        1
        for item in review_manifest.get("items", [])
        if (item.get("source_event") or {}).get("evidence_state") == "detector_disagreement"
    )
    disagreement_preserved = summary["detector_disagreement_preserved_count"]
    weak_rule_count = sum(summary["weak_or_ambiguous_rule_fire_counts"].values())
    untraceable = summary["untraceable_hypothesis_count"]
    physical_fault_claims = any(
        hypothesis.get("physical_fault_claim_made")
        for item in review_manifest.get("items", [])
        for hypothesis in item["rca_result"].get("hypotheses", [])
    )
    if untraceable or physical_fault_claims or disagreement_preserved < disagreement_source_count:
        recommendation = "revise"
    elif summary["hypothesis_count"] == 0:
        recommendation = "reject"
    else:
        recommendation = "advance_to_expert_validation"

    return {
        "schema_version": "evidence-based-rca-decision-v1",
        "recommendation": recommendation,
        "production_behavior_changed": False,
        "drive_safe_behavior_changed": False,
        "answers": {
            "events_with_hypotheses": summary["results_with_hypotheses"],
            "events_returning_insufficient_evidence": summary["results_insufficient_evidence"],
            "most_frequent_rules": summary["rule_fire_counts"],
            "weak_or_ambiguous_rule_outputs": summary["weak_or_ambiguous_rule_fire_counts"],
            "detector_disagreement_preserved": disagreement_preserved == disagreement_source_count,
            "detector_disagreement_source_count": disagreement_source_count,
            "detector_disagreement_preserved_count": disagreement_preserved,
            "every_hypothesis_traceable": untraceable == 0,
            "outputs_requiring_expert_or_physical_validation": "all generated hypotheses",
        },
        "rule_catalog_version": contract["rule_catalog_version"],
        "engine_version": contract["engine_version"],
        "risks_and_limitations": [
            "RCA hypotheses are deterministic explanations of observed evidence, not mechanical fault findings.",
            "Possible-status hypotheses intentionally include weak or ambiguous evidence that needs expert review.",
            "No event-level physical ground truth is available for validating RCA correctness.",
            "Coverage-gap events can limit causal interpretation even when one detector fired.",
        ],
    }


def empty_review_annotation() -> dict[str, Any]:
    return {
        "reviewer": None,
        "reviewed_at": None,
        "label": None,
        "allowed_labels": list(EXPERT_REVIEW_LABELS),
        "supporting_evidence": None,
        "contradicting_evidence": None,
        "missing_cause_notes": None,
        "uncertainty": None,
        "comments": None,
        "physical_fault_claim_validated": False,
    }


def write_markdown_report(path: Path, review_manifest: dict[str, Any], decision: dict[str, Any]) -> None:
    summary = review_manifest["summary"]
    answers = decision["answers"]
    lines = [
        "# H7 Evidence-Based RCA Prototype Report",
        "",
        "## Dataset",
        "",
        f"- Review items: `{summary['review_item_count']}`",
        f"- Source region types: `{summary['source_validation_region_type_counts']}`",
        f"- RCA states: `{summary['rca_state_counts']}`",
        f"- Hypotheses generated: `{summary['hypothesis_count']}` across `{summary['results_with_hypotheses']}` events",
        f"- Insufficient-evidence events: `{summary['results_insufficient_evidence']}`",
        "",
        "## Rules",
        "",
        f"- Rule fire counts: `{summary['rule_fire_counts']}`",
        f"- Hypothesis counts: `{summary['hypothesis_counts']}`",
        f"- Weak or ambiguous possible-status rules: `{summary['weak_or_ambiguous_rule_fire_counts']}`",
        "",
        "## Validation Boundary",
        "",
        f"- Every hypothesis traceable: `{answers['every_hypothesis_traceable']}`",
        f"- Detector disagreement preserved: `{answers['detector_disagreement_preserved']}` "
        f"({answers['detector_disagreement_preserved_count']}/{answers['detector_disagreement_source_count']})",
        "- Detector predictions, RCA hypotheses and expert judgments are stored separately.",
        "- RCA does not modify detector output and does not make physical fault claims.",
        "",
        "## Decision",
        "",
        f"- Recommendation: `{decision['recommendation']}`",
        "- All hypotheses require expert or physical validation before operational use.",
        "",
        "## Compatibility",
        "",
        "- Production inference remains unchanged.",
        "- DriveSafe behavior was not modified.",
        "- Rejected temporal candidates remain excluded.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
