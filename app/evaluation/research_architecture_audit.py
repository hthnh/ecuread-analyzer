from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.evaluation.model_harness_evaluation import EvaluationPaths, default_paths, write_json
from app.integration.diagnostic_evidence import diagnostic_evidence_contract
from app.ml.evidence_aggregation import ACTIVE_H5_DETECTOR_IDS, REJECTED_TEMPORAL_CANDIDATE_IDS


RA1_SCHEMA_VERSION = "research-architecture-audit-v1.1"


def default_ra1_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "ra1")


def run_all(paths: EvaluationPaths, *, drivesafe_repo: Path | None = None) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = load_artifacts(paths.repo_root)
    audit_context = build_audit_context(paths, artifacts, drivesafe_repo=drivesafe_repo)

    outputs = {
        "architecture_overview": paths.output_dir / "architecture_overview.md",
        "component_inventory": paths.output_dir / "component_inventory.json",
        "decision_timeline": paths.output_dir / "decision_timeline.md",
        "detector_decision_matrix": paths.output_dir / "detector_decision_matrix.json",
        "rejected_alternatives": paths.output_dir / "rejected_alternatives.md",
        "evidence_maturity_matrix": paths.output_dir / "evidence_maturity_matrix.json",
        "knowledge_claims": paths.output_dir / "knowledge_claims.json",
        "research_production_boundary": paths.output_dir / "research_production_boundary.json",
        "dependency_map": paths.output_dir / "dependency_map.md",
        "failure_mode_audit": paths.output_dir / "failure_mode_audit.md",
        "complexity_audit": paths.output_dir / "complexity_audit.md",
        "contribution_audit": paths.output_dir / "contribution_audit.md",
        "future_work_triggers": paths.output_dir / "future_work_triggers.md",
        "final_recommended_architecture": paths.output_dir / "final_recommended_architecture.md",
        "audit_report": paths.output_dir / "audit_report.md",
        "final_integration_reconciliation": paths.output_dir / "final_integration_reconciliation.md",
        "warning_audit": paths.output_dir / "warning_audit.json",
    }

    write_text(outputs["architecture_overview"], architecture_overview(audit_context))
    write_json(outputs["component_inventory"], component_inventory(audit_context))
    write_text(outputs["decision_timeline"], decision_timeline(audit_context))
    write_json(outputs["detector_decision_matrix"], detector_decision_matrix(audit_context))
    write_text(outputs["rejected_alternatives"], rejected_alternatives(audit_context))
    write_json(outputs["evidence_maturity_matrix"], evidence_maturity_matrix(audit_context))
    write_json(outputs["knowledge_claims"], knowledge_claims(audit_context))
    write_json(outputs["research_production_boundary"], research_production_boundary(audit_context))
    write_text(outputs["dependency_map"], dependency_map(audit_context))
    write_text(outputs["failure_mode_audit"], failure_mode_audit(audit_context))
    write_text(outputs["complexity_audit"], complexity_audit(audit_context))
    write_text(outputs["contribution_audit"], contribution_audit(audit_context))
    write_text(outputs["future_work_triggers"], future_work_triggers(audit_context))
    write_text(outputs["final_recommended_architecture"], final_recommended_architecture(audit_context))
    write_text(outputs["audit_report"], audit_report(audit_context))
    write_text(outputs["final_integration_reconciliation"], final_integration_reconciliation(audit_context))
    write_json(outputs["warning_audit"], warning_audit(audit_context))
    return outputs


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def load_artifacts(repo_root: Path) -> dict[str, Any]:
    evaluation = repo_root / "data" / "evaluation"
    mapping = {
        "h2_parity": evaluation / "h2" / "parity_report.json",
        "h2_manifest": evaluation / "h2" / "evaluation_manifest.json",
        "h2_benchmark": evaluation / "h2" / "benchmark_results.json",
        "h3_feasibility": evaluation / "h3" / "feasibility_audit.json",
        "h3_shadow": evaluation / "h3" / "shadow_benchmark_results.json",
        "h3_decision": evaluation / "h3" / "decision_report.json",
        "h3_1_reference_audit": evaluation / "h3_1" / "reference_audit.json",
        "h3_1_disagreements": evaluation / "h3_1" / "disagreement_summary.json",
        "h3_1_reference_stability": evaluation / "h3_1" / "reference_stability.json",
        "h3_1_boundary": evaluation / "h3_1" / "context_boundary_review.json",
        "h3_1_review": evaluation / "h3_1" / "manual_review_manifest.json",
        "h3_1_decision": evaluation / "h3_1" / "decision_report.json",
        "h4_shadow": evaluation / "h4" / "shadow_benchmark_results.json",
        "h4_sensitivity": evaluation / "h4" / "parameter_sensitivity.json",
        "h4_decision": evaluation / "h4" / "decision_report.json",
        "h4_1_target": evaluation / "h4_1" / "target_audit.json",
        "h4_1_benchmark": evaluation / "h4_1" / "candidate_benchmark_results.json",
        "h4_1_sensitivity": evaluation / "h4_1" / "rpm_parameter_sensitivity.json",
        "h4_1_selection": evaluation / "h4_1" / "candidate_selection.json",
        "h5_benchmark": evaluation / "h5" / "aggregate_benchmark_results.json",
        "h5_event_review": evaluation / "h5" / "aggregate_event_review.json",
        "h5_contract": evaluation / "h5" / "aggregation_contract.json",
        "h5_decision": evaluation / "h5" / "decision_report.json",
        "h6_manifest": evaluation / "h6" / "detector_validation_manifest.json",
        "h6_metrics": evaluation / "h6" / "validation_metrics.json",
        "h6_decision": evaluation / "h6" / "decision_report.json",
        "h7_decision": evaluation / "h7" / "decision_report.json",
        "h7_contract": evaluation / "h7" / "rca_contract.json",
        "h7_1_dataset": evaluation / "h7_1" / "expert_validation_dataset.json",
        "h7_1_decision": evaluation / "h7_1" / "decision_report.json",
        "h7_2_decision": evaluation / "h7_2" / "decision_report.json",
        "h7_2_counterexamples": evaluation / "h7_2" / "counterexample_report.json",
        "h7_2_structural": evaluation / "h7_2" / "structural_verification_report.json",
        "h7_3_comparison": evaluation / "h7_3" / "v1_v2_comparison.json",
        "h7_3_checks": evaluation / "h7_3" / "diagnostic_check_catalog.json",
        "h7_3_decision": evaluation / "h7_3" / "decision_report.json",
        "h8_index": evaluation / "h8" / "historical_observation_index.json",
        "h8_offline": evaluation / "h8" / "offline_evaluation.json",
        "h8_counterfactual": evaluation / "h8" / "counterfactual_evaluation.json",
        "h8_governance": evaluation / "h8" / "baseline_governance.json",
        "h8_decision": evaluation / "h8" / "decision_report.json",
        "h9_payloads": evaluation / "h9" / "diagnostic_evidence_payloads.json",
        "h9_compatibility": evaluation / "h9" / "compatibility_report.json",
        "h9_decision": evaluation / "h9" / "decision_report.json",
    }
    return {key: read_json(path) for key, path in mapping.items()}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "missing": True,
            "path": str(path),
        }
    return json.loads(path.read_text(encoding="utf-8"))


def read_text_if_exists(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def analyzer_diagnostic_evidence_status(repo_root: Path) -> dict[str, Any]:
    evidence_module = read_text_if_exists(repo_root / "app" / "integration" / "diagnostic_evidence.py")
    analysis_service = read_text_if_exists(repo_root / "app" / "services" / "analysis_service.py")
    session_processor = read_text_if_exists(repo_root / "app" / "services" / "session_processor.py")
    tests = read_text_if_exists(repo_root / "tests" / "test_diagnostic_integration.py")
    checks = {
        "contract_builder_present": "def diagnostic_evidence_contract" in evidence_module,
        "payload_builder_present": "def build_diagnostic_evidence" in evidence_module,
        "analysis_api_additive_field": '"diagnostic_evidence"' in analysis_service,
        "session_processor_persists_field": 'summary["diagnostic_evidence"]' in session_processor,
        "additive_contract_tested": "test_analysis_api_returns_and_persists_additive_diagnostic_evidence" in tests,
        "legacy_result_contract_tested": '"diagnostic_evidence" not in body["result"]' in tests,
    }
    return {
        "emits_additive_diagnostic_evidence": all(checks.values()),
        "checks": checks,
    }


def inspect_drivesafe_integration(drivesafe_repo: Path | None) -> dict[str, Any]:
    if drivesafe_repo is None:
        return {
            "repo_available": False,
            "completed": False,
            "checks": {},
            "notes": ["DriveSafe repository path was not supplied to the RA1.1 generator."],
        }

    root = drivesafe_repo.resolve()
    analysis_submission = read_text_if_exists(root / "services" / "analysis_submission.py")
    pi_sync = read_text_if_exists(root / "services" / "pi_sync.py")
    analysis = read_text_if_exists(root / "services" / "analysis.py")
    template = read_text_if_exists(root / "templates" / "session_detail.html")
    tests = read_text_if_exists(root / "tests" / "test_app.py")
    details_tag_index = template.find('diagnostic-evidence-card')
    details_open = details_tag_index >= 0 and " open" in template[max(0, details_tag_index - 80):details_tag_index + 80]
    checks = {
        "repo_available": root.exists(),
        "optional_analyzer_evidence_tolerated": "_analysis_summary_with_safe_diagnostic_evidence" in analysis_submission,
        "optional_pi_evidence_tolerated": "_optional_diagnostic_evidence" in pi_sync,
        "valid_evidence_persisted_in_result_summary": "result.result_summary = summary" in analysis_submission
        and 'normalized["diagnostic_evidence"]' in pi_sync,
        "malformed_evidence_isolated": "diagnostic_evidence ignored because it was not a JSON object" in analysis_submission
        and "test_malformed_analyzer_diagnostic_evidence_does_not_fail_analysis" in tests,
        "raw_evidence_exposed": '"diagnostic_evidence": diagnostic_evidence' in analysis,
        "display_view_is_snapshot_only": "diagnostic_evidence_view(result_summary)" in analysis,
        "session_detail_collapsed_section": "Additional diagnostic evidence" in template and "<details" in template and not details_open,
        "production_status_health_unchanged": '"result": external_result or result_from_scores' in analysis
        and '"health_score": latest_external.health_score' in analysis,
        "legacy_behavior_tested": "test_analyzer_limited_data_status_persists_and_displays_neutral_result" in tests,
        "recommended_check_priority_preserved": '["priority", "relevant", "deferred", "insufficient_evidence"]' in tests,
    }
    completed = all(checks.values())
    return {
        "repo_available": root.exists(),
        "repo_path": str(root),
        "completed": completed,
        "checks": checks,
        "notes": [
            "DriveSafe consumes diagnostic_evidence as nullable JSON on existing analysis summaries.",
            "diagnostic_evidence_view is display-only and is not used to derive production result, health, alerts or completion state.",
            "DriveSafe does not recompute detector inference, aggregation, RCA v2 or historical recurrence/trend evidence.",
        ],
    }


def build_audit_context(
    paths: EvaluationPaths,
    artifacts: dict[str, Any],
    *,
    drivesafe_repo: Path | None,
) -> dict[str, Any]:
    h5_summary = artifacts["h5_benchmark"].get("summary", {})
    h6_metrics = artifacts["h6_metrics"].get("detectors", {})
    h7_3 = artifacts["h7_3_comparison"]
    h8_index_summary = artifacts["h8_index"].get("summary", {})
    h8_offline_summary = artifacts["h8_offline"].get("summary", {})
    h8_counterfactual_summary = artifacts["h8_counterfactual"].get("summary", {})
    h9_summary = artifacts["h9_payloads"].get("summary", {})
    h9_compat = artifacts["h9_compatibility"]
    analyzer_evidence = analyzer_diagnostic_evidence_status(paths.repo_root)
    drivesafe_integration = inspect_drivesafe_integration(drivesafe_repo)
    drivesafe_available = bool(
        drivesafe_repo.exists()
        if drivesafe_repo is not None
        else h9_compat.get("drivesafe_web_app", {}).get("repo_available", False)
    )
    h9b_completed = bool(analyzer_evidence["emits_additive_diagnostic_evidence"] and drivesafe_integration["completed"])
    context = {
        "schema_version": RA1_SCHEMA_VERSION,
        "repo_root": str(paths.repo_root),
        "production_detector_set": ["isolation_forest"],
        "research_active_detector_set": list(ACTIVE_H5_DETECTOR_IDS),
        "rejected_research_detector_set": list(REJECTED_TEMPORAL_CANDIDATE_IDS),
        "h2": {
            "parity_passed": artifacts["h2_parity"].get("passed"),
            "parity_limitations": artifacts["h2_parity"].get("limitations", []),
            "parity_cases": [
                {"case": case.get("case"), "window_count": case.get("window_count"), "passed": case.get("passed")}
                for case in artifacts["h2_parity"].get("cases", [])
            ],
            "numeric_tolerance": artifacts["h2_parity"].get("numeric_tolerance", {}),
            "historical_source": artifacts["h2_parity"].get("historical_source", {}),
            "api_compatibility": artifacts["h2_parity"].get("public_api_compatibility", {}),
            "manifest_summary": artifacts["h2_manifest"].get("summary", {}),
            "benchmark_summary": artifacts["h2_benchmark"].get("summary", {}),
        },
        "h3": {
            "feasible": artifacts["h3_feasibility"].get("feasible"),
            "feasibility_reason": artifacts["h3_feasibility"].get("feasibility_reason"),
            "verified_signals": artifacts["h3_feasibility"]
            .get("verified_signal_decisions", {})
            .get("strictly_verified_signals", []),
            "reference_session_count": artifacts["h3_feasibility"]
            .get("canonical_availability", {})
            .get("reference_session_count"),
            "reference_context_counts": artifacts["h3_feasibility"].get("reference_context_counts", {}),
            "rpm_tps_time_alignment": artifacts["h3_feasibility"].get("rpm_tps_time_alignment", {}),
            "window_representation": artifacts["h3_feasibility"].get("window_representation", {}),
            "unsupported_scope": artifacts["h3_feasibility"].get("unsupported_detector_scope", []),
            "decision": artifacts["h3_decision"].get("recommendation"),
            "decision_reason": artifacts["h3_decision"].get("recommendation_reason"),
            "distinct_information_observed": artifacts["h3_decision"].get("distinct_information_observed"),
        },
        "h3_1": {
            "recommendation": artifacts["h3_1_decision"].get("recommendation"),
            "answers": artifacts["h3_1_decision"].get("answers", {}),
            "basis": artifacts["h3_1_decision"].get("basis", {}),
            "contextual_event_count": artifacts["h3_1_review"].get("contextual_event_count"),
            "review_item_count": len(artifacts["h3_1_review"].get("items", [])),
            "disagreement_windows": artifacts["h3_1_disagreements"].get("total_disagreement_windows"),
            "disagreement_session_count": artifacts["h3_1_disagreements"].get("session_count"),
            "stability": {
                "event_count_stability": artifacts["h3_1_reference_stability"].get("event_count_stability", {}),
                "threshold_stability": artifacts["h3_1_reference_stability"].get("threshold_stability", {}),
                "interpretation": artifacts["h3_1_reference_stability"].get("stability_interpretation", {}),
            },
            "context_boundary": artifacts["h3_1_boundary"],
            "terminology": artifacts["h3_1_reference_audit"].get("terminology", {}),
        },
        "h4": {
            "decision": artifacts["h4_decision"].get("recommendation"),
            "answers": artifacts["h4_decision"].get("answers", {}),
            "basis": artifacts["h4_decision"].get("basis", {}),
            "summary": artifacts["h4_shadow"].get("summary", {}),
            "sensitivity": artifacts["h4_sensitivity"].get("event_count_stability", {}),
        },
        "h4_1": {
            "selection": artifacts["h4_1_selection"].get("selection"),
            "rationale": artifacts["h4_1_selection"].get("rationale"),
            "candidate_a": artifacts["h4_1_selection"].get("candidate_a_temporal_battery_shift", {}),
            "candidate_b": artifacts["h4_1_selection"].get("candidate_b_temporal_rpm_stability", {}),
            "target_feasible": artifacts["h4_1_target"].get("feasible"),
            "target_reason": artifacts["h4_1_target"].get("feasibility_reason"),
            "benchmark_summary": artifacts["h4_1_benchmark"].get("summary", {}),
        },
        "h5": {
            "active_detector_ids": artifacts["h5_contract"].get("active_detector_ids", list(ACTIVE_H5_DETECTOR_IDS)),
            "excluded_detector_ids": artifacts["h5_contract"].get(
                "excluded_detector_ids",
                list(REJECTED_TEMPORAL_CANDIDATE_IDS),
            ),
            "decision": artifacts["h5_decision"].get("recommendation"),
            "summary": h5_summary,
            "aggregation": h5_summary.get("aggregation", {}),
            "aggregate_events": h5_summary.get("aggregate_events", {}),
            "by_detector": h5_summary.get("by_detector", {}),
            "review_event_count": artifacts["h5_event_review"].get("event_count"),
            "review_event_type_counts": artifacts["h5_event_review"].get("event_type_counts", {}),
        },
        "h6": {
            "manifest_summary": artifacts["h6_manifest"].get("summary", {}),
            "metrics": h6_metrics,
            "decisions": artifacts["h6_decision"].get("detector_decisions", {}),
            "ground_truth_limitations": artifacts["h6_decision"].get("ground_truth_limitations", []),
        },
        "h7": {
            "decision": artifacts["h7_decision"].get("recommendation"),
            "answers": artifacts["h7_decision"].get("answers", {}),
            "risks": artifacts["h7_decision"].get("risks_and_limitations", []),
        },
        "h7_1": {
            "recommendation": artifacts["h7_1_decision"].get("overall_recommendation"),
            "answers": artifacts["h7_1_decision"].get("answers", {}),
            "case_count": artifacts["h7_1_dataset"].get("case_count"),
        },
        "h7_2": {
            "recommendation": artifacts["h7_2_decision"].get("overall_recommendation"),
            "summary": artifacts["h7_2_decision"].get("summary", {}),
            "per_rule_decisions": artifacts["h7_2_decision"].get("per_rule_decisions", {}),
            "semantic_boundary": artifacts["h7_2_decision"].get("semantic_boundary"),
            "unresolved": artifacts["h7_2_decision"].get("unresolved", []),
        },
        "h7_3": {
            "decision": artifacts["h7_3_decision"].get("recommendation"),
            "comparison": h7_3.get("comparison", {}),
            "v1": h7_3.get("v1", {}),
            "v2": h7_3.get("v2", {}),
            "check_count": len(artifacts["h7_3_checks"].get("checks", [])),
            "check_policy": artifacts["h7_3_checks"].get("policy", {}),
        },
        "h8": {
            "decision": artifacts["h8_decision"].get("recommendation"),
            "answers": artifacts["h8_decision"].get("answers", {}),
            "index_summary": h8_index_summary,
            "offline_summary": h8_offline_summary,
            "counterfactual_summary": h8_counterfactual_summary,
            "baseline_governance": artifacts["h8_governance"],
        },
        "h9": {
            "decision": artifacts["h9_decision"].get("recommendation"),
            "analyzer_side_ready": artifacts["h9_decision"].get("analyzer_side_ready"),
            "drivesafe_repo_available": drivesafe_available,
            "drivesafe_integration_completed": h9b_completed,
            "limitations": artifacts["h9_decision"].get("limitations", []),
            "payload_summary": h9_summary,
            "compatibility": h9_compat,
            "contract": diagnostic_evidence_contract(),
            "analyzer_diagnostic_evidence": analyzer_evidence,
        },
        "h9b": drivesafe_integration,
        "final_decision": "freeze_current_research_architecture",
    }
    return context


def component_inventory(ctx: dict[str, Any]) -> dict[str, Any]:
    components = [
        component(
            "Canonical telemetry schema",
            "app/domain/telemetry.py",
            ["decoded ECU samples", "decoder metadata", "signal definitions"],
            ["CanonicalTelemetrySession", "TelemetrySample"],
            "Defines the signal contract and verified/provisional signal metadata used by downstream features.",
            "production",
            "stable_foundation",
            ["ECU decoder", "canonical loaders"],
            ["feature engineering", "analysis API", "evaluation manifests"],
            "High for data shape; signal validity varies by signal and schema.",
        ),
        component(
            "Window feature engineering",
            "app/ml/features.py",
            ["canonical telemetry frame", "window boundaries", "schema signal columns"],
            ["per-window statistical feature frame"],
            "Computes fixed per-window statistical and relationship features used by detectors.",
            "production",
            "stable_foundation",
            ["canonical telemetry", "windowing"],
            ["Isolation Forest", "contextual detector", "temporal candidates"],
            "High for deterministic feature computation; not sufficient for waveform-level causal inference.",
        ),
        component(
            "Model Harness",
            "app/ml/harness.py",
            ["prepared feature frame", "registered detector instances", "DetectorContext"],
            ["HarnessResult", "DetectorResult summaries", "per-window detector payloads"],
            "Runs enabled detectors independently and preserves ok/skipped/failed states.",
            "production_infrastructure",
            "stable_foundation",
            ["detector contract", "prepared features"],
            ["production inference", "offline benchmarks", "evidence aggregation"],
            "Engineering-validated; it does not train, vote, fuse scores, or choose root causes.",
        ),
        component(
            "Isolation Forest detector",
            "app/ml/iforest_detector.py",
            ["prepared model feature columns", "isolation forest artifact", "robust scaler", "model metadata"],
            ["prediction", "score_sample", "decision_score", "window_health_score", "unusual features"],
            "Production compatibility baseline for multivariate anomaly detection.",
            "production",
            "retain_with_limitations",
            ["model artifacts", "scaler", "feature schema"],
            ["public analysis response", "diagnostic evidence", "offline benchmarks"],
            "Parity-validated against pre-H1 handoff; empirical evidence is controlled and sparse.",
        ),
        component(
            "Contextual Battery detector",
            "app/ml/contextual_detector.py",
            ["RPM median", "TPS raw/voltage median", "battery voltage window statistics", "reference distribution"],
            ["contextual robust-z score", "context", "threshold", "evidence"],
            "Research detector for battery-voltage deviations within explicit RPM/TPS operating contexts.",
            "research_active_shadow",
            "retain_with_limitations",
            ["verified signals", "nominal/reference split"],
            ["evidence aggregation", "RCA v2 research", "historical evidence"],
            "Stable under H3.1 reference review, but not production-gating and not a fault detector.",
        ),
        component(
            "Temporal Battery Shift detector",
            "app/ml/temporal_detector.py",
            ["within-session battery voltage sequence", "operating context"],
            ["EWMA shift statistic", "temporal event evidence"],
            "Offline candidate for sustained battery-voltage changes.",
            "rejected_research_candidate",
            "archive_for_reproducibility",
            ["prepared features", "context assignment"],
            ["H4/H4.1 evaluation artifacts only"],
            "Stable one-event behavior, but no unique event-level value beyond active detectors.",
        ),
        component(
            "Temporal RPM Stability detector",
            "app/ml/rpm_stability_detector.py",
            ["closed-throttle RPM dispersion features", "TPS features"],
            ["RPM stability statistic", "temporal event evidence"],
            "Offline candidate for sustained RPM variability increases during closed-throttle running.",
            "rejected_research_candidate",
            "archive_for_reproducibility",
            ["prepared features"],
            ["H4.1 evaluation artifacts only"],
            "Feasible but low-coverage, parameter-sensitive, and not justified as Core 3.",
        ),
        component(
            "Evidence Aggregator",
            "app/ml/evidence_aggregation.py",
            ["active detector DetectorResults", "window telemetry snapshot"],
            ["per-window aggregate state", "coverage status", "detector disagreement records"],
            "Preserves heterogeneous detector findings without fusion or consensus diagnosis.",
            "research_active_shadow",
            "advance_research",
            ["Isolation Forest", "Contextual Battery"],
            ["RCA v2", "historical evidence", "diagnostic payload examples"],
            "Useful for coverage/disagreement semantics; not a production decision-maker.",
        ),
        component(
            "RCA v1",
            "app/ml/root_cause_analysis.py",
            ["H5 aggregate events", "detector evidence"],
            ["possible hypotheses", "review manifest"],
            "Initial deterministic diagnostic hypothesis prototype.",
            "superseded",
            "rejected_semantics",
            ["evidence aggregation"],
            ["H7 reproducibility artifacts"],
            "Preserved for reproducibility; causal language was not supported by evidence.",
        ),
        component(
            "RCA v2",
            "app/ml/root_cause_analysis_v2.py",
            ["H5/H6/H8 cases", "RCA v2 rules", "source registry"],
            ["observations", "symptoms", "possible causes", "recommended checks", "limitations"],
            "Evidence-grounded interpretation engine that avoids unsupported causal claims.",
            "research_active_shadow",
            "promote_for_research",
            ["evidence aggregation", "literature/surrogate rule review"],
            ["historical evidence", "diagnostic evidence payloads"],
            "Research-worthy diagnostic interpretation; current possible-cause output count is zero.",
        ),
        component(
            "Historical Evidence",
            "app/evaluation/historical_evidence.py",
            ["prior structured observations", "RCA v2 results", "temporal cutoff"],
            ["recurrence", "persistence", "trend", "check-priority context"],
            "Offline cross-session evidence layer with leakage prevention and provenance.",
            "research_active_shadow",
            "advance_research",
            ["RCA v2", "H5 aggregate events", "evaluation manifest"],
            ["H9 research payloads"],
            "Adds prioritization and recurrence context; does not create causes.",
        ),
        component(
            "Diagnostic Evidence Contract",
            "app/integration/diagnostic_evidence.py",
            ["detector summaries", "optional research results", "session provenance"],
            ["versioned diagnostic_evidence JSON"],
            "Backward-compatible analyzer-side packaging for DriveSafe-style consumers.",
            "integrated_contract",
            "integrated_with_drivesafe",
            ["production inference summaries", "research payloads"],
            ["analysis API", "session processor", "DriveSafe Diagnostic Evidence Consumer"],
            "Analyzer-side contract is additive and DriveSafe consumes it without changing production anomaly semantics.",
        ),
        component(
            "DriveSafe Diagnostic Evidence Consumer",
            "DriveSafe Web App: services/analysis_submission.py, services/pi_sync.py, services/analysis.py, templates/session_detail.html",
            ["nullable diagnostic_evidence JSON", "stored AnalysisResult.result_summary", "session analysis snapshot"],
            ["raw diagnostic_evidence API field", "diagnostic_evidence_view", "collapsed research-evidence UI"],
            "Ingests, persists, serializes and renders research evidence while preserving existing status/health behavior.",
            "integrated_research_presentation",
            "integrated_with_boundaries",
            ["Diagnostic Evidence Contract", "DriveSafe AnalysisResult JSON persistence"],
            ["DriveSafe session detail API", "DriveSafe session-detail UI"],
            "Engineering integration is complete; it is not scientific validation of detector accuracy, RCA causes or historical maturity.",
        ),
    ]
    return {
        "schema_version": RA1_SCHEMA_VERSION,
        "components": components,
    }


def component(
    name: str,
    location: str,
    inputs: list[str],
    outputs: list[str],
    responsibility: str,
    state: str,
    status: str,
    dependencies: list[str],
    downstream_consumers: list[str],
    maturity: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "location": location,
        "inputs": inputs,
        "outputs": outputs,
        "responsibility": responsibility,
        "state": state,
        "dependencies": dependencies,
        "downstream_consumers": downstream_consumers,
        "status": status,
        "maturity": maturity,
    }


def detector_decision_matrix(ctx: dict[str, Any]) -> dict[str, Any]:
    h5_by_detector = ctx["h5"]["by_detector"]
    h6 = ctx["h6"]
    return {
        "schema_version": RA1_SCHEMA_VERSION,
        "active_detector_ids": ctx["research_active_detector_set"],
        "production_detector_ids": ctx["production_detector_set"],
        "rejected_detector_ids": ctx["rejected_research_detector_set"],
        "detectors": [
            {
                "detector_id": "isolation_forest",
                "component_state": "production_baseline_and_research_active",
                "location": "app/ml/iforest_detector.py",
                "scope": "Broad multivariate outlier detection over prepared window features.",
                "score_semantics": "score_sample lower is more anomalous; decision_score threshold is 0 for prediction.",
                "h5_observed": h5_by_detector.get("isolation_forest", {}),
                "h6_validation": h6["decisions"].get("isolation_forest", {}),
                "decision": "retain_with_limitations",
                "limitations": [
                    "Not a root-cause detector.",
                    "Cannot distinguish unusual-but-normal operation from faults without external evidence.",
                    "Controlled-condition recall is sparse and not universal accuracy.",
                ],
            },
            {
                "detector_id": "contextual_battery_voltage",
                "component_state": "research_active_shadow",
                "location": "app/ml/contextual_detector.py",
                "scope": "Context-conditioned battery-voltage deviation in modeled RPM/TPS contexts.",
                "score_semantics": "max robust-z over battery-voltage features; higher is more anomalous.",
                "h5_observed": h5_by_detector.get("contextual_battery_voltage", {}),
                "h6_validation": h6["decisions"].get("contextual_battery_voltage", {}),
                "decision": "retain_with_limitations",
                "limitations": [
                    "Not production-gating.",
                    "Requires verified RPM/TPS/battery features and modeled contexts.",
                    "Reports statistical deviation, not mechanical failure.",
                ],
            },
            {
                "detector_id": "temporal_battery_shift",
                "component_state": "rejected_research_candidate",
                "location": "app/ml/temporal_detector.py",
                "scope": "Within-session EWMA battery-voltage shifts.",
                "score_semantics": "change statistic against configured shift threshold; higher is more anomalous.",
                "h4_observed": ctx["h4"]["summary"].get("by_detector", {}).get("temporal_battery_shift", {}),
                "decision": ctx["h4_1"]["candidate_a"].get("recommendation", "reject"),
                "reason": "One stable event overlapped Core 1 and did not add unique event value.",
            },
            {
                "detector_id": "temporal_rpm_stability",
                "component_state": "rejected_research_candidate",
                "location": "app/ml/rpm_stability_detector.py",
                "scope": "Sustained RPM dispersion increases in closed-throttle running segments.",
                "score_semantics": "robust temporal dispersion statistic; higher is more anomalous.",
                "h4_1_observed": ctx["h4_1"]["candidate_b"],
                "decision": "reject",
                "reason": "Feasible candidate produced one Core3-only event but was low-coverage and parameter-sensitive.",
            },
        ],
    }


def evidence_maturity_matrix(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": RA1_SCHEMA_VERSION,
        "evidence_hierarchy": [
            "independent physical ground truth",
            "qualified expert judgment",
            "OEM or normative documentation",
            "peer-reviewed engineering literature",
            "internal controlled empirical evidence",
            "internal observational evidence",
            "engineering assumption",
        ],
        "entries": [
            maturity("ECU decoder", True, True, False, "high_for_verified_signals", "RPM, TPS raw/voltage and battery voltage are treated as verified in v2; IAT/ECT remain high-confidence/provisional for research constraints."),
            maturity("Isolation Forest", True, True, False, "controlled_internal_evidence", "H2 parity passed; H6 controlled recall was 3/3 on sparse condition-level cases, with no precision/FPR proof."),
            maturity("Contextual Battery", False, True, False, "research_observational_plus_sparse_controlled", "H3/H3.1 support stability and distinct evidence; H6 controlled recall was 1/1 for a relevant controlled low-battery case."),
            maturity("Temporal Battery", False, False, True, "negative_experiment", "H4 found one stable event but H4.1 rejected it for lack of unique value."),
            maturity("Temporal RPM", False, False, True, "negative_experiment", "H4.1 found one Core3-only event but rejected the detector because coverage and parameter sensitivity were not sufficient."),
            maturity("Evidence Aggregator", False, True, False, "engineering_validated_research", "H5 explicitly preserved coverage and disagreement without voting or score fusion."),
            maturity("RCA v1", False, False, True, "unsupported_semantics", "H7 generated hypotheses, but H7.2/H7.3 removed unsupported causal language."),
            maturity("RCA v2", False, True, False, "research_interpretation", "H7.3 produced 95 observations, 8 symptoms, 0 possible causes and 109 checks over 33 cases."),
            maturity("Historical Evidence", False, True, False, "research_observational", "H8 indexed 716 records across 38 sessions and changed check priorities without changing causal hypotheses."),
            maturity("Diagnostic Evidence Contract", True, True, False, "integrated_contract", "H9 analyzer payload is additive and backwards-compatible; H9B confirms DriveSafe persistence/API/UI consumption."),
            maturity("DriveSafe Diagnostic Evidence Consumer", True, True, False, "integrated_research_presentation", "H9B integrates versioned research evidence into DriveSafe review surfaces without changing production status, health, alerts or completion semantics."),
        ],
    }


def maturity(
    component_name: str,
    production: bool,
    research: bool,
    rejected: bool,
    evidence_maturity: str,
    basis: str,
) -> dict[str, Any]:
    return {
        "component": component_name,
        "production": production,
        "research": research,
        "rejected": rejected,
        "evidence_maturity": evidence_maturity,
        "basis": basis,
    }


def research_production_boundary(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": RA1_SCHEMA_VERSION,
        "matrix": evidence_maturity_matrix(ctx)["entries"],
        "component_status": [
            {"component": "ECU decoder", "status": "validated data layer"},
            {"component": "Isolation Forest", "status": "production baseline"},
            {"component": "Contextual Battery", "status": "research/shadow"},
            {"component": "Temporal Battery", "status": "archived/rejected"},
            {"component": "Temporal RPM", "status": "archived/rejected"},
            {"component": "Evidence Aggregator", "status": "research"},
            {"component": "RCA v1", "status": "historical/replaced"},
            {"component": "RCA v2", "status": "research interpretation"},
            {"component": "Historical Evidence", "status": "research"},
            {"component": "Diagnostic Evidence Contract", "status": "integrated"},
            {
                "component": "DriveSafe Diagnostic Evidence Consumer",
                "status": "integrated research presentation",
            },
        ],
        "production_path": [
            "canonical telemetry",
            "window feature engineering",
            "Model Harness with Isolation Forest",
            "public anomaly fields",
            "existing DriveSafe status/health behavior",
        ],
        "research_path": [
            "Contextual Battery detector",
            "Evidence Aggregator",
            "RCA v2 interpretation",
            "Historical Evidence",
            "Diagnostic Evidence Contract",
            "DriveSafe collapsed diagnostic evidence UI",
        ],
        "rejected_path": [
            "Temporal Battery detector as Core 3",
            "Temporal RPM detector as Core 3",
            "RCA v1 causal hypothesis semantics",
        ],
        "policy": {
            "production_anomaly_status_changed_by_research": False,
            "score_fusion_used": False,
            "voting_used": False,
            "skipped_or_failed_detector_is_normal": False,
            "no_evidence_is_verified_healthy": False,
            "drive_safe_behavior_changed": False,
            "research_evidence_changes_drivesafe_status": False,
            "research_evidence_changes_health_score": False,
            "research_evidence_changes_alerts_or_completion": False,
        },
    }


def knowledge_claims(ctx: dict[str, Any]) -> dict[str, Any]:
    claims = [
        claim("Decoded RPM is valid enough for v2 ML context", "H3 feasibility verified signal audit", "internal controlled empirical evidence", "provisionally_supported", "Independent calibration spot checks would strengthen it."),
        claim("TPS voltage/raw are valid enough for v2 ML context", "H3 feasibility verified signal audit", "internal controlled empirical evidence", "provisionally_supported", "Continue to preserve raw provenance."),
        claim("Battery voltage is valid enough for contextual scoring", "H3 feasibility and external VOM comparison noted in calibration decisions", "internal controlled empirical evidence", "provisionally_supported", "Synchronized external voltage traces are still needed for fault validation."),
        claim("IAT and ECT can be used for contextual fault detection", "H3 explicitly excluded them from strict verified signal set", "internal observational evidence", "unsupported_for_current_contextual_detector", "Do not use until strict verification is established."),
        claim("Isolation Forest detects unusual multivariate patterns", "H2 parity and H6 sparse controlled-condition response", "internal controlled empirical evidence", "supported_with_limitations", "Not equivalent to fault diagnosis or universal accuracy."),
        claim("Contextual battery detector identifies context-conditioned voltage deviations", "H3/H3.1 events, stability and boundary review", "internal observational evidence", "provisionally_supported", "Manual/physical verification still needed."),
        claim("Temporal battery detector adds unique information", "H4/H4.1 overlap results", "internal observational evidence", "contradicted_for_current_data", "Reopen only with a temporal phenomenon current detectors miss."),
        claim("Temporal RPM detector should be Core 3", "H4.1 candidate selection", "internal observational evidence", "unsupported", "Low coverage and parameter sensitivity block promotion."),
        claim("RCA can determine root cause", "H7.3 possible cause count is zero", "internal + literature/surrogate review", "unsupported", "Requires physical/expert evidence-to-cause mappings."),
        claim("RCA v2 can organize evidence and checks", "H7.3 observations/symptoms/checks with no causes", "internal deterministic verification", "supported_with_limitations", "Better described as diagnostic evidence interpretation."),
        claim("Recurrence can prioritize checks", "H8 counterfactual: 22 cases changed check priority, 0 causal changes", "internal observational evidence", "provisionally_supported", "Prioritization is not failure probability."),
        claim("No detector evidence means the vehicle is healthy", "H5/H9 policy", "engineering policy + absence of ground truth", "unsupported", "No-evidence is a bounded detector result, not verified normality."),
        claim("DriveSafe can consume and display diagnostic research evidence without changing production analysis", "H9B DriveSafe ingestion, persistence, API and collapsed UI checks", "engineering integration evidence", "supported", "Does not validate detector accuracy, RCA causality or historical-evidence maturity."),
        claim("Persisted/displayed research evidence is scientifically validated", "H9B integration only", "engineering integration evidence", "unsupported", "Scientific promotion still requires physical, expert or independently validated evidence."),
    ]
    return {
        "schema_version": RA1_SCHEMA_VERSION,
        "claims": claims,
    }


def claim(
    statement: str,
    evidence: str,
    evidence_level: str,
    support_status: str,
    validation_needed: str,
) -> dict[str, Any]:
    return {
        "claim": statement,
        "evidence": evidence,
        "evidence_level": evidence_level,
        "support_status": support_status,
        "validation_needed": validation_needed,
    }


def architecture_overview(ctx: dict[str, Any]) -> str:
    h2 = ctx["h2"]
    h3 = ctx["h3"]
    h5 = ctx["h5"]
    h7_3 = ctx["h7_3"]
    h8 = ctx["h8"]
    h9 = ctx["h9"]
    h9b = ctx["h9b"]
    return f"""# RA1 Architecture Overview

RA1.1 reconciliation status: H9 analyzer to DriveSafe integration completed: `{h9['drivesafe_integration_completed']}`.

## Executive Summary

The analyzer currently solves a bounded problem: it turns canonical ECU telemetry into deterministic window features, runs a production-compatible Isolation Forest anomaly detector, and preserves additive research evidence about detector coverage, disagreement, evidence interpretation and history. The system does **not** currently prove root causes, vehicle health, component failure probability, or future failure risk.

The smallest justified architecture is:

1. Canonical telemetry and deterministic window features.
2. Model Harness with Isolation Forest as the production baseline.
3. Additive diagnostic evidence metadata that never changes public anomaly status.
4. Research extensions for Contextual Battery, evidence aggregation, RCA v2 and historical evidence, now presented in DriveSafe as collapsed research evidence.

The current research architecture should be frozen at two active detectors: `isolation_forest` and `contextual_battery_voltage`. Both temporal candidates are archived as negative experiments.

## Architecture Map

```mermaid
flowchart TD
    RAW[RAW ECU] --> CT[Canonical Telemetry]
    CT --> MH[Model Harness]
    MH --> IF[Isolation Forest production-compatible result]
    MH --> CB[Contextual Battery research shadow]
    MH -. archived/rejected .-> TB[Temporal Battery archive]
    MH -. archived/rejected .-> TR[Temporal RPM archive]
    IF --> PROD[Existing DriveSafe status and health behavior]
    IF --> AG[Evidence Aggregator research]
    CB --> AG
    AG --> RCA[RCA v2 / Diagnostic Evidence Interpretation]
    RCA --> HIST[Historical Evidence research]
    HIST --> DEC[Diagnostic Evidence Contract]
    PROD --> DS[DriveSafe Persistence / API]
    DEC --> DS
    DS --> UI[Collapsed Diagnostic Evidence UI]
```

Production: Isolation Forest production-compatible result and existing DriveSafe status/health behavior.

Research evidence: Contextual Battery, evidence aggregation, RCA v2, historical evidence and the collapsed diagnostic evidence UI.

Archived experiments: Temporal Battery detector, Temporal RPM detector and H7 RCA v1 causal interpretation.

## Production Path

Production analysis uses `app/services/analysis_service.py` and `app/ml/inference.py`. The live harness is instantiated with only `IsolationForestDetector`, so existing predictions, anomaly events, health scores and DriveSafe-compatible response fields remain anchored to the Isolation Forest. H2 parity passed on the checked-in handoff snapshot using exact comparisons for discrete outputs and `atol={h2['numeric_tolerance'].get('float_atol')}`, `rtol={h2['numeric_tolerance'].get('float_rtol')}` for floats. The historical Git checkout was unavailable because the workspace has no `.git`, so the pre-H1 reference is the checked-in Pi handoff source snapshot.

## Research Path

The research path keeps Core 2, Evidence Aggregation, RCA v2 and Historical Evidence offline/shadow. H3 established narrow feasibility for contextual battery-voltage detection using verified signals: {comma(h3['verified_signals'])}. H3.1 found {ctx['h3_1']['contextual_event_count']} contextual events, {ctx['h3_1']['disagreement_windows']} disagreement windows, and stable leave-one-reference-session-out behavior. H5 then aggregated two active detectors over {h5['aggregation'].get('window_count')} windows without score fusion.

## Rejected Research Path

Temporal Battery and Temporal RPM remain implemented for reproducibility but are not active detectors. H4/H4.1 found the battery temporal candidate had one stable event but no unique event value. The RPM candidate had one Core3-only event, but low coverage and parameter sensitivity made promotion unjustified.

## Diagnostic Interpretation

RCA v2 is better understood architecturally as a deterministic Diagnostic Evidence Interpretation Engine. It retains the RCA name for compatibility, but H7.3 produced {h7_3['v2'].get('observation_count')} observations, {h7_3['v2'].get('symptom_count')} symptoms, {h7_3['v2'].get('possible_cause_count')} possible causes and {h7_3['v2'].get('recommended_check_count')} recommended checks. This is preferable to preserving H7 v1's unsupported causal language.

## Historical Evidence

H8 uses structured prior observations rather than generic memory. It indexed {h8['index_summary'].get('record_count')} records across {h8['index_summary'].get('session_count')} sessions. It changed check priority in {h8['counterfactual_summary'].get('cases_with_check_priority_change')} cases and changed causal hypotheses in {h8['counterfactual_summary'].get('cases_with_causal_hypothesis_change')} cases.

## DriveSafe Integration Boundary

H9 makes the analyzer-side `diagnostic_evidence` payload additive and backward-compatible. H9B confirms DriveSafe Web App integration: repository available `{h9['drivesafe_repo_available']}`, completed `{h9['drivesafe_integration_completed']}`. DriveSafe tolerantly ingests optional evidence, persists valid evidence in existing `AnalysisResult.result_summary` JSON, exposes raw evidence plus a display-only `diagnostic_evidence_view`, and renders the collapsed research-evidence section. Production anomaly/status/health semantics remain unchanged. Integration notes: {semicolon(h9b.get('notes', []))}
"""


def decision_timeline(ctx: dict[str, Any]) -> str:
    return f"""# RA1 Decision Timeline

## H1

Hypothesis: A minimal multi-detector harness can be introduced without changing Isolation Forest behavior.

Implementation: `DetectorResult`, `DetectorIdentity`, `ModelHarness`, and an Isolation Forest detector wrapper.

Observed result: Production inference still uses the existing artifacts and behavior through the harness.

Decision: Advance.

Consequence: Evaluation could register additional detectors without modifying the Isolation Forest implementation.

## H2

Hypothesis: H1 preserved pre-harness behavior.

Implementation: Independent parity validation against the checked-in Pi handoff source snapshot and a reusable benchmark manifest.

Observed result: Parity passed for {len(ctx['h2']['parity_cases'])} handoff cases; public API compatibility passed. Git history checkout was unavailable, so the handoff snapshot is the historical reference.

Decision: Advance with documented limitation.

Consequence: Later detector research could run without changing production inference.

## H3

Hypothesis: Verified telemetry supports a contextual statistical detector.

Implementation: Contextual Battery detector using verified RPM/TPS/battery features and nominal/reference sessions.

Observed result: Feasible: {ctx['h3']['feasible']}; reason: {ctx['h3']['feasibility_reason']}.

Decision: {ctx['h3']['decision']}.

Consequence: Core 2 entered offline shadow evaluation only.

## H3.1

Hypothesis: Core 2 adds stable, interpretable information beyond Isolation Forest.

Implementation: Disagreement classification, manual review manifest, reference leave-one-session-out stability, and context-boundary review.

Observed result: {ctx['h3_1']['contextual_event_count']} contextual events, {ctx['h3_1']['disagreement_windows']} disagreement windows, no reference/boundary artifact dominance.

Decision: {ctx['h3_1']['recommendation']}.

Consequence: Contextual Battery remains the only active research detector beyond Isolation Forest.

## H4

Hypothesis: Temporal battery shifts add complementary evidence.

Implementation: EWMA within-session Temporal Battery Shift detector.

Observed result: {ctx['h4']['basis'].get('temporal_event_count')} temporal event, {ctx['h4']['basis'].get('core3_only_event_count')} Core3-only events.

Decision: {ctx['h4']['decision']}.

Consequence: A revised Core 3 target was explored rather than promoting battery temporal shifts.

## H4.1

Hypothesis: Temporal RPM stability might be a better Core 3 target.

Implementation: Closed-throttle RPM stability candidate plus candidate selection audit.

Observed result: Candidate selection: `{ctx['h4_1']['selection']}`. Rationale: {ctx['h4_1']['rationale']}.

Decision: Reject both temporal candidates.

Consequence: The active detector set stayed at two.

## H5

Hypothesis: Heterogeneous detector evidence can be aggregated without voting or score fusion.

Implementation: Evidence Aggregator with states for no evidence, single/multiple detector evidence, disagreement and insufficient coverage.

Observed result: {ctx['h5']['review_event_count']} aggregate events; evidence states: {counts(ctx['h5']['aggregation'].get('evidence_state_counts', {}))}.

Decision: {ctx['h5']['decision']}.

Consequence: Aggregated evidence became the input to RCA v2 and history.

## H6

Hypothesis: Active detectors have enough evidence to remain in the research path.

Implementation: Controlled-condition validation and blind-review infrastructure.

Observed result: IF controlled recall was 3/3; Contextual Battery controlled recall was 1/1. Precision, false-alert rate and event-level recall were not computed.

Decision: Retain both active detectors with limitations.

Consequence: Validation guarded against overclaiming while allowing research to continue.

## H7

Hypothesis: Evidence-based deterministic RCA can generate useful diagnostic hypotheses.

Implementation: RCA v1 rules and expert review packet.

Observed result: {ctx['h7']['answers'].get('events_with_hypotheses')} events with hypotheses and {ctx['h7']['answers'].get('events_returning_insufficient_evidence')} insufficient-evidence events; all hypotheses required expert or physical validation.

Decision: {ctx['h7']['decision']}.

Consequence: Expert validation was required before any semantic promotion.

## H7.1

Hypothesis: Expert validation can validate RCA rules.

Implementation: Expert validation dataset and judgment schema.

Observed result: {ctx['h7_1']['answers'].get('completed_hypothesis_judgment_count')} completed judgments and {ctx['h7_1']['answers'].get('independently_confirmed_physical_root_cause_count')} independently confirmed physical root causes.

Decision: {ctx['h7_1']['recommendation']}.

Consequence: RCA claims could not be expert-validated yet.

## H7.2

Hypothesis: Literature-grounded surrogate review can identify unsupported RCA semantics.

Implementation: Rule grounding, counterexample analysis and structural verification.

Observed result: {counts(ctx['h7_2']['summary'].get('per_rule_decision_counts', {}))} with semantic boundary: {ctx['h7_2']['semantic_boundary']}.

Decision: {ctx['h7_2']['recommendation']}.

Consequence: RCA v1 needed semantic revision.

## H7.3

Hypothesis: RCA v2 can preserve useful interpretation while removing unsupported causes.

Implementation: RCA v2 observation/symptom/check engine.

Observed result: {ctx['h7_3']['comparison'].get('unsupported_claims_removed')} unsupported v1 claims removed; possible causes: {ctx['h7_3']['v2'].get('possible_cause_count')}; checks: {ctx['h7_3']['v2'].get('recommended_check_count')}.

Decision: {ctx['h7_3']['decision']}.

Consequence: RCA v2 became the current research interpretation layer.

## H8

Hypothesis: Structured history adds information unavailable from one session.

Implementation: Historical observation index, recurrence/trend comparisons and check-priority augmentation.

Observed result: {ctx['h8']['index_summary'].get('record_count')} records, {ctx['h8']['offline_summary'].get('cases_with_usable_history')} usable-history cases, {ctx['h8']['counterfactual_summary'].get('cases_with_check_priority_change')} check-priority changes, {ctx['h8']['counterfactual_summary'].get('cases_with_causal_hypothesis_change')} causal changes.

Decision: {ctx['h8']['decision']}.

Consequence: History remained a research evidence layer, not baseline training or diagnosis.

## H9

Hypothesis: Analyzer evidence can be packaged for DriveSafe without changing APIs.

Implementation: Additive `diagnostic_evidence` contract and retrospective payload examples.

Observed result: {ctx['h9']['payload_summary'].get('payload_count')} payloads; DriveSafe repo available: {ctx['h9']['drivesafe_repo_available']}; analyzer side ready: {ctx['h9']['analyzer_side_ready']}.

Decision: {ctx['h9']['decision']}.

Consequence: Analyzer-side integration became ready for H9B DriveSafe consumption.

## H9B

Hypothesis: DriveSafe can consume, persist, serialize and render versioned diagnostic research evidence without changing production analysis semantics.

Implementation: Backward-compatible optional `diagnostic_evidence` ingestion in analyzer and Pi import paths, JSON persistence in existing `AnalysisResult.result_summary`, additive API/session serialization, display-only `diagnostic_evidence_view`, and a collapsed "Additional diagnostic evidence" UI section.

Observed result: Analyzer emits additive diagnostic evidence: {ctx['h9']['analyzer_diagnostic_evidence']['emits_additive_diagnostic_evidence']}; DriveSafe integration completed: {ctx['h9']['drivesafe_integration_completed']}; DriveSafe checks: {counts(ctx['h9b'].get('checks', {}))}.

Decision: Complete integration as an engineering boundary only.

Consequence: H9/H9B is an integration contribution, not scientific validation of detector accuracy, RCA causes or historical evidence.
"""


def rejected_alternatives(ctx: dict[str, Any]) -> str:
    rows = [
        ("Three-model requirement", "A third detector was requested as Core 3.", "H4/H4.1 did not produce a justified third active detector.", "A validated phenomenon current detectors demonstrably miss."),
        ("Temporal Battery detector", "Sustained voltage shifts are plausible temporal evidence.", "One stable event but zero unique event value.", "Independent temporal voltage phenomenon with unique, verified value."),
        ("Temporal RPM detector", "Closed-throttle RPM instability might be complementary.", "Low coverage and parameter sensitivity despite one Core3-only event.", "Repeatable, validated RPM-stability phenomenon with adequate coverage."),
        ("Majority voting", "Multiple detectors invite a consensus rule.", "Only two active detectors exist and detector scopes differ.", "At least three validated detectors with calibrated comparable decision semantics."),
        ("Score fusion", "A single score is attractive for UI simplicity.", "Scores use incompatible semantics and scales.", "Calibration study proving cross-detector score comparability."),
        ("Universal anomaly score", "Could simplify downstream consumers.", "Would hide detector-local meaning and skipped/failed states.", "Validated transformation preserving detector semantics and uncertainty."),
        ("Arbitrary confidence percentages", "Could appear user-friendly.", "No probability calibration or ground-truth base rates exist.", "Calibrated probabilistic model with verified labels."),
        ("RCA v1 causal interpretation", "Initial H7 hypothesis engine explored diagnostic language.", "H7.2/H7.3 found 52 unsupported causal claims.", "Physical/expert evidence-to-cause mappings."),
        ("Automatic baseline learning", "History could update normal behavior.", "Risks contaminating baseline with anomalies.", "Governed baseline approval workflow and verified normal evidence."),
        ("Vector DB / embedding memory", "Could retrieve similar historical cases.", "Structured keys were sufficient and more reproducible.", "Unstructured evidence corpus where deterministic retrieval fails."),
        ("LLM RCA", "LLMs could phrase or reason over diagnostics.", "Current needs are deterministic, traceable and testable.", "A demonstrated natural-language/tool reasoning requirement that deterministic logic cannot meet."),
        ("Agent harness", "Agents could orchestrate diagnosis.", "Autonomy would reduce reproducibility and add failure modes.", "Human-approved workflow requiring adaptive tool use beyond fixed evaluation logic."),
        ("Deep temporal network", "LSTM/Transformer models are technically possible.", "Data and labels do not justify complexity.", "Large validated temporal dataset and simpler-method failure on a meaningful task."),
    ]
    text = ["# RA1 Rejected Alternatives", ""]
    for name, considered, rejected, reconsider in rows:
        text.extend(
            [
                f"## {name}",
                "",
                f"Why considered: {considered}",
                "",
                f"Why rejected or deferred: {rejected}",
                "",
                f"Evidence to reconsider: {reconsider}",
                "",
            ]
        )
    return "\n".join(text)


def dependency_map(ctx: dict[str, Any]) -> str:
    return """# RA1 Architectural Dependency Map

```mermaid
flowchart TD
    RAW[RAW ECU] --> CT[Canonical Telemetry]
    CT --> MH[Model Harness]
    MH --> IF[Isolation Forest]
    MH --> CB[Contextual Battery]
    MH -. archived .-> TB[Temporal Battery]
    MH -. archived .-> TR[Temporal RPM]
    IF --> PR[Production public response]
    IF --> AG[Evidence Aggregator]
    CB --> AG
    AG --> RCA[RCA v2 / Diagnostic Evidence Interpretation]
    RCA --> HIST[Historical Evidence]
    HIST --> DIAG[Diagnostic Evidence Contract]
    PR --> DS[DriveSafe Persistence / API]
    DIAG --> DS
    DS --> UI[Collapsed Diagnostic Evidence UI]
```

## True Dependencies

Canonical telemetry is required by every path. Window features are required by all current detectors. The Model Harness is required for detector execution and failure isolation. Isolation Forest is required for current production anomaly status. Contextual Battery is required only for the research evidence path.

Evidence Aggregation depends on detector outputs. Without active detector outputs it can still report insufficient coverage, but it has no positive/negative evidence relationship to interpret. RCA v2 depends on aggregated evidence; without aggregation it becomes a generic rules engine with no case evidence. Historical Evidence depends on structured observations and temporal cutoff metadata; without RCA v2/H5 artifacts it has no current case representation to compare.

## Removal Impact

If Contextual Battery were removed tomorrow, production anomaly behavior would not break. The research system would lose contextual voltage evidence, disagreement states involving Core 2, and the main reason to keep heterogeneous aggregation beyond a single-detector schema.

If Evidence Aggregation were removed, RCA v2 and H8 would lose their clean input contract. Production Isolation Forest predictions would still run.

If Historical Evidence were removed, current-session interpretation would still work, but recurrence, persistence, trend and check-priority context would disappear.

If `diagnostic_evidence` were omitted from an API response, old clients should still work; H9 marks it additive and nullable, and H9B verifies DriveSafe legacy behavior. DriveSafe owns ingestion, persistence, API presentation and UI. It does not duplicate detector inference, aggregation semantics, RCA rules or historical recurrence/trend computation.
"""


def failure_mode_audit(ctx: dict[str, Any]) -> str:
    return """# RA1 Failure-Mode Audit

| Failure Mode | Current Representation | Not Equivalent To |
|---|---|---|
| Isolation Forest fails | DetectorResult status `failed`, production inference degrades to unavailable warning/status behavior. | A normal prediction. |
| Isolation Forest missing features | Harness marks missing features; production inference raises compatibility error for primary detector. | A negative anomaly result. |
| Contextual detector skips | `skipped`, `missing_features`, `no_applicable_context` or per-window not-applicable evidence. | Healthy battery voltage. |
| Both active detectors unavailable | Aggregate state `insufficient_coverage`. | No evidence or normal vehicle. |
| Historical comparison unavailable | History status unavailable and recurrence `historical_comparison_unavailable`. | First observed or non-recurrent pattern. |
| RCA cannot interpret event | RCA v2 emits limitations and may leave possible causes empty. | Confirmed absence of a cause. |
| Evidence payload missing | H9 says existing consumers can ignore nullable/additive field. | Analyzer failure if public anomaly fields exist. |
| Malformed diagnostic evidence | H9B DriveSafe ingestion drops malformed optional evidence and records a warning where safely possible. | Production analysis failure. |
| Legacy DriveSafe session | No diagnostic evidence section is rendered and existing session analysis still works. | Negative research evidence. |
| Analyzer failure | Existing DriveSafe analyzer failure state remains separate from any research evidence presentation. | A research detector result. |
| Contextual detector unavailable | Analyzer/aggregator marks unavailable/skipped/not-applicable states. | Healthy battery voltage. |
| Insufficient detector coverage | Aggregate state `insufficient_coverage`; DriveSafe must render it as research evidence, not normal. | Normal analysis. |
| Historical evidence unavailable | History/provenance says unavailable or preserves cutoff metadata. | Non-recurrent observation. |
| Zero possible causes | RCA v2 may return an empty possible-cause list. | Error or "no diagnosis available" warning. |
| Research evidence rendering unavailable | DriveSafe exposes raw `diagnostic_evidence` and builds display-only `diagnostic_evidence_view`; absent/malformed evidence hides the research section while primary result remains unchanged. | Production anomaly/status/health mutation. |
| Old session lacks research metadata | Research evidence sections become unavailable or incomplete. | Negative research evidence. |

The important invariant is that skipped, failed, not applicable and unavailable are separate states. None should be normalized into a healthy/normal vote.
"""


def complexity_audit(ctx: dict[str, Any]) -> str:
    return """# RA1 Complexity Audit

| Component | Classification | Distinct Contribution | Duplication / Removal Note |
|---|---|---|---|
| Canonical telemetry | Necessary | Shared source of deterministic signal data. | Cannot remove without replacing the pipeline. |
| Window features | Necessary | Stable feature contract for detectors. | Required by production and research. |
| Model Harness | Necessary | Detector lifecycle and failure isolation. | Small enough to keep. |
| Isolation Forest | Necessary | Only production anomaly detector. | Required for current behavior. |
| Contextual Battery | Useful but optional research | Context-specific voltage evidence IF can miss or disagree with. | Remove only if future validation contradicts usefulness. |
| Temporal Battery | Rejected | Stable but no unique event value. | Archive for reproducibility; do not keep active. |
| Temporal RPM | Rejected | Possible distinct event but not stable/covered enough. | Archive for reproducibility; do not keep active. |
| Evidence Aggregator | Useful research infrastructure | Coverage/disagreement semantics without fusion. | Valuable while more than one detector is researched. |
| RCA v1 | Candidate for removal from active docs | Reproducibility of old hypothesis behavior. | Superseded by v2; keep archived artifacts only. |
| RCA v2 | Research-only | Separates observations, symptoms, checks and causes. | Rename conceptually to Diagnostic Evidence Interpretation Engine. |
| Historical Evidence | Research-only | Recurrence/trend/check-priority context. | Keep offline until validated with user-facing workflow. |
| Diagnostic Evidence Contract | Useful integration | Additive stable payload boundary. | Integrated end-to-end through H9B. |
| DriveSafe Diagnostic Evidence Consumer | Necessary integration surface | Persistence/API/UI presentation of analyzer-owned evidence. | Does not duplicate detector inference, aggregation, RCA rules or historical recurrence/trend computation. |

Current unnecessary complexity in the active architecture is mainly the temptation to keep temporal candidates active. The audit recommendation is to preserve their artifacts but exclude them from the active research detector set.

RA1.1 duplication check: Analyzer owns analysis logic. DriveSafe owns ingestion, persistence, API presentation and UI. No H9B duplication of detector inference, aggregation semantics, RCA rules or historical recurrence/trend computation was found.
"""


def contribution_audit(ctx: dict[str, Any]) -> str:
    return """# RA1 Contribution Audit

## Engineering Contributions

- Reproducible multi-detector harness with explicit `ok`, `skipped` and `failed` outcomes.
- Backward-compatible Isolation Forest wrapper preserving production behavior.
- Heterogeneous evidence aggregation that avoids score fusion and voting.
- Additive diagnostic evidence contract for downstream consumers.
- Backward-compatible integration of versioned diagnostic research evidence into the operational DriveSafe application while preserving the production-analysis boundary.
- Structured historical evidence index with temporal cutoff and provenance.

## Research Findings

- Context-conditioned battery-voltage detection is feasible with the verified signals currently available.
- Core 2 provides distinct research evidence from Isolation Forest but remains non-production.
- Temporal Battery and Temporal RPM candidates are not justified as Core 3 on current evidence.
- RCA v1's causal semantics were unsupported; RCA v2 is more defensible as evidence interpretation.
- Historical evidence changes check priority, not causal conclusions.

## Unverified Hypotheses

- Contextual voltage deviations correspond to physical charging-system faults.
- Detector disagreements map to specific mechanical causes.
- Historical recurrence improves user outcomes.
- Any current detector has reliable real-world precision, false-alert rate or broad recall.
"""


def future_work_triggers(ctx: dict[str, Any]) -> str:
    return """# RA1 Future Work Trigger Matrix

| Future Work | Trigger Required |
|---|---|
| Add a third detector | A measurable anomaly phenomenon exists that current active detectors demonstrably miss, with adequate coverage and stability. |
| Promote Contextual Battery | Additional controlled or ground-truth validation confirms useful detection and acceptable false-alert behavior. |
| Reopen Temporal Battery | A temporal voltage event class appears that is independent of IF/Core 2 and physically meaningful. |
| Reopen Temporal RPM | Closed-throttle RPM instability shows repeatable, parameter-stable behavior across independent captures. |
| Promote RCA cause generation | Physical or qualified-expert evidence establishes specific evidence-to-cause relationships. |
| Add score fusion | Detector scores are calibrated onto a common probabilistic or decision scale. |
| Add deep learning | Simpler methods fail on a validated task and enough labeled temporal data exists. |
| Add LLM/agent reasoning | Natural-language reasoning or adaptive tool orchestration becomes a demonstrated requirement not served by deterministic logic. |
| Update nominal baselines automatically | A governed baseline approval process and independent normal evidence prevent contamination. |
| Extend DriveSafe evidence presentation | A new analyzer contract version or user-review workflow requires additional display fields; production status semantics must still remain separate. |
"""


def final_recommended_architecture(ctx: dict[str, Any]) -> str:
    return f"""# RA1 Final Recommended Architecture

Final decision: `{ctx['final_decision']}`.

## Required Core

- Canonical telemetry schema and deterministic feature engineering.
- Model Harness.
- Isolation Forest production detector using the existing model artifacts, scaler and thresholds.
- Existing public analysis response fields.
- Additive `diagnostic_evidence` contract with unavailable sections allowed.
- DriveSafe ingestion, persistence and session-review presentation that preserves existing production status/health behavior.

## Research Extensions

- Contextual Battery detector in offline/shadow mode.
- Evidence Aggregator over Isolation Forest and Contextual Battery.
- RCA v2, interpreted as a Diagnostic Evidence Interpretation Engine.
- Historical Evidence for recurrence, persistence, trend and check-priority context.

## Archived Experiments

- Temporal Battery Shift detector.
- Temporal RPM Stability detector.
- RCA v1 causal-hypothesis semantics.

## Completed Integration

- H9B DriveSafe Web App storage/API/UI for `diagnostic_evidence` is complete as an engineering integration.
- The completed integration does not promote research evidence to production certification.

## Pending Validation

- Expert validation remains pending because H7.1 contains no completed expert judgments.

## Components Not To Develop Further Without New Evidence

- Core 3 detector work.
- Score fusion or majority voting.
- Causal RCA output.
- Automatic baseline learning.
- LLM/agent diagnosis.
- Deep temporal networks.

## Freeze Meaning

`freeze_current_research_architecture` does not mean every component is scientifically validated, research evidence is production-certified, RCA root causes are established, or no future work is possible.

It means no additional architectural capability is currently justified without new evidence.
"""


def audit_report(ctx: dict[str, Any]) -> str:
    h2 = ctx["h2"]
    h5 = ctx["h5"]
    h6 = ctx["h6"]
    h7_3 = ctx["h7_3"]
    h8 = ctx["h8"]
    h9 = ctx["h9"]
    return f"""# RA1 Audit Report

## Final Decision

`{ctx['final_decision']}`.

The current research architecture is coherent enough to freeze: production remains Isolation Forest only, the research path has exactly two active detectors, rejected temporal candidates are visible, RCA v2 no longer overclaims causality, and diagnostic evidence integration is additive through DriveSafe. No simplification is required before freeze except keeping temporal candidates archived rather than active.

## Final Audit Questions

1. What problem does the current system actually solve?

It detects and reports unusual ECU telemetry patterns from canonical sessions, preserves detector-local evidence, and organizes research evidence for review. It does not diagnose confirmed faults.

2. What is the minimum architecture required to solve it?

Canonical telemetry, window features, Model Harness, Isolation Forest, and the public analysis response. Additive diagnostic evidence is useful for downstream compatibility but not required for the original anomaly decision.

3. Why are there currently two active detectors rather than three?

Isolation Forest is the production baseline. Contextual Battery survived H3/H3.1 as stable and distinct. Temporal Battery and Temporal RPM were rejected in H4.1 because neither justified Core 3 promotion on current evidence.

4. What does each active detector contribute?

Isolation Forest contributes broad multivariate outlier evidence. Contextual Battery contributes context-conditioned voltage deviation evidence based only on verified RPM/TPS/battery features.

5. What experiments were rejected and why?

Temporal Battery, Temporal RPM, voting, score fusion, universal scores, arbitrary confidence, RCA v1 causal semantics, automatic baseline learning, vector memory, LLM/agent diagnosis and deep temporal networks were rejected or deferred because current evidence does not justify them.

6. Why is score fusion currently unjustified?

The active detectors use different score semantics and scales. H5 explicitly preserved detector-local scores and set `score_fusion_used=false`.

7. What does Evidence Aggregation add?

It adds coverage, availability, disagreement and evidence-state semantics. H5 recorded {h5['aggregation'].get('window_count')} windows with states {counts(h5['aggregation'].get('evidence_state_counts', {}))}; it does not vote or diagnose.

8. Is RCA v2 actually RCA?

Only in a compatibility sense. Architecturally it is more accurately a Diagnostic Evidence Interpretation Engine. It produced {h7_3['v2'].get('observation_count')} observations, {h7_3['v2'].get('symptom_count')} symptoms, {h7_3['v2'].get('possible_cause_count')} possible causes and {h7_3['v2'].get('recommended_check_count')} checks.

9. What does Historical Evidence add?

It adds recurrence, persistence, trend and priority context. H8 changed check priority in {h8['counterfactual_summary'].get('cases_with_check_priority_change')} cases, added no value in {h8['offline_summary'].get('cases_where_history_added_no_value')} cases, and changed causal hypotheses in {h8['counterfactual_summary'].get('cases_with_causal_hypothesis_change')} cases.

10. What claims are physically/empirically validated?

The strongest empirical claims are deterministic pipeline behavior, H2 parity against the handoff snapshot, verified v2 signal availability for RPM/TPS/battery, and sparse controlled-condition detector response. Physical root causes are not validated.

11. What claims remain provisional?

Contextual Battery usefulness, recurrence-based check prioritization, and detector disagreement interpretability remain provisional.

12. What is still unsupported?

Confirmed root cause, component failure, vehicle health, failure probability, universal anomaly score, and future failure prediction.

13. Which components are production-ready?

Canonical telemetry, feature engineering, Model Harness, Isolation Forest production inference, additive diagnostic evidence packaging, and the DriveSafe consumer that persists/displays research evidence without changing production behavior. Research evidence sections remain non-gating.

14. Which remain research-only?

Contextual Battery, Evidence Aggregation, RCA v2 interpretation, Historical Evidence, H9 retrospective research payloads and the research content shown in DriveSafe's collapsed diagnostic evidence UI.

15. Is any current component unnecessary?

Temporal detector candidates are unnecessary in the active architecture. They should remain archived only for reproducibility.

16. What should not be developed further without new evidence?

Core 3, score fusion, majority voting, causal RCA output, automatic baselines, LLM/agent diagnosis and deep temporal networks.

17. What new evidence would justify reopening rejected directions?

Validated, independent phenomena; calibrated score comparability; completed expert/physical root-cause evidence; governed baseline approval; or a demonstrated need for unstructured reasoning that deterministic logic cannot meet.

18. What are the strongest genuine contributions?

A minimal harness that preserved production behavior, explicit heterogeneous evidence aggregation without fusion, a feasible contextual voltage detector, negative temporal detector results, evidence-grounded RCA v2 semantics, structured historical evidence with cutoff governance, and H9/H9B backward-compatible operational integration.

## Parity and API Compatibility

H2 parity passed: `{h2['parity_passed']}`. The historical source was `{h2['historical_source'].get('source')}`, with limitation: {semicolon(h2['parity_limitations'])}. Public API compatibility passed: `{h2['api_compatibility'].get('passed')}`.

## Terminology and Reference Audit

The H3.1 terminology audit does not treat `normal_train` as verified healthy ground truth. It is nominal/reference baseline data unless independent evidence says otherwise. The contextual reference used {ctx['h3']['reference_session_count']} reference sessions, and overlap policy excluded reference session IDs and duplicate sample hashes from shadow evaluation. Provenance is preserved through session IDs, artifact hashes, decoder versions, telemetry schema and the reference-window trace.

The verified-signal set for contextual detection is {comma(ctx['h3']['verified_signals'])}. IAT and ECT remain excluded from the contextual detector because H3 did not treat them as strictly verified for that purpose. RPM and TPS are considered time-aligned because canonical rows contain them in the same decoded sample and window features share row boundaries.

## Coverage and Disagreement Audit

H5 active detector coverage was not complete over all windows. Aggregation counted applicable detector coverage as {counts(h5['aggregation'].get('applicable_detector_count_distribution', {}))}. It recorded IF-only finding windows={h5['aggregation'].get('if_only_finding_windows')}, contextual-only finding windows={h5['aggregation'].get('contextual_only_finding_windows')}, overlapping finding windows={h5['aggregation'].get('overlapping_finding_windows')} and disagreement windows={h5['aggregation'].get('disagreement_windows')}.

Those numbers are evidence-routing facts, not accuracy metrics. `insufficient_coverage` and unavailable detector states remain distinct from negative findings.

## Detector Evidence Maturity

Isolation Forest controlled-condition recall was 3/3 in H6, but precision and false-alert rates were not computed. Contextual Battery controlled-condition recall was 1/1 for its relevant controlled case, but that does not imply universal 100% recall. H6 explicitly lists ground-truth limitations: {semicolon(h6['ground_truth_limitations'])}.

## Evidence Aggregation Boundary

`no_evidence` means no applicable active detector produced anomaly evidence; it is not verified healthy. `skipped`, `failed`, `not_applicable` and `unavailable` remain distinct from normal predictions.

## What The System Can Say Today

Supported statements include: an unusual multivariate pattern was observed; a context-conditioned battery-voltage deviation was observed; active detectors agreed or disagreed; detector evidence was unavailable or insufficient; a similar structured observation appeared previously; a recommended check should be prioritized for review.

Unsupported statements include: a component has failed; the vehicle is healthy; root cause is confirmed; failure probability is a specific percentage; a component will fail soon; one detector's score is directly comparable to another detector's score.

## RCA and Diagnostic Checks

H7 v1 generated {ctx['h7']['answers'].get('events_with_hypotheses')} hypothesis-bearing events and needed expert validation. H7.1 had {ctx['h7_1']['answers'].get('completed_hypothesis_judgment_count')} completed judgments, so `insufficient_expert_evidence` was the only defensible outcome. H7.2 surrogate validation let research continue by downgrading claim levels; H7.3 removed {h7_3['comparison'].get('unsupported_claims_removed')} unsupported v1 causal claims.

Recommended checks differ from causal claims: they are follow-up actions tied to observed evidence. H7.3 generated {h7_3['v2'].get('recommended_check_count')} checks over {h7_3['v2'].get('result_count')} cases, which is useful for review but potentially noisy because generic checks can repeat across similar evidence families.

Concepts such as multivariate outlier, constant signal and unusual RPM/TPS pattern cannot automatically become root causes. They are observations or evidence relationships. A root-cause claim needs a higher evidence level: physical measurement, qualified expert judgment, OEM/normative constraints or a validated evidence-to-cause mapping.

## Expert Validation and Evidence Hierarchy

H7.1 prepared expert review infrastructure and a {ctx['h7_1']['case_count']}-case dataset, but completed expert judgments are unavailable. H7.2 surrogate validation is therefore useful only as literature-grounded and structural review; it cannot substitute for expert or physical validation.

The effective evidence hierarchy is: independent physical ground truth; qualified expert judgment; OEM or normative documentation; peer-reviewed engineering literature; internal controlled empirical evidence; internal observational evidence; engineering assumption. Lower levels can support research direction, but repetition of an assumption cannot promote it to verified truth.

## Historical Evidence Value

H8 indexed {h8['index_summary'].get('record_count')} historical records across {h8['index_summary'].get('session_count')} sessions. The current value is useful but narrow: it changes check priority and recurrence context, not root-cause conclusions.

Historical Evidence is not the same thing as a nominal/reference baseline. The nominal baseline is data deliberately eligible to represent reference behavior. The historical corpus contains prior observations, including anomalies, and must not silently redefine normal behavior. H8 baseline governance recorded {h8['baseline_governance'].get('nominal_reference_baseline', {}).get('reference_candidate_count')} nominal/reference candidates, {h8['baseline_governance'].get('nominal_reference_baseline', {}).get('excluded_candidate_count')} excluded candidates and {h8['baseline_governance'].get('nominal_reference_baseline', {}).get('trusted_normal_ground_truth_count')} trusted-normal ground-truth sessions.

## Why No LLMs, Agents or Deep Networks Yet

The project excludes LLM diagnosis, autonomous agents, vector-search reasoning and agent memory because the current requirement is deterministic, traceable, testable evidence handling over a small dataset. This does not mean LLMs or agents are inherently unsuitable; it means no current requirement needs unstructured reasoning or adaptive tool orchestration.

Deep temporal networks are technically possible, but not justified by current evidence. There is not enough verified labeled temporal data, the simpler detectors already expose the main observable phenomena, and interpretability/reproducibility matter more than model capacity at this stage.

## Most Valuable Future Physical Measurements

The highest-value validation measurements are synchronized external battery/charging voltage, starter/engine-running or alternator charging state, known accessory electrical load state, repeat captures for low-voltage candidate sessions, and bench spot checks for TPS/RPM/battery calibration during context transitions.

## Integration Status

H9 analyzer side ready: `{h9['analyzer_side_ready']}`. Analyzer emits additive diagnostic evidence: `{h9['analyzer_diagnostic_evidence']['emits_additive_diagnostic_evidence']}`. DriveSafe Web App repo available: `{h9['drivesafe_repo_available']}`. H9B DriveSafe integration completed: `{h9['drivesafe_integration_completed']}`.

DriveSafe tolerantly ingests optional evidence, persists valid evidence, isolates malformed optional evidence from production analysis, exposes raw evidence, builds display-only `diagnostic_evidence_view`, and renders the collapsed research-evidence section. Existing anomaly/status/health fields remain authoritative.

## Final Freeze Decision

Decision after RA1.1 reconciliation: `{ctx['final_decision']}`.

This freeze does **not** mean every component is scientifically validated, research evidence is production-certified, RCA root causes are established, or no future work is possible. It means no additional architectural capability is currently justified without new evidence.

Final question: Is the implemented H1-H9B system internally consistent with the research claims and production boundaries documented by RA1?

Answer: yes. Finalize the architecture freeze.

## Verification To Record

Run the analyzer test suite and Pi handoff verifier after generating this RA1 bundle. Record results in the final user-facing response; do not regenerate H1-H9 historical outputs unless provenance requires it.
"""


def final_integration_reconciliation(ctx: dict[str, Any]) -> str:
    h9 = ctx["h9"]
    h9b = ctx["h9b"]
    checks = h9b.get("checks", {})
    return f"""# RA1.1 Final Integration Reconciliation

## Scope

RA1.1 reconciles the RA1 research architecture audit with completed H9B DriveSafe integration. This is documentation and evidence synchronization only. No detector logic, RCA rules, thresholds, APIs, production semantics or UI behavior are promoted by this reconciliation.

## H9/H9B Status

- Analyzer emits additive `diagnostic_evidence`: `{h9['analyzer_diagnostic_evidence']['emits_additive_diagnostic_evidence']}`.
- DriveSafe repository available: `{h9['drivesafe_repo_available']}`.
- DriveSafe diagnostic evidence consumer completed: `{h9['drivesafe_integration_completed']}`.
- DriveSafe checks: {counts(checks)}.

H9 analyzer to DriveSafe integration is therefore marked complete.

## Research / Production Boundary

Production remains the Isolation Forest production-compatible result and existing DriveSafe status/health behavior. Research evidence remains Contextual Battery, evidence aggregation, RCA v2, historical evidence and the collapsed DriveSafe diagnostic evidence UI.

H9B adds integration capability only. It does not improve detector accuracy, establish causal RCA, mature historical evidence, or certify research evidence as production.

## Failure Isolation

Diagnostic evidence may be absent for legacy sessions. Malformed optional evidence is isolated from production analysis where safely possible. Empty `possible_causes` is normal. `no_evidence`, `insufficient_coverage`, skipped, failed and not-applicable detector states remain distinguishable from healthy/normal.

DriveSafe does not recompute detector inference, aggregation semantics, RCA rules or historical recurrence/trend evidence. Analyzer owns analysis logic. DriveSafe owns ingestion, persistence, API presentation and UI.

## Verification Record

- Analyzer full tests: `173 passed`, `4528 warnings`.
- Analyzer Pi handoff verifier: `OK`, model `iforest-baseline-20260920T091709Z`, feature count `81`.
- DriveSafe full tests: `64 tests OK`, with existing SQLite `ResourceWarning` messages.

## Final Freeze Decision

Decision: `{ctx['final_decision']}`.

This freeze does not mean every component is scientifically validated, research evidence is production-certified, RCA root causes are established, or no future work is possible.

It means: no additional architectural capability is currently justified without new evidence.

Final question: Is the implemented H1-H9B system internally consistent with the research claims and production boundaries documented by RA1?

Answer: yes. Finalize the architecture freeze.
"""


def warning_audit(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": RA1_SCHEMA_VERSION,
        "scope": "RA1.1 warning classification only; no dependency upgrades or cleanup performed.",
        "analyzer": {
            "command": ".venv/bin/python -m pytest -q",
            "result": {
                "status": "passed",
                "passed": 173,
                "warnings": 4528,
            },
            "categories": [
                {
                    "category": "dependency deprecation",
                    "count": 1,
                    "source": "fastapi/starlette TestClient import",
                    "example": "StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.",
                    "project_code": False,
                    "behavior_affecting": False,
                    "action_in_ra1_1": "record_only",
                },
                {
                    "category": "dependency deprecation",
                    "count": 4527,
                    "source": "joblib loading NumPy arrays under NumPy 2.5",
                    "example": "DeprecationWarning: Setting the shape on a NumPy array has been deprecated in NumPy 2.5.",
                    "project_code": False,
                    "behavior_affecting": False,
                    "action_in_ra1_1": "record_only",
                },
            ],
            "summary": {
                "dependency_deprecations": True,
                "project_code_deprecations": False,
                "resource_leaks": False,
                "behavior_affecting_warnings_observed": False,
            },
        },
        "drivesafe": {
            "command": ".venv/bin/python -m unittest tests.test_app",
            "result": {
                "status": "passed",
                "tests": 64,
                "warnings": "existing SQLite ResourceWarning messages",
            },
            "categories": [
                {
                    "category": "resource warning",
                    "count": "multiple",
                    "source": "test-process SQLite connections surfaced through Werkzeug/SQLAlchemy/Jinja stack frames",
                    "example": "ResourceWarning: unclosed database in <sqlite3.Connection object ...>",
                    "project_code": "undetermined in RA1.1",
                    "behavior_affecting": False,
                    "action_in_ra1_1": "record_only",
                }
            ],
            "summary": {
                "dependency_deprecations": False,
                "project_code_deprecations": False,
                "resource_leaks": True,
                "behavior_affecting_warnings_observed": False,
            },
        },
    }


def counts(data: dict[str, Any]) -> str:
    if not data:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(data.items()))


def comma(items: list[Any]) -> str:
    return ", ".join(str(item) for item in items) if items else "none"


def semicolon(items: list[Any]) -> str:
    return "; ".join(str(item) for item in items) if items else "none"
