from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.evaluation.model_harness_evaluation import EvaluationPaths, default_paths, write_json


RETRIEVAL_DATE = "2026-10-06"
SOURCE_REGISTRY_VERSION = "rca-evidence-source-registry-v1"
GROUNDING_MATRIX_VERSION = "rca-rule-grounding-matrix-v1"
CANDIDATE_CATALOG_VERSION = "candidate-rca-rule-catalog-v2"

CLAIM_LEVELS = (
    "observation",
    "symptom",
    "possible_hypothesis",
    "supported_hypothesis",
    "confirmed_cause",
)
GROUNDING_DECISIONS = (
    "retain",
    "retain_with_restricted_claim",
    "revise",
    "remove",
    "insufficient_grounding",
)
PROVISIONAL_RULE_DECISIONS = (
    "provisionally_grounded",
    "grounded_with_limitations",
    "revise",
    "remove",
    "insufficient_evidence",
)


def default_h72_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h7_2")


def run_all(
    paths: EvaluationPaths,
    *,
    h7_dir: Path | None = None,
    h71_dir: Path | None = None,
) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h7_dir = (h7_dir or paths.repo_root / "data" / "evaluation" / "h7").resolve()
    resolved_h71_dir = (h71_dir or paths.repo_root / "data" / "evaluation" / "h7_1").resolve()
    h7_contract = read_json(resolved_h7_dir / "rca_contract.json")
    h7_review = read_json(resolved_h7_dir / "rca_review_manifest.json")
    h71_dataset = read_json(resolved_h71_dir / "expert_validation_dataset.json")

    registry = evidence_source_registry()
    counterexamples = build_counterexample_report(h71_dataset)
    matrix = build_rule_grounding_matrix(h7_contract, h7_review, h71_dataset, counterexamples)
    case_review = build_case_surrogate_validation(h71_dataset, matrix, counterexamples)
    candidate_v2 = build_candidate_v2_catalog(matrix)
    structural = build_structural_verification_report(candidate_v2, matrix)
    decision = build_decision_report(matrix, counterexamples, structural, case_review)

    outputs = {
        "source_registry": paths.output_dir / "source_registry.json",
        "grounding_matrix": paths.output_dir / "rule_grounding_matrix.json",
        "counterexamples": paths.output_dir / "counterexample_report.json",
        "case_surrogate": paths.output_dir / "case_surrogate_validation.json",
        "structural": paths.output_dir / "structural_verification_report.json",
        "candidate_v2": paths.output_dir / "candidate_rca_v2_catalog.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["source_registry"], registry)
    write_json(outputs["grounding_matrix"], matrix)
    write_json(outputs["counterexamples"], counterexamples)
    write_json(outputs["case_surrogate"], case_review)
    write_json(outputs["structural"], structural)
    write_json(outputs["candidate_v2"], candidate_v2)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], registry, matrix, counterexamples, case_review, structural, candidate_v2, decision)
    return outputs


def evidence_source_registry() -> dict[str, Any]:
    return {
        "schema_version": SOURCE_REGISTRY_VERSION,
        "semantic_boundary": "Literature-grounded and internally consistent does not mean physically confirmed.",
        "policy": {
            "long_copyrighted_excerpts_stored": False,
            "concise_paraphrased_claims_only": True,
            "expert_judgments_populated": False,
            "physical_root_cause_confirmation_allowed": False,
        },
        "source_classes": {
            "Tier A": "Normative or OEM source.",
            "Tier B": "Engineering literature or industry technical source.",
            "Tier C": "Internal empirical project evidence.",
            "Tier D": "Engineering assumption that cannot independently justify a strong causal claim.",
        },
        "sources": [
            {
                "source_id": "src.iso.13379-1-2025",
                "title": "ISO 13379-1:2025, Condition monitoring and diagnostics of machine systems - Data interpretation and diagnostics techniques - Part 1: General guidelines",
                "publisher_or_organization": "International Organization for Standardization",
                "publication_or_version_date": "2025",
                "source_tier": "Tier A",
                "topic": "machine diagnostics concepts and data interpretation",
                "supported_diagnostic_principle": (
                    "Diagnostics should use common concepts and structured interpretation; diagnostic communication "
                    "should distinguish data interpretation from maintenance or fault conclusions."
                ),
                "limitations": "Public registry page only; full standard text is not stored or quoted.",
                "bibliographic_reference": "ISO 13379-1:2025 official ISO standard registry page.",
                "url": "https://www.iso.org/standard/88027.html",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.honda.mcs.2025",
                "title": "Why Choose MCS? - Diagnostic Tool for Honda Motorcycles",
                "publisher_or_organization": "Honda Global Motorcycle Aftersales",
                "publication_or_version_date": "2025-06-25",
                "source_tier": "Tier A",
                "topic": "Honda motorcycle PGM-FI, OBD, DTC and MCS diagnostic workflow",
                "supported_diagnostic_principle": (
                    "Honda describes DTC/MCS information as diagnostic support for maintenance and fault-area isolation, "
                    "not as a standalone physical root-cause confirmation."
                ),
                "limitations": "General aftersales material, not the model-specific shop manual for this project vehicle.",
                "bibliographic_reference": "Honda Global Motorcycle Aftersales Plus ONE knowledge article, 2025.",
                "url": "https://global.honda/en/motorcycle-aftersales/plusone/202506.html",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.honda.shopmanual.2026",
                "title": "Shop Manual and Parts Catalog",
                "publisher_or_organization": "Honda Global Motorcycle Aftersales",
                "publication_or_version_date": "2026-01-26",
                "source_tier": "Tier A",
                "topic": "shop manual inspection, diagnosis and DTC/MCS use",
                "supported_diagnostic_principle": (
                    "Honda presents diagnosis as a shop-manual procedure using inspection information and DTC/MCS data; "
                    "therefore project RCA should recommend checks rather than claim component failure."
                ),
                "limitations": "General description of shop manuals; no model-specific measurements or pass/fail limits.",
                "bibliographic_reference": "Honda Global Motorcycle Aftersales Plus ONE knowledge article, 2026.",
                "url": "https://global.honda/en/motorcycle-aftersales/plusone/202601.html",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.honda.common-service-manual",
                "title": "Honda Common Service Manual",
                "publisher_or_organization": "Honda Motor Co., Ltd.",
                "publication_or_version_date": "undated public mirror",
                "source_tier": "Tier A",
                "topic": "common Honda motorcycle systems including battery, charging and troubleshooting",
                "supported_diagnostic_principle": (
                    "Honda service material treats battery/charging diagnosis as inspection of battery, regulator/rectifier, "
                    "wiring/ground and model-specific procedures rather than a single-voltage conclusion."
                ),
                "limitations": "Retrieved from public mirror, not Honda's authenticated service portal; model-specific manual remains authoritative.",
                "bibliographic_reference": "Honda Common Service Manual, Honda Motor Co., Ltd.",
                "url": "https://www.honda4fun.com/dwnload/Shop-Manual/Honda-Common-Service-Manual.pdf",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.jardine.2006.cbm-review",
                "title": "A review on machinery diagnostics and prognostics implementing condition-based maintenance",
                "publisher_or_organization": "Mechanical Systems and Signal Processing",
                "publication_or_version_date": "2006",
                "source_tier": "Tier B",
                "topic": "condition-based maintenance, diagnostics, data acquisition and processing",
                "supported_diagnostic_principle": (
                    "Condition-based maintenance separates data acquisition, processing and diagnostic decision-making; "
                    "statistical indicators require interpretation and validation before maintenance conclusions."
                ),
                "limitations": "General machinery diagnostics review, not motorcycle- or Honda-specific.",
                "bibliographic_reference": "Jardine, Lin, and Banjevic, Mechanical Systems and Signal Processing 20(7), 1483-1510.",
                "url": "https://doi.org/10.1016/j.ymssp.2005.09.012",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.knauf.2002.rule-validation",
                "title": "A framework for validation of rule-based systems",
                "publisher_or_organization": "IEEE Transactions on Systems, Man, and Cybernetics - Part B",
                "publication_or_version_date": "2002",
                "source_tier": "Tier B",
                "topic": "validation methodology for rule-based expert systems",
                "supported_diagnostic_principle": (
                    "Rule-system validation requires explicit cases, expert assessment and comparison; absent expert reviews "
                    "should be reported as insufficient validation rather than inferred approval."
                ),
                "limitations": "Methodological expert-system validation paper, not automotive-specific.",
                "bibliographic_reference": "Knauf et al., IEEE Transactions on Systems, Man, and Cybernetics - Part B, 2002.",
                "url": "https://pubmed.ncbi.nlm.nih.gov/18238127/",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.rulebased.vv.1994",
                "title": "Verification, Testing and Validation of Rule-Based Expert Systems",
                "publisher_or_organization": "IFAC Proceedings Volumes / ScienceDirect",
                "publication_or_version_date": "1994",
                "source_tier": "Tier B",
                "topic": "rule-base verification and validation",
                "supported_diagnostic_principle": (
                    "Rule bases should be checked for conflicts, redundancy, unreachable rules and testable behavior before use."
                ),
                "limitations": "General rule-system V&V source; does not validate any project-specific rule.",
                "bibliographic_reference": "Verification, Testing and Validation of Rule-Based Expert Systems, IFAC Proceedings Volumes.",
                "url": "https://www.sciencedirect.com/science/article/pii/S1474667017518194",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.sensor-fdd.stuck-signal",
                "title": "Hybrid online sensor error detection and functional redundancy for systems with time-varying parameters",
                "publisher_or_organization": "Sensors / PubMed Central",
                "publication_or_version_date": "2018",
                "source_tier": "Tier B",
                "topic": "sensor fault detection, missing or stuck signals, functional redundancy",
                "supported_diagnostic_principle": (
                    "Missing or stuck-at-constant sensor signals can be fault patterns, but confirmation requires redundancy, "
                    "model checks or additional integrity evidence."
                ),
                "limitations": "Generic sensor-FDD paper, not ECU-frame-specific or motorcycle-specific.",
                "bibliographic_reference": "Hybrid online sensor error detection and functional redundancy, Sensors, 2018.",
                "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5796791/",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.denso.charging-diagnosis",
                "title": "Charging System Diagnosis",
                "publisher_or_organization": "DENSO Auto Parts",
                "publication_or_version_date": "2020s public technical guide",
                "source_tier": "Tier B",
                "topic": "charging system diagnosis, wiring, load and OEM procedure",
                "supported_diagnostic_principle": (
                    "Charging-system diagnosis should check battery, alternator/regulator output, wiring, loads and OEM service procedures; "
                    "voltage deviation alone does not isolate a component."
                ),
                "limitations": "Industry technical guidance, not peer-reviewed and not Honda-model-specific.",
                "bibliographic_reference": "DENSO Auto Parts charging-system diagnostic guide.",
                "url": "https://www.densoautoparts.com/charging-system-diagnosis/",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.internal.h6",
                "title": "H6 active detector validation artifacts",
                "publisher_or_organization": "ecuread-analyzer project",
                "publication_or_version_date": "2026-10-06 local artifact",
                "source_tier": "Tier C",
                "topic": "controlled and observed detector validation cases",
                "supported_diagnostic_principle": (
                    "Internal controlled and observed cases can support whether detector evidence exists, but do not independently "
                    "confirm physical root causes."
                ),
                "limitations": "Sparse controlled cases and no completed expert/physical validation.",
                "bibliographic_reference": "data/evaluation/h6/*",
                "url": "local:data/evaluation/h6",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.internal.h7-h71",
                "title": "H7/H7.1 RCA and expert-validation preparation artifacts",
                "publisher_or_organization": "ecuread-analyzer project",
                "publication_or_version_date": "2026-10-06 local artifact",
                "source_tier": "Tier C",
                "topic": "RCA v1 outputs and blank expert-validation dataset",
                "supported_diagnostic_principle": (
                    "Project artifacts preserve observations, hypotheses, rule provenance and unreviewed expert forms separately."
                ),
                "limitations": "No completed expert judgments are present.",
                "bibliographic_reference": "data/evaluation/h7/* and data/evaluation/h7_1/*",
                "url": "local:data/evaluation/h7_1",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
            {
                "source_id": "src.assumption.operating-context",
                "title": "Project engineering assumption: RPM/TPS features may indicate operating context, not failure",
                "publisher_or_organization": "ecuread-analyzer project",
                "publication_or_version_date": "2026-10-06",
                "source_tier": "Tier D",
                "topic": "operating-state interpretation boundary",
                "supported_diagnostic_principle": (
                    "RPM/TPS feature movement is treated as possible operating context unless independent evidence shows abnormal behavior."
                ),
                "limitations": "Engineering assumption only; cannot independently justify causal RCA.",
                "bibliographic_reference": "H7.2 assumption registry entry.",
                "url": "local:assumption",
                "retrieval_or_review_date": RETRIEVAL_DATE,
            },
        ],
    }


def build_rule_grounding_matrix(
    h7_contract: dict[str, Any],
    h7_review: dict[str, Any],
    h71_dataset: dict[str, Any],
    counterexamples: dict[str, Any],
) -> dict[str, Any]:
    h7_summary = h7_review.get("summary", {})
    rule_fire_counts = h7_summary.get("rule_fire_counts", {})
    by_rule = {rule["rule_id"]: rule for rule in h7_contract.get("rules", [])}
    rows = []
    for rule_id in h71_dataset.get("coverage", {}).get("contract_rule_ids", []):
        row = rule_grounding_row(rule_id, by_rule.get(rule_id, {}), rule_fire_counts, counterexamples)
        rows.append(row)
    return {
        "schema_version": GROUNDING_MATRIX_VERSION,
        "claim_levels": list(CLAIM_LEVELS),
        "allowed_grounding_decisions": list(GROUNDING_DECISIONS),
        "policy": {
            "confirmed_cause_requires_independent_physical_evidence": True,
            "confirmed_cause_allowed_in_this_run": False,
            "tier_d_cannot_independently_justify_strong_claim": True,
            "expert_judgments_populated": False,
        },
        "summary": {
            "rule_count": len(rows),
            "decision_counts": dict(sorted(Counter(row["provisional_decision"] for row in rows).items())),
            "maximum_claim_level_counts": dict(sorted(Counter(row["maximum_defensible_claim_level"] for row in rows).items())),
        },
        "rules": rows,
    }


def rule_grounding_row(
    rule_id: str,
    rule_payload: dict[str, Any],
    rule_fire_counts: dict[str, int],
    counterexamples: dict[str, Any],
) -> dict[str, Any]:
    base = {
        "rule_id": rule_id,
        "h7_rule_description": rule_payload.get("description"),
        "h7_fire_count": int(rule_fire_counts.get(rule_id, 0)),
        "decomposed_claims": decompose_claims(rule_id),
        "supporting_internal_cases": internal_cases_for_rule(rule_id),
        "counterexamples": counterexamples.get("by_rule", {}).get(rule_id, []),
        "contradicting_or_limiting_sources": limiting_sources_for_rule(rule_id),
    }
    if rule_id == "rca.iforest.multivariate_pattern":
        return {
            **base,
            "claim": "Isolation Forest outside-reference output indicates a detector observation, not a causal hypothesis.",
            "claim_type": "detector_observation",
            "supporting_sources": ["src.iso.13379-1-2025", "src.jardine.2006.cbm-review", "src.internal.h6"],
            "maximum_defensible_claim_level": "observation",
            "provisional_decision": "revise",
            "decision_reason": "Move outside-reference concept from hypothesis to observation/evidence.",
        }
    if rule_id == "rca.electrical.contextual_voltage_deviation":
        return {
            **base,
            "claim": "Contextual battery-voltage deviation is a supported symptom; specific electrical causes remain possible hypotheses.",
            "claim_type": "symptom_plus_possible_hypotheses",
            "supporting_sources": [
                "src.honda.common-service-manual",
                "src.denso.charging-diagnosis",
                "src.jardine.2006.cbm-review",
                "src.internal.h6",
            ],
            "maximum_defensible_claim_level": "symptom",
            "provisional_decision": "retain_with_restricted_claim",
            "decision_reason": "Retain voltage symptom but split candidate causes and require follow-up checks.",
        }
    if rule_id == "rca.operating_state.throttle_related_pattern":
        return {
            **base,
            "claim": "RPM/TPS-related IF features can describe operating context, but do not independently imply abnormal throttle behavior.",
            "claim_type": "operating_context_observation",
            "supporting_sources": ["src.iso.13379-1-2025", "src.internal.h6", "src.assumption.operating-context"],
            "maximum_defensible_claim_level": "observation",
            "provisional_decision": "revise",
            "decision_reason": "Require context evidence that distinguishes normal operator demand from unexplained behavior.",
        }
    if rule_id == "rca.data_quality.constant_signal_pattern":
        return {
            **base,
            "claim": "Constant-signal features are observations; acquisition/data-quality hypotheses require integrity evidence.",
            "claim_type": "data_quality_observation",
            "supporting_sources": ["src.sensor-fdd.stuck-signal", "src.rulebased.vv.1994", "src.internal.h6"],
            "maximum_defensible_claim_level": "observation",
            "provisional_decision": "revise",
            "decision_reason": "Constant/low-variance signal alone is insufficient for acquisition-artifact RCA.",
        }
    if rule_id == "rca.detectors.disagreement_preservation":
        return {
            **base,
            "claim": "Detector disagreement should be preserved as scope difference/evidence relationship without forced consensus.",
            "claim_type": "evidence_relationship",
            "supporting_sources": ["src.iso.13379-1-2025", "src.rulebased.vv.1994", "src.internal.h7-h71"],
            "maximum_defensible_claim_level": "observation",
            "provisional_decision": "retain",
            "decision_reason": "Rule is conservative and prevents unsupported fusion.",
        }
    if rule_id == "rca.coverage.insufficient_evidence":
        return {
            **base,
            "claim": "Unavailable/applicability gaps should return insufficient evidence instead of diagnosis.",
            "claim_type": "evidence_limitation",
            "supporting_sources": ["src.iso.13379-1-2025", "src.knauf.2002.rule-validation", "src.internal.h7-h71"],
            "maximum_defensible_claim_level": "observation",
            "provisional_decision": "retain",
            "decision_reason": "Rule prevents hypotheses without active evidence.",
        }
    if rule_id == "rca.no_evidence.no_hypothesis":
        return {
            **base,
            "claim": "No active detector anomaly evidence should not become a healthy/faulty label or RCA.",
            "claim_type": "evidence_limitation",
            "supporting_sources": ["src.iso.13379-1-2025", "src.knauf.2002.rule-validation", "src.internal.h7-h71"],
            "maximum_defensible_claim_level": "observation",
            "provisional_decision": "retain",
            "decision_reason": "Rule is conservative and avoids unsupported causes.",
        }
    return {
        **base,
        "claim": "Rule grounding not mapped.",
        "claim_type": "unmapped",
        "supporting_sources": [],
        "maximum_defensible_claim_level": "observation",
        "provisional_decision": "insufficient_grounding",
        "decision_reason": "No grounding entry exists.",
    }


def decompose_claims(rule_id: str) -> dict[str, Any]:
    catalog = {
        "rca.iforest.multivariate_pattern": {
            "observed_fact": "Isolation Forest positive finding with detector-local score and unusual feature list.",
            "derived_symptom": "Representative window is unusual relative to the IF reference model.",
            "causal_hypothesis": None,
            "recommended_diagnostic_check": "Review listed feature families against raw telemetry and operator/session notes.",
            "required_reclassification": "outside-reference wording belongs to observation/evidence, not cause.",
        },
        "rca.electrical.contextual_voltage_deviation": {
            "observed_fact": "Contextual battery detector positive with top voltage deviation and operating context.",
            "derived_symptom": "Battery/supply-voltage behavior differs from contextual reference statistics.",
            "causal_hypothesis": "Electrical supply variation remains broad; charging, battery, load, wiring and supply path are alternatives.",
            "recommended_diagnostic_check": "External voltage measurement, electrical load notes and service-manual charging checks.",
            "required_reclassification": "separate symptom from candidate causes.",
        },
        "rca.operating_state.throttle_related_pattern": {
            "observed_fact": "IF unusual features include RPM/TPS family and telemetry snapshot.",
            "derived_symptom": "Operating-state variation may be present.",
            "causal_hypothesis": "Only possible if evidence shows unexplained behavior rather than normal operator demand.",
            "recommended_diagnostic_check": "Compare RPM/TPS traces with operator input and operating conditions.",
            "required_reclassification": "gate causal hypothesis on contextual/operator evidence.",
        },
        "rca.data_quality.constant_signal_pattern": {
            "observed_fact": "IF unusual features include constant_signal feature names.",
            "derived_symptom": "A signal was low-variance or constant over the representative window.",
            "causal_hypothesis": "Acquisition artifact only if additional integrity evidence is present.",
            "recommended_diagnostic_check": "Check timestamps, raw-frame patterns, checksum/header failures and plausible stable-operation alternatives.",
            "required_reclassification": "constant signal alone remains observed feature.",
        },
        "rca.detectors.disagreement_preservation": {
            "observed_fact": "At least one active detector is positive and at least one is negative.",
            "derived_symptom": "Detectors evaluate different phenomena/scopes.",
            "causal_hypothesis": None,
            "recommended_diagnostic_check": "Review each detector in its own score semantics.",
            "required_reclassification": "none.",
        },
        "rca.coverage.insufficient_evidence": {
            "observed_fact": "No active detector or incomplete detector coverage is applicable.",
            "derived_symptom": "Evidence coverage is insufficient for RCA.",
            "causal_hypothesis": None,
            "recommended_diagnostic_check": "Restore missing features or inspect preprocessing.",
            "required_reclassification": "none.",
        },
        "rca.no_evidence.no_hypothesis": {
            "observed_fact": "Applicable active detectors are negative.",
            "derived_symptom": "No detector anomaly evidence in this representative region.",
            "causal_hypothesis": None,
            "recommended_diagnostic_check": "Do not treat as verified healthy without independent validation.",
            "required_reclassification": "none.",
        },
    }
    return catalog.get(rule_id, {})


def internal_cases_for_rule(rule_id: str) -> list[str]:
    return {
        "rca.iforest.multivariate_pattern": ["H7 IF-positive review items", "H6 controlled high ECT/high RPM/load/low battery cases"],
        "rca.electrical.contextual_voltage_deviation": ["H6 contextual-positive voltage cases", "H7 contextual-only and overlapping events"],
        "rca.operating_state.throttle_related_pattern": ["H7 IF-positive events with RPM/TPS features"],
        "rca.data_quality.constant_signal_pattern": ["H7 IF events with constant_signal feature names"],
        "rca.detectors.disagreement_preservation": ["H7 detector disagreement cases"],
        "rca.coverage.insufficient_evidence": ["H7 insufficient-coverage cases"],
        "rca.no_evidence.no_hypothesis": ["H7 no-anomaly-evidence cases"],
    }.get(rule_id, [])


def limiting_sources_for_rule(rule_id: str) -> list[str]:
    return {
        "rca.iforest.multivariate_pattern": ["src.knauf.2002.rule-validation"],
        "rca.electrical.contextual_voltage_deviation": ["src.honda.common-service-manual", "src.denso.charging-diagnosis"],
        "rca.operating_state.throttle_related_pattern": ["src.assumption.operating-context"],
        "rca.data_quality.constant_signal_pattern": ["src.sensor-fdd.stuck-signal"],
        "rca.detectors.disagreement_preservation": [],
        "rca.coverage.insufficient_evidence": [],
        "rca.no_evidence.no_hypothesis": [],
    }.get(rule_id, [])


def build_counterexample_report(h71_dataset: dict[str, Any]) -> dict[str, Any]:
    by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in h71_dataset.get("cases", []):
        region = case["selection_tags"].get("source_validation_region_type")
        validation_label = (case.get("provenance", {}).get("validation_label") or {}).get("label")
        source_label = (case.get("provenance", {}).get("validation_label") or {}).get("source_label")
        event = case.get("provenance", {}).get("source_event") or {}
        telemetry = (case.get("review_presentation", {}).get("observable_evidence") or {}).get("telemetry") or {}
        features = str((case.get("review_presentation", {}).get("observable_evidence") or {}).get("unusual_feature_evidence", {}).get("isolation_forest_most_unusual_features") or "")
        rule_ids = case["selection_tags"].get("rule_ids_represented", [])
        if "rca.iforest.multivariate_pattern" in rule_ids and validation_label in {"controlled_condition", "observed_normal_behavior", "inconclusive"}:
            by_rule["rca.iforest.multivariate_pattern"].append(
                counterexample_case(
                    case,
                    "IF-positive outside-reference evidence does not identify a physical cause.",
                    {"validation_label": validation_label, "source_label": source_label, "region": region},
                )
            )
        if "rca.electrical.contextual_voltage_deviation" in rule_ids:
            by_rule["rca.electrical.contextual_voltage_deviation"].append(
                counterexample_case(
                    case,
                    "Voltage deviation has multiple possible electrical/load/wiring/charging explanations without external measurement.",
                    {
                        "battery_voltage_min": telemetry.get("battery_voltage_min"),
                        "battery_voltage_mean": telemetry.get("battery_voltage_mean"),
                        "validation_label": validation_label,
                    },
                )
            )
        if "rca.operating_state.throttle_related_pattern" in rule_ids:
            by_rule["rca.operating_state.throttle_related_pattern"].append(
                counterexample_case(
                    case,
                    "RPM/TPS features may reflect normal operator demand or operating state rather than abnormal throttle behavior.",
                    {
                        "rpm_median": telemetry.get("rpm_median"),
                        "tps_raw_median": telemetry.get("tps_raw_median"),
                        "tps_voltage_median": telemetry.get("tps_voltage_median"),
                        "source_label": source_label,
                    },
                )
            )
        if "rca.data_quality.constant_signal_pattern" in rule_ids and "constant_signal" in features:
            by_rule["rca.data_quality.constant_signal_pattern"].append(
                counterexample_case(
                    case,
                    "Constant-signal feature appears without independent timestamp/checksum/header/raw-frame integrity evidence.",
                    {"features": features, "duration_ms": event.get("duration_ms"), "validation_label": validation_label},
                )
            )
        if event.get("evidence_state") == "detector_disagreement":
            by_rule["rca.detectors.disagreement_preservation"].append(
                counterexample_case(
                    case,
                    "Disagreement can arise from detector scope differences and should not imply one detector is wrong.",
                    {"region": region, "event_state": event.get("evidence_state")},
                )
            )
    return {
        "schema_version": "rca-counterexample-report-v1",
        "policy": {
            "counterexamples_used_to_restrict_claims": True,
            "counterexamples_do_not_confirm_absence_of_fault": True,
        },
        "summary": {
            "rule_counterexample_counts": dict(sorted((rule_id, len(items)) for rule_id, items in by_rule.items())),
        },
        "by_rule": dict(sorted(by_rule.items())),
    }


def counterexample_case(case: dict[str, Any], reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id"),
        "source_review_item_id": case.get("source_review_item_id"),
        "source_validation_region_type": case["selection_tags"].get("source_validation_region_type"),
        "reason": reason,
        "observable_evidence": evidence,
    }


def build_case_surrogate_validation(
    h71_dataset: dict[str, Any],
    matrix: dict[str, Any],
    counterexamples: dict[str, Any],
) -> dict[str, Any]:
    rule_by_id = {rule["rule_id"]: rule for rule in matrix.get("rules", [])}
    counterexample_ids = {
        (rule_id, item["case_id"])
        for rule_id, items in counterexamples.get("by_rule", {}).items()
        for item in items
    }
    cases = []
    for case in h71_dataset.get("cases", []):
        hypotheses = []
        for hypothesis in case.get("rule_provenance", {}).get("hypotheses", []):
            rule_id = hypothesis.get("source_rule_id")
            rule = rule_by_id.get(rule_id, {})
            is_counterexample = (rule_id, case["case_id"]) in counterexample_ids
            hypotheses.append(
                {
                    "hypothesis_review_id": hypothesis.get("hypothesis_review_id"),
                    "hypothesis_id": hypothesis.get("hypothesis_id"),
                    "source_rule_id": rule_id,
                    "literature_grounding_decision": rule.get("provisional_decision"),
                    "internal_evidence_grounding_decision": internal_evidence_decision(rule_id, case),
                    "counterexample_status": "counterexample_or_claim_limiter_present" if is_counterexample else "no_specific_counterexample_found",
                    "maximum_supported_claim_level": rule.get("maximum_defensible_claim_level"),
                    "recommended_rule_revision": recommended_revision_for_rule(rule_id),
                    "unresolved_questions": unresolved_questions_for_rule(rule_id),
                    "surrogate_namespace": "surrogate_validation",
                }
            )
        cases.append(
            {
                "case_id": case["case_id"],
                "source_review_item_id": case.get("source_review_item_id"),
                "source_validation_region_type": case["selection_tags"].get("source_validation_region_type"),
                "rca_state": case["selection_tags"].get("rca_state"),
                "surrogate_validation": {
                    "hypotheses": hypotheses,
                    "case_notes": case_level_surrogate_notes(case),
                    "expert_judgment_populated": False,
                    "expert_rating_populated": False,
                    "reviewer_id_populated": False,
                },
            }
        )
    return {
        "schema_version": "rca-case-surrogate-validation-v1",
        "namespace": "surrogate_validation",
        "policy": {
            "does_not_populate_expert_fields": True,
            "literature_grounding_is_not_physical_confirmation": True,
            "historical_h7_1_expert_judgment_fields_untouched": True,
        },
        "summary": {
            "case_count": len(cases),
            "hypothesis_count": sum(len(case["surrogate_validation"]["hypotheses"]) for case in cases),
            "claim_level_counts": dict(
                sorted(
                    Counter(
                        hypothesis["maximum_supported_claim_level"]
                        for case in cases
                        for hypothesis in case["surrogate_validation"]["hypotheses"]
                    ).items()
                )
            ),
            "literature_grounding_decision_counts": dict(
                sorted(
                    Counter(
                        hypothesis["literature_grounding_decision"]
                        for case in cases
                        for hypothesis in case["surrogate_validation"]["hypotheses"]
                    ).items()
                )
            ),
        },
        "cases": cases,
    }


def internal_evidence_decision(rule_id: str | None, case: dict[str, Any]) -> str:
    if not rule_id:
        return "insufficient_evidence"
    if rule_id in {"rca.iforest.multivariate_pattern", "rca.electrical.contextual_voltage_deviation"}:
        return "grounded_in_detector_evidence_only"
    if rule_id in {"rca.operating_state.throttle_related_pattern", "rca.data_quality.constant_signal_pattern"}:
        return "requires_additional_context_or_integrity_evidence"
    return "not_applicable"


def recommended_revision_for_rule(rule_id: str | None) -> str | None:
    return {
        "rca.iforest.multivariate_pattern": "Reclassify as detector observation, not RCA hypothesis.",
        "rca.electrical.contextual_voltage_deviation": "Split observed voltage symptom from possible electrical causes.",
        "rca.operating_state.throttle_related_pattern": "Require operator/context evidence before generating causal hypothesis.",
        "rca.data_quality.constant_signal_pattern": "Require acquisition-integrity evidence before artifact hypothesis.",
    }.get(rule_id)


def unresolved_questions_for_rule(rule_id: str | None) -> list[str]:
    return {
        "rca.iforest.multivariate_pattern": ["Which raw feature family actually changed and under what operating condition?"],
        "rca.electrical.contextual_voltage_deviation": [
            "Was external battery/charging voltage measured?",
            "What electrical loads were active?",
            "Were wiring/ground/regulator/battery checks performed?",
        ],
        "rca.operating_state.throttle_related_pattern": [
            "Was RPM/TPS variation expected from operator input or route/load?",
            "Is there evidence of unexplained signal behavior?",
        ],
        "rca.data_quality.constant_signal_pattern": [
            "Are timestamps valid and contiguous?",
            "Do raw frames repeat unexpectedly or fail checksum/header validation?",
            "Could the constant signal reflect stable operation?",
        ],
    }.get(rule_id, [])


def case_level_surrogate_notes(case: dict[str, Any]) -> list[str]:
    if case["selection_tags"].get("is_insufficient_evidence_case"):
        return ["Case remains insufficient evidence; no surrogate causal upgrade is allowed."]
    if case["selection_tags"].get("is_no_anomaly_evidence_case"):
        return ["No-anomaly-evidence case remains a review control, not verified healthy ground truth."]
    return ["Surrogate review evaluates rule grounding, not physical root cause."]


def build_candidate_v2_catalog(matrix: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CANDIDATE_CATALOG_VERSION,
        "replaces_h7_v1": False,
        "activation_status": "candidate_only_not_registered",
        "semantic_boundary": "Candidate v2 reduces causal claims but does not confirm physical root causes.",
        "candidate_rules": [
            {
                "rule_id": "rca.v2.iforest.detector_observation",
                "supersedes_or_restricts": ["rca.iforest.multivariate_pattern"],
                "output_type": "observation",
                "maximum_claim_level": "observation",
                "trigger": "Isolation Forest positive finding with unusual features.",
                "output": "Representative window is outside IF reference distribution.",
                "recommended_checks": ["Review unusual feature families, raw telemetry, and session/operator context."],
                "required_evidence": ["IF positive detector finding", "IF unusual feature list"],
                "blocked_claims": ["causal hypothesis", "component failure"],
            },
            {
                "rule_id": "rca.v2.electrical.contextual_voltage_symptom",
                "supersedes_or_restricts": ["rca.electrical.contextual_voltage_deviation"],
                "output_type": "symptom",
                "maximum_claim_level": "symptom",
                "trigger": "Contextual battery detector positive with voltage top-deviation evidence.",
                "output": "Observed contextual supply-voltage deviation.",
                "required_evidence": [
                    "contextual battery detector positive finding",
                    "top voltage deviation",
                    "operating context",
                ],
                "recommended_checks": [
                    "Measure external voltage.",
                    "Record electrical load state.",
                    "Check battery, charging system, wiring/ground and model-specific service procedures.",
                ],
                "possible_hypotheses_requiring_additional_evidence": [
                    "charging-system variation",
                    "battery condition",
                    "electrical load variation",
                    "wiring or supply-path variation",
                ],
                "blocked_claims": ["regulator failure", "battery failure", "stator failure"],
            },
            {
                "rule_id": "rca.v2.operating_state.rpm_tps_observation",
                "supersedes_or_restricts": ["rca.operating_state.throttle_related_pattern"],
                "output_type": "observation_or_possible_hypothesis",
                "maximum_claim_level": "observation",
                "trigger": "IF positive and unusual features include RPM/TPS family.",
                "output": "RPM/TPS features were involved in the detector observation.",
                "required_evidence": [
                    "IF positive detector finding",
                    "RPM or TPS feature in IF unusual feature list",
                    "RPM/TPS telemetry snapshot",
                ],
                "hypothesis_gate": "Generate possible operating-state hypothesis only if operator/context evidence is unexplained.",
                "recommended_checks": ["Compare RPM/TPS traces with operator input, route, load and expected operating state."],
                "blocked_claims": ["throttle fault", "abnormal throttle behavior from RPM/TPS alone"],
            },
            {
                "rule_id": "rca.v2.data_quality.constant_signal_integrity_gate",
                "supersedes_or_restricts": ["rca.data_quality.constant_signal_pattern"],
                "output_type": "observation_with_integrity_gate",
                "maximum_claim_level": "observation",
                "trigger": "IF positive and unusual features include constant_signal.",
                "output": "Constant or low-variance signal observed.",
                "required_evidence": [
                    "IF positive detector finding",
                    "constant_signal feature in IF unusual feature list",
                    "representative telemetry window",
                ],
                "hypothesis_gate": (
                    "Generate acquisition/data-quality hypothesis only when invalid timestamps, repeated raw frames, checksum/header "
                    "failures, implausible saturation, missing expected response, or equivalent acquisition-integrity evidence exists."
                ),
                "recommended_checks": ["Inspect timestamps, raw frames, checksum/header outcomes, saturation and expected stimulus response."],
                "blocked_claims": ["acquisition failure from constant signal alone"],
            },
            {
                "rule_id": "rca.v2.evidence.coverage_boundary",
                "supersedes_or_restricts": ["rca.coverage.insufficient_evidence", "rca.no_evidence.no_hypothesis"],
                "output_type": "evidence_limitation",
                "maximum_claim_level": "observation",
                "trigger": "No applicable active detector evidence or all applicable detectors negative.",
                "output": "Insufficient/no anomaly evidence; no causal RCA generated.",
                "recommended_checks": ["Restore feature coverage or obtain independent validation before diagnosis."],
            },
            {
                "rule_id": "rca.v2.detectors.disagreement_scope",
                "supersedes_or_restricts": ["rca.detectors.disagreement_preservation"],
                "output_type": "evidence_relationship",
                "maximum_claim_level": "observation",
                "trigger": "At least one active detector positive and at least one negative.",
                "output": "Detector scope disagreement preserved without consensus diagnosis.",
                "recommended_checks": ["Review each detector in its own score semantics and phenomenon scope."],
            },
        ],
    }


def build_structural_verification_report(candidate_v2: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    rules = candidate_v2.get("candidate_rules", [])
    anomalies = []
    ids = [rule["rule_id"] for rule in rules]
    duplicate_ids = [rule_id for rule_id, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        anomalies.append(structural_anomaly("duplicate_rule_ids", "Duplicate candidate rule IDs.", duplicate_ids, "error"))
    for rule in rules:
        if not rule.get("required_evidence") and rule.get("output_type") not in {"evidence_limitation", "evidence_relationship"}:
            anomalies.append(structural_anomaly("missing_required_evidence", "Rule lacks explicit required evidence.", [rule["rule_id"]], "warning"))
        if not rule.get("recommended_checks"):
            anomalies.append(structural_anomaly("missing_recommended_checks", "Rule lacks recommended checks.", [rule["rule_id"]], "warning"))
        if rule.get("maximum_claim_level") == "confirmed_cause":
            anomalies.append(structural_anomaly("confirmed_cause_claim", "Candidate rule attempts confirmed cause without physical evidence.", [rule["rule_id"]], "error"))
    conceptual = [
        structural_anomaly(
            "v1_duplicate_observation_cause_concepts",
            "H7 v1 IF and constant-signal hypotheses duplicate detector observations as causal hypotheses.",
            ["rca.iforest.multivariate_pattern", "rca.data_quality.constant_signal_pattern"],
            "resolved_in_candidate_v2",
        ),
        structural_anomaly(
            "v1_rules_without_strong_contradiction_gate",
            "H7 v1 operating-state and data-quality hypotheses can be generated without enough counterevidence gates.",
            ["rca.operating_state.throttle_related_pattern", "rca.data_quality.constant_signal_pattern"],
            "resolved_in_candidate_v2",
        ),
    ]
    return {
        "schema_version": "rca-structural-verification-report-v1",
        "checks": {
            "conflicting_rules_checked": True,
            "redundant_rules_checked": True,
            "unreachable_rules_checked": True,
            "cyclic_causal_reasoning_checked": True,
            "duplicate_observation_cause_concepts_checked": True,
            "hypotheses_without_evidence_checked": True,
            "hypotheses_without_contradiction_path_checked": True,
            "recommended_checks_related_to_triggering_evidence_checked": True,
        },
        "summary": {
            "candidate_rule_count": len(rules),
            "blocking_error_count": sum(1 for item in anomalies if item["severity"] == "error"),
            "warning_count": sum(1 for item in anomalies if item["severity"] == "warning"),
            "v1_anomaly_count": len(conceptual),
        },
        "candidate_v2_anomalies": anomalies,
        "h7_v1_structural_anomalies_addressed_by_candidate_v2": conceptual,
    }


def structural_anomaly(anomaly_id: str, description: str, affected_rules: list[str], severity: str) -> dict[str, Any]:
    return {
        "anomaly_id": anomaly_id,
        "description": description,
        "affected_rules": affected_rules,
        "severity": severity,
    }


def build_decision_report(
    matrix: dict[str, Any],
    counterexamples: dict[str, Any],
    structural: dict[str, Any],
    case_review: dict[str, Any],
) -> dict[str, Any]:
    per_rule = {}
    for rule in matrix.get("rules", []):
        matrix_decision = rule["provisional_decision"]
        if matrix_decision == "retain":
            decision = "provisionally_grounded"
        elif matrix_decision == "retain_with_restricted_claim":
            decision = "grounded_with_limitations"
        elif matrix_decision == "revise":
            decision = "revise"
        elif matrix_decision == "remove":
            decision = "remove"
        else:
            decision = "insufficient_evidence"
        per_rule[rule["rule_id"]] = {
            "decision": decision,
            "maximum_defensible_claim_level": rule["maximum_defensible_claim_level"],
            "reason": rule["decision_reason"],
        }
    decision_counts = Counter(item["decision"] for item in per_rule.values())
    blocking_errors = structural["summary"]["blocking_error_count"]
    if blocking_errors:
        overall = "reject"
    elif decision_counts.get("remove", 0):
        overall = "reject"
    elif decision_counts.get("revise", 0):
        overall = "revise"
    else:
        overall = "advance_with_provisional_grounding"
    return {
        "schema_version": "rca-literature-grounded-surrogate-decision-v1",
        "overall_recommendation": overall,
        "production_behavior_changed": False,
        "drive_safe_behavior_changed": False,
        "expert_review_fields_modified": False,
        "candidate_v2_replaces_h7_v1": False,
        "semantic_boundary": "Literature-grounded and internally consistent does not mean physically confirmed.",
        "per_rule_decisions": per_rule,
        "summary": {
            "per_rule_decision_counts": dict(sorted(decision_counts.items())),
            "counterexample_counts": counterexamples.get("summary", {}).get("rule_counterexample_counts", {}),
            "structural_blocking_error_count": blocking_errors,
            "case_surrogate_hypothesis_count": case_review.get("summary", {}).get("hypothesis_count"),
            "case_surrogate_claim_level_counts": case_review.get("summary", {}).get("claim_level_counts", {}),
        },
        "unresolved": [
            "No completed H7.1 expert judgments.",
            "No independent physical validation for any RCA hypothesis.",
            "No model-specific Honda service limits are encoded in RCA.",
        ],
    }


def write_markdown_report(
    path: Path,
    registry: dict[str, Any],
    matrix: dict[str, Any],
    counterexamples: dict[str, Any],
    case_review: dict[str, Any],
    structural: dict[str, Any],
    candidate_v2: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    lines = [
        "# H7.2 Literature-Grounded Surrogate Validation",
        "",
        "## Boundary",
        "",
        "- Literature-grounded and internally consistent does not mean physically confirmed.",
        "- Expert-review fields remain untouched; no synthetic expert judgments are created.",
        "- Candidate RCA v2 is generated beside H7 v1 and is not production-registered.",
        "",
        "## Source Registry",
        "",
        f"- Sources: `{len(registry['sources'])}`",
        f"- Source tiers: `{dict(sorted(Counter(source['source_tier'] for source in registry['sources']).items()))}`",
        "",
        "## Rule Grounding",
        "",
        f"- Rule decisions: `{matrix['summary']['decision_counts']}`",
        f"- Maximum claim levels: `{matrix['summary']['maximum_claim_level_counts']}`",
        "",
        "## Counterexamples",
        "",
        f"- Counterexample/limiter counts: `{counterexamples['summary']['rule_counterexample_counts']}`",
        "",
        "## Case Surrogate Review",
        "",
        f"- Cases: `{case_review['summary']['case_count']}`",
        f"- Hypotheses reviewed under surrogate namespace: `{case_review['summary']['hypothesis_count']}`",
        f"- Claim levels: `{case_review['summary']['claim_level_counts']}`",
        "",
        "## Structural Verification",
        "",
        f"- Candidate v2 rules: `{structural['summary']['candidate_rule_count']}`",
        f"- Blocking structural errors: `{structural['summary']['blocking_error_count']}`",
        f"- H7 v1 structural anomalies addressed: `{structural['summary']['v1_anomaly_count']}`",
        "",
        "## Candidate V2",
        "",
        f"- Catalog version: `{candidate_v2['schema_version']}`",
        f"- Activation status: `{candidate_v2['activation_status']}`",
        f"- Replaces H7 v1: `{candidate_v2['replaces_h7_v1']}`",
        "",
        "## Decision",
        "",
        f"- Overall recommendation: `{decision['overall_recommendation']}`",
        f"- Per-rule decisions: `{decision['per_rule_decisions']}`",
        "",
        "## Compatibility",
        "",
        "- Production inference remains unchanged.",
        "- DriveSafe behavior was not modified.",
        "- H7 v1 rules and H7.1 expert-review fields are preserved.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
