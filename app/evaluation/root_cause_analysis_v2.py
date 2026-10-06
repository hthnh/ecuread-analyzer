from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.evaluation.model_harness_evaluation import EvaluationPaths, default_paths, write_json
from app.ml.root_cause_analysis_v2 import (
    RootCauseAnalyzerV2,
    diagnostic_check_catalog,
    rca_v2_contract,
    summarize_v2_results,
)


def default_h73_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h7_3")


def run_all(
    paths: EvaluationPaths,
    *,
    h7_dir: Path | None = None,
    h71_dir: Path | None = None,
    h72_dir: Path | None = None,
) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h7_dir = (h7_dir or paths.repo_root / "data" / "evaluation" / "h7").resolve()
    resolved_h71_dir = (h71_dir or paths.repo_root / "data" / "evaluation" / "h7_1").resolve()
    resolved_h72_dir = (h72_dir or paths.repo_root / "data" / "evaluation" / "h7_2").resolve()

    h7_review = read_json(resolved_h7_dir / "rca_review_manifest.json")
    h71_dataset = read_json(resolved_h71_dir / "expert_validation_dataset.json")
    source_registry = read_json(resolved_h72_dir / "source_registry.json")
    grounding_matrix = read_json(resolved_h72_dir / "rule_grounding_matrix.json")
    candidate_catalog = read_json(resolved_h72_dir / "candidate_rca_v2_catalog.json")

    contract = rca_v2_contract()
    check_catalog = diagnostic_check_catalog()
    analyzer = RootCauseAnalyzerV2(
        source_registry=source_registry,
        grounding_matrix=grounding_matrix,
        candidate_catalog=candidate_catalog,
        check_catalog=check_catalog,
    )
    results = [analyzer.analyze_case(case) for case in h71_dataset.get("cases", [])]
    comparison = build_v1_v2_comparison(h7_review, h71_dataset, results)
    decision = build_decision_report(comparison, results)

    outputs = {
        "contract": paths.output_dir / "rca_v2_contract.json",
        "check_catalog": paths.output_dir / "diagnostic_check_catalog.json",
        "results": paths.output_dir / "rca_v2_results.json",
        "comparison": paths.output_dir / "v1_v2_comparison.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["contract"], contract)
    write_json(outputs["check_catalog"], check_catalog)
    write_json(outputs["results"], build_results_payload(results))
    write_json(outputs["comparison"], comparison)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], comparison, decision)
    return outputs


def build_results_payload(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_v2_results(results)
    return {
        "schema_version": "root-cause-analysis-v2-results-v1",
        "summary": summary,
        "policy": {
            "production_behavior_changed": False,
            "drive_safe_behavior_changed": False,
            "h7_v1_artifacts_preserved": True,
            "empty_possible_cause_lists_supported": True,
        },
        "results": results,
    }


def build_v1_v2_comparison(
    h7_review: dict[str, Any],
    h71_dataset: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    v2_summary = summarize_v2_results(results)
    case_count = len(h71_dataset.get("cases", []))
    evaluated_case_ids = {result["source_case_id"] for result in results}
    source_case_ids = {case["case_id"] for case in h71_dataset.get("cases", [])}
    v1_summary = h7_review.get("summary", {})
    v1_hypothesis_count = int(v1_summary.get("hypothesis_count", 0))
    v1_insufficient = int(v1_summary.get("results_insufficient_evidence", 0))
    v2_insufficient = sum(1 for result in results if any(limit["limitation_id"] == "limit.v2.insufficient_coverage" for limit in result["evidence_limitations"]))
    return {
        "schema_version": "rca-v1-v2-comparison-v1",
        "v1": {
            "rule_catalog_version": "evidence-based-rca-rules-v1",
            "hypothesis_count": v1_hypothesis_count,
            "result_count": int(v1_summary.get("result_count", 0)),
            "insufficient_evidence_count": v1_insufficient,
        },
        "v2": {
            "rule_catalog_version": "evidence-grounded-rca-v2",
            **v2_summary,
        },
        "comparison": {
            "unsupported_claims_removed": v2_summary["unsupported_v1_causal_claims_removed"],
            "causal_claim_reduction": v1_hypothesis_count - v2_summary["possible_cause_count"],
            "case_coverage_retained": len(evaluated_case_ids),
            "case_coverage_expected": case_count,
            "case_coverage_lost": sorted(source_case_ids - evaluated_case_ids),
            "case_coverage_retained_ratio": round(len(evaluated_case_ids) / max(1, case_count), 6),
            "insufficient_evidence_handling": {
                "v1_insufficient_evidence_count": v1_insufficient,
                "v2_insufficient_evidence_count": v2_insufficient,
                "changed": v1_insufficient != v2_insufficient,
            },
            "checks_without_causes_supported": v2_summary["cases_with_recommended_checks_without_causes"],
            "expected_causal_claim_reduction": True,
        },
    }


def build_decision_report(comparison: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    coverage_ok = comparison["comparison"]["case_coverage_retained"] == comparison["comparison"]["case_coverage_expected"]
    sections_distinct = all(
        isinstance(result.get("observations"), list)
        and isinstance(result.get("symptoms"), list)
        and isinstance(result.get("possible_causes"), list)
        and isinstance(result.get("recommended_checks"), list)
        for result in results
    )
    unsupported_removed = comparison["comparison"]["unsupported_claims_removed"] > 0
    if coverage_ok and sections_distinct and unsupported_removed:
        recommendation = "promote_v2_for_research"
        replace_default = True
    elif coverage_ok and sections_distinct:
        recommendation = "revise"
        replace_default = False
    else:
        recommendation = "reject"
        replace_default = False
    return {
        "schema_version": "rca-v2-semantic-promotion-decision-v1",
        "recommendation": recommendation,
        "replace_h7_v1_as_default_offline_research_interpretation": replace_default,
        "h7_v1_remains_available_for_historical_reproducibility": True,
        "production_behavior_changed": False,
        "drive_safe_behavior_changed": False,
        "expert_fields_synthesized": False,
        "answers": {
            "observation_symptom_cause_sections_distinct": sections_distinct,
            "all_33_cases_evaluated": coverage_ok,
            "unsupported_causal_claims_removed": unsupported_removed,
            "possible_cause_lists_can_be_empty": all(isinstance(result["possible_causes"], list) for result in results),
            "recommended_checks_without_causes_supported": comparison["comparison"]["checks_without_causes_supported"] > 0,
            "insufficient_evidence_handling_changed": comparison["comparison"]["insufficient_evidence_handling"]["changed"],
        },
        "limitations": [
            "RCA v2 is an offline research interpretation layer only.",
            "RCA v2 does not confirm physical root causes.",
            "H7.1 expert validation remains unpopulated.",
        ],
    }


def write_markdown_report(path: Path, comparison: dict[str, Any], decision: dict[str, Any]) -> None:
    lines = [
        "# H7.3 RCA v2 Semantic Promotion",
        "",
        "## Boundary",
        "",
        "- RCA v2 separates observations, symptoms, possible causes and recommended checks.",
        "- Empty possible-cause lists are valid.",
        "- Recommended checks may exist without a causal hypothesis.",
        "- Production inference and DriveSafe behavior remain unchanged.",
        "",
        "## Comparison",
        "",
        f"- H7 v1 hypothesis count: `{comparison['v1']['hypothesis_count']}`",
        f"- RCA v2 observation count: `{comparison['v2']['observation_count']}`",
        f"- RCA v2 symptom count: `{comparison['v2']['symptom_count']}`",
        f"- RCA v2 possible-cause count: `{comparison['v2']['possible_cause_count']}`",
        f"- RCA v2 recommended-check count: `{comparison['v2']['recommended_check_count']}`",
        f"- Unsupported v1 causal claims removed: `{comparison['comparison']['unsupported_claims_removed']}`",
        f"- Case coverage retained: `{comparison['comparison']['case_coverage_retained']}/{comparison['comparison']['case_coverage_expected']}`",
        f"- Insufficient-evidence handling changed: `{comparison['comparison']['insufficient_evidence_handling']['changed']}`",
        "",
        "## Decision",
        "",
        f"- Recommendation: `{decision['recommendation']}`",
        f"- Replace H7 v1 as default offline research interpretation: `{decision['replace_h7_v1_as_default_offline_research_interpretation']}`",
        "- H7 v1 remains available for historical reproducibility.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
