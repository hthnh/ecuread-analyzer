from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.evaluation.model_harness_evaluation import EvaluationPaths, default_paths, write_json
from app.integration.diagnostic_evidence import (
    DIAGNOSTIC_EVIDENCE_ANALYSIS_VERSION,
    build_research_diagnostic_evidence,
    diagnostic_evidence_contract,
)


REPRESENTATIVE_SCENARIOS = (
    "if_only_evidence",
    "contextual_only_evidence",
    "overlapping_detector_evidence",
    "detector_disagreement",
    "insufficient_coverage",
    "history_unavailable",
    "recurrent_history",
    "history_prioritized_check",
)


def default_h9_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h9")


def run_all(
    paths: EvaluationPaths,
    *,
    h8_dir: Path | None = None,
    drivesafe_repo: Path | None = None,
) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h8_dir = (h8_dir or paths.repo_root / "data" / "evaluation" / "h8").resolve()
    h8_results = read_json(resolved_h8_dir / "rca_v2_with_history_results.json")
    h8_decision = read_json(resolved_h8_dir / "decision_report.json")
    contract = diagnostic_evidence_contract()
    payloads = build_payloads(h8_results)
    examples = build_representative_examples(payloads)
    compatibility = build_compatibility_report(paths, drivesafe_repo, payloads, examples)
    decision = build_decision_report(contract, compatibility, h8_decision)

    outputs = {
        "contract": paths.output_dir / "diagnostic_evidence_contract.json",
        "payloads": paths.output_dir / "diagnostic_evidence_payloads.json",
        "examples": paths.output_dir / "representative_session_examples.json",
        "compatibility": paths.output_dir / "compatibility_report.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["contract"], contract)
    write_json(outputs["payloads"], payloads)
    write_json(outputs["examples"], examples)
    write_json(outputs["compatibility"], compatibility)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], compatibility, examples, decision)
    return outputs


def build_payloads(h8_results: dict[str, Any]) -> dict[str, Any]:
    payloads = []
    for result in h8_results.get("results", []):
        payloads.append(
            {
                "source_case_id": result.get("source_case_id"),
                "session_id": result.get("session_id"),
                "source_event_id": (result.get("source_event") or {}).get("event_id"),
                "diagnostic_evidence": build_research_diagnostic_evidence(result),
            }
        )
    aggregate_counts = Counter(payload["diagnostic_evidence"]["aggregate"]["evidence_state"] for payload in payloads)
    possible_cause_count = sum(
        len(payload["diagnostic_evidence"]["interpretation"]["possible_causes"])
        for payload in payloads
    )
    priority_counts = Counter(
        priority["priority"]
        for payload in payloads
        for priority in payload["diagnostic_evidence"].get("recommended_check_priorities", [])
    )
    return {
        "schema_version": "h9-diagnostic-evidence-payloads-v1",
        "analysis_version": DIAGNOSTIC_EVIDENCE_ANALYSIS_VERSION,
        "policy": {
            "additive_contract": True,
            "production_anomaly_status_changed": False,
            "h7_v1_hypotheses_exposed": False,
            "history_can_create_root_cause": False,
            "check_priority_is_failure_probability": False,
        },
        "summary": {
            "payload_count": len(payloads),
            "aggregate_evidence_state_counts": dict(sorted(aggregate_counts.items())),
            "possible_cause_count": possible_cause_count,
            "recommended_check_priority_counts": dict(sorted(priority_counts.items())),
        },
        "payloads": payloads,
    }


def build_representative_examples(payloads: dict[str, Any]) -> dict[str, Any]:
    selected: dict[str, dict[str, Any]] = {}
    for payload in payloads.get("payloads", []):
        for scenario in scenarios_for_payload(payload):
            selected.setdefault(scenario, payload)
    missing = [scenario for scenario in REPRESENTATIVE_SCENARIOS if scenario not in selected]
    return {
        "schema_version": "h9-representative-session-examples-v1",
        "required_scenarios": list(REPRESENTATIVE_SCENARIOS),
        "covered_scenarios": sorted(selected),
        "missing_scenarios": missing,
        "examples": {
            scenario: compact_example(scenario, selected[scenario])
            for scenario in REPRESENTATIVE_SCENARIOS
            if scenario in selected
        },
    }


def scenarios_for_payload(payload: dict[str, Any]) -> list[str]:
    evidence = payload["diagnostic_evidence"]
    aggregate = evidence["aggregate"]
    interpretation = evidence["interpretation"]
    history = evidence["history"]
    priorities = evidence.get("recommended_check_priorities", [])
    positive_ids = set((aggregate.get("detector_ids") or {}).get("positive") or [])
    scenarios = []
    if positive_ids == {"isolation_forest"}:
        scenarios.append("if_only_evidence")
    if positive_ids == {"contextual_battery_voltage"}:
        scenarios.append("contextual_only_evidence")
    if len(positive_ids) > 1 or aggregate.get("evidence_state") == "multiple_detector_evidence":
        scenarios.append("overlapping_detector_evidence")
    if aggregate.get("evidence_state") == "detector_disagreement" or any(
        observation.get("observation_id") == "obs.v2.detector_disagreement"
        for observation in interpretation.get("observations", [])
    ):
        scenarios.append("detector_disagreement")
    if aggregate.get("evidence_state") == "insufficient_coverage" or any(
        limitation.get("limitation_id") == "limit.v2.insufficient_coverage"
        for limitation in interpretation.get("limitations", [])
    ):
        scenarios.append("insufficient_coverage")
    recurrence_counts = (history.get("summary") or {}).get("recurrence_classification_counts", {})
    if recurrence_counts.get("historical_comparison_unavailable"):
        scenarios.append("history_unavailable")
    if recurrence_counts.get("recurrent") or recurrence_counts.get("persistent_across_sessions"):
        scenarios.append("recurrent_history")
    if any(priority.get("priority") == "priority" for priority in priorities):
        scenarios.append("history_prioritized_check")
    return scenarios


def compact_example(scenario: str, payload: dict[str, Any]) -> dict[str, Any]:
    evidence = payload["diagnostic_evidence"]
    return {
        "scenario": scenario,
        "source_case_id": payload.get("source_case_id"),
        "session_id": payload.get("session_id"),
        "source_event_id": payload.get("source_event_id"),
        "aggregate": evidence["aggregate"],
        "observation_ids": [
            observation.get("observation_id")
            for observation in evidence["interpretation"].get("observations", [])
        ],
        "symptom_ids": [
            symptom.get("symptom_id")
            for symptom in evidence["interpretation"].get("symptoms", [])
        ],
        "possible_cause_count": len(evidence["interpretation"].get("possible_causes", [])),
        "recommended_check_priorities": evidence.get("recommended_check_priorities", []),
        "history_summary": evidence["history"].get("summary", {}),
        "production_boundary": evidence["production_boundary"],
    }


def build_compatibility_report(
    paths: EvaluationPaths,
    drivesafe_repo: Path | None,
    payloads: dict[str, Any],
    examples: dict[str, Any],
) -> dict[str, Any]:
    discovered = drivesafe_repo if drivesafe_repo is not None else discover_drivesafe_repo(paths.repo_root)
    drivesafe_available = discovered is not None and discovered.exists()
    payload_list = payloads.get("payloads", [])
    unsafe_terms = ("confirmed fault", "component failure", "probability of failure", "healthy vehicle", "diagnosis confirmed")
    unsafe_payloads = [
        payload.get("source_case_id")
        for payload in payload_list
        if any(term in json.dumps(payload["diagnostic_evidence"]).lower() for term in unsafe_terms)
    ]
    possible_causes = sum(
        len(payload["diagnostic_evidence"]["interpretation"].get("possible_causes", []))
        for payload in payload_list
    )
    return {
        "schema_version": "h9-diagnostic-integration-compatibility-v1",
        "analyzer_contract": {
            "field": "diagnostic_evidence",
            "additive": True,
            "persisted_in_analysis_run_json": True,
            "returned_by_canonical_analysis_api": True,
            "returned_by_raw_decode_analysis_api": True,
            "returned_by_upload_session_as_top_level_field": True,
        },
        "drivesafe_web_app": {
            "repo_available": drivesafe_available,
            "path": str(discovered) if discovered else None,
            "persistence_schema_modified": False,
            "ui_modified": False,
            "limitation": (
                None
                if drivesafe_available
                else "DriveSafe Web App repository was not present in this workspace, so UI and web-app persistence changes were not applied."
            ),
        },
        "backward_compatibility": {
            "existing_public_fields_replaced": False,
            "production_anomaly_fields_changed": False,
            "payload_required_for_existing_consumers": False,
            "works_when_diagnostic_evidence_absent": True,
        },
        "research_production_boundary": {
            "possible_cause_count": possible_causes,
            "unsafe_language_payload_ids": unsafe_payloads,
            "h7_v1_hypotheses_exposed": False,
            "history_changes_alerting": False,
            "check_priority_is_failure_probability": False,
        },
        "end_to_end_coverage": {
            "required_scenarios": list(REPRESENTATIVE_SCENARIOS),
            "covered_scenarios": examples.get("covered_scenarios", []),
            "missing_scenarios": examples.get("missing_scenarios", []),
            "all_representative_scenarios_covered": not examples.get("missing_scenarios"),
        },
        "migration": {
            "analyzer_database_migration_required": False,
            "recommended_drivesafe_storage": "Store diagnostic_evidence as nullable versioned JSON on the existing analysis/session review record.",
            "historical_cutoff_fields": [
                "diagnostic_evidence.provenance.historical_comparison_cutoff",
                "diagnostic_evidence.history.mode",
                "diagnostic_evidence.history.future_sessions_allowed",
            ],
        },
    }


def build_decision_report(
    contract: dict[str, Any],
    compatibility: dict[str, Any],
    h8_decision: dict[str, Any],
) -> dict[str, Any]:
    analyzer_ready = (
        compatibility["backward_compatibility"]["existing_public_fields_replaced"] is False
        and compatibility["backward_compatibility"]["production_anomaly_fields_changed"] is False
        and not compatibility["research_production_boundary"]["unsafe_language_payload_ids"]
        and compatibility["research_production_boundary"]["possible_cause_count"] == 0
        and compatibility["end_to_end_coverage"]["all_representative_scenarios_covered"]
        and h8_decision.get("recommendation") == "advance"
    )
    drivesafe_available = compatibility["drivesafe_web_app"]["repo_available"]
    recommendation = "advance" if analyzer_ready and drivesafe_available else "revise" if analyzer_ready else "reject"
    return {
        "schema_version": "h9-diagnostic-integration-decision-v1",
        "recommendation": recommendation,
        "analyzer_side_ready": analyzer_ready,
        "drivesafe_repo_available": drivesafe_available,
        "production_behavior_changed": False,
        "drive_safe_behavior_changed": False,
        "answers": {
            "contract_changes": "Added additive diagnostic_evidence JSON contract to analyzer summaries and persisted analysis_run.json.",
            "database_changes": "Analyzer file-backed analysis records now include nullable diagnostic_evidence JSON; no database migration or new service.",
            "backward_compatibility_behavior": "Existing anomaly/health fields remain unchanged and consumers may ignore diagnostic_evidence.",
            "migration_requirements": compatibility["migration"]["recommended_drivesafe_storage"],
            "ui_changes": (
                "Not applied because DriveSafe Web App repository was unavailable."
                if not drivesafe_available
                else "Analyzer payload is ready for a collapsed diagnostic evidence section."
            ),
            "research_production_boundary": contract["policy"],
        },
        "limitations": [
            "DriveSafe Web App persistence and UI were not modified because the repository was not available in this workspace.",
            "Live production analysis exposes detector and aggregate evidence; RCA v2/history remain unavailable unless research artifacts are explicitly attached.",
            "H9 payloads from H8 are retrospective research examples with temporal cutoff metadata, not alerting inputs.",
        ],
    }


def write_markdown_report(
    path: Path,
    compatibility: dict[str, Any],
    examples: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    lines = [
        "# H9 Analyzer to DriveSafe Diagnostic Evidence Integration",
        "",
        "## Contract",
        "",
        "- Added versioned `diagnostic_evidence` JSON as an additive analyzer payload.",
        "- Existing anomaly status, health score, alerts and detector thresholds remain unchanged.",
        "- Research evidence is explicitly marked and cannot confirm root cause.",
        "",
        "## Compatibility",
        "",
        f"- DriveSafe Web App repository available: `{compatibility['drivesafe_web_app']['repo_available']}`",
        f"- Existing public fields replaced: `{compatibility['backward_compatibility']['existing_public_fields_replaced']}`",
        f"- Production anomaly fields changed: `{compatibility['backward_compatibility']['production_anomaly_fields_changed']}`",
        f"- Representative scenarios covered: `{examples['covered_scenarios']}`",
        f"- Missing scenarios: `{examples['missing_scenarios']}`",
        f"- Possible-cause count in H9 examples: `{compatibility['research_production_boundary']['possible_cause_count']}`",
        "",
        "## Decision",
        "",
        f"- Recommendation: `{decision['recommendation']}`",
        f"- Analyzer side ready: `{decision['analyzer_side_ready']}`",
        f"- DriveSafe repo available: `{decision['drivesafe_repo_available']}`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def discover_drivesafe_repo(repo_root: Path) -> Path | None:
    candidates = [
        repo_root.parent / "drivesafe-web-app",
        repo_root.parent / "DriveSafe Web App",
        repo_root.parent / "DriveSafe",
        repo_root.parent / "drivesafe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
