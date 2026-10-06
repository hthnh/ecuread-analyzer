from __future__ import annotations

from collections import Counter
from typing import Any


RCA_V2_ENGINE_VERSION = "deterministic-rca-engine-v2"
RCA_V2_RULE_CATALOG_VERSION = "evidence-grounded-rca-v2"
V2_TO_H7_RULE_IDS = {
    "rca.v2.iforest.detector_observation": ["rca.iforest.multivariate_pattern"],
    "rca.v2.electrical.contextual_voltage_symptom": ["rca.electrical.contextual_voltage_deviation"],
    "rca.v2.operating_state.rpm_tps_observation": ["rca.operating_state.throttle_related_pattern"],
    "rca.v2.data_quality.constant_signal_integrity_gate": ["rca.data_quality.constant_signal_pattern"],
    "rca.v2.evidence.coverage_boundary": ["rca.coverage.insufficient_evidence", "rca.no_evidence.no_hypothesis"],
    "rca.v2.detectors.disagreement_scope": ["rca.detectors.disagreement_preservation"],
}


def rca_v2_contract() -> dict[str, Any]:
    return {
        "schema_version": "root-cause-analysis-v2-contract-v1",
        "engine_version": RCA_V2_ENGINE_VERSION,
        "rule_catalog_version": RCA_V2_RULE_CATALOG_VERSION,
        "semantic_sections": {
            "observations": "Direct telemetry or detector evidence. Observations are not causes.",
            "symptoms": "Evidence-backed non-causal interpretations derived from observations.",
            "possible_causes": "Optional causal hypotheses emitted only when extra case evidence justifies that claim level.",
            "recommended_checks": "Evidence-tied follow-up actions that do not require a cause hypothesis.",
        },
        "policy": {
            "llm_used": False,
            "agents_used": False,
            "production_behavior_changed": False,
            "drive_safe_behavior_changed": False,
            "score_fusion_used": False,
            "confidence_percentages_allowed": False,
            "empty_possible_cause_list_allowed": True,
            "h7_v1_reproducibility_preserved": True,
        },
        "claim_levels": ["observation", "symptom", "possible_hypothesis"],
        "blocked_claims": [
            "battery failure from contextual voltage deviation alone",
            "regulator failure from contextual voltage deviation alone",
            "stator failure from contextual voltage deviation alone",
            "throttle fault from RPM/TPS features alone",
            "acquisition failure from constant-signal feature alone",
            "component failure from Isolation Forest anomaly alone",
        ],
    }


def diagnostic_check_catalog() -> dict[str, Any]:
    return {
        "schema_version": "rca-v2-diagnostic-check-catalog-v1",
        "policy": {
            "checks_are_follow_up_actions_not_fault_claims": True,
            "checks_tied_to_observed_evidence": True,
            "maintenance_knowledge_base_scope": "minimal",
        },
        "checks": [
            {
                "check_id": "check.v2.iforest_feature_review",
                "category": "multivariate_review",
                "description": "Inspect IF-listed feature families in raw telemetry and compare with operator/session notes.",
                "trigger_evidence": ["isolation_forest positive finding", "most_unusual_features"],
            },
            {
                "check_id": "check.v2.voltage_context_compare",
                "category": "electrical_voltage",
                "description": "Compare voltage behavior across similar RPM/TPS operating contexts and repeat captures.",
                "trigger_evidence": ["contextual battery-voltage deviation"],
            },
            {
                "check_id": "check.v2.external_supply_measurement",
                "category": "electrical_voltage",
                "description": "Inspect charging/supply measurements and battery terminal/supply-path condition when physically appropriate.",
                "trigger_evidence": ["contextual supply-voltage symptom"],
            },
            {
                "check_id": "check.v2.rpm_tps_reproduction",
                "category": "operating_context",
                "description": "Reproduce the same operating state and compare RPM response against throttle behavior.",
                "trigger_evidence": ["RPM/TPS feature involvement"],
            },
            {
                "check_id": "check.v2.rpm_tps_repeatability",
                "category": "operating_context",
                "description": "Inspect whether the RPM/TPS pattern repeats across independent sessions.",
                "trigger_evidence": ["RPM/TPS feature involvement"],
            },
            {
                "check_id": "check.v2.raw_frame_integrity",
                "category": "data_quality",
                "description": "Inspect raw frames, timestamp/frame continuity and checksum/header outcomes.",
                "trigger_evidence": ["constant-signal feature", "missing or suspect acquisition integrity evidence"],
            },
            {
                "check_id": "check.v2.known_stimulus_response",
                "category": "data_quality",
                "description": "Check whether the signal changes under a known stimulus before inferring acquisition failure.",
                "trigger_evidence": ["constant-signal feature"],
            },
            {
                "check_id": "check.v2.feature_coverage",
                "category": "coverage",
                "description": "Restore required features or inspect preprocessing before causal interpretation.",
                "trigger_evidence": ["insufficient detector coverage"],
            },
            {
                "check_id": "check.v2.detector_scope_review",
                "category": "evidence_review",
                "description": "Review each detector in its own score semantics and phenomenon scope.",
                "trigger_evidence": ["detector disagreement"],
            },
            {
                "check_id": "check.v2.no_evidence_boundary",
                "category": "validation_boundary",
                "description": "Do not treat no detector anomaly evidence as verified healthy without independent validation.",
                "trigger_evidence": ["all applicable detectors negative"],
            },
        ],
    }


class RootCauseAnalyzerV2:
    def __init__(
        self,
        *,
        source_registry: dict[str, Any] | None = None,
        grounding_matrix: dict[str, Any] | None = None,
        candidate_catalog: dict[str, Any] | None = None,
        check_catalog: dict[str, Any] | None = None,
    ) -> None:
        self.source_registry = source_registry or {}
        self.grounding_matrix = grounding_matrix or {}
        self.candidate_catalog = candidate_catalog or {}
        self.check_catalog = check_catalog or diagnostic_check_catalog()
        self.sources_by_rule = {
            rule["rule_id"]: list(rule.get("supporting_sources") or [])
            for rule in self.grounding_matrix.get("rules", [])
        }
        self.grounding_by_rule = {
            rule["rule_id"]: rule for rule in self.grounding_matrix.get("rules", [])
        }
        self.checks_by_id = {check["check_id"]: check for check in self.check_catalog.get("checks", [])}

    def analyze_case(self, case: dict[str, Any]) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        symptoms: list[dict[str, Any]] = []
        possible_causes: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        limitations: list[dict[str, Any]] = []

        evidence = observable_evidence(case)
        detector_findings = evidence.get("detector_findings", {})
        event = case.get("provenance", {}).get("source_event") or {}
        source_event_id = event.get("event_id")

        observations.append(self.observation(case, "obs.v2.aggregate_evidence", "aggregate_evidence", "Aggregate detector evidence state preserved.", {
            "evidence_state": event.get("evidence_state"),
            "coverage_status": event.get("coverage_status"),
            "positive_detector_ids": detector_findings.get("positive_detector_ids", []),
            "negative_detector_ids": detector_findings.get("negative_detector_ids", []),
            "unavailable_detector_ids": detector_findings.get("unavailable_detector_ids", []),
        }, source_rule_id="rca.v2.evidence.coverage_boundary"))

        if is_insufficient_case(case):
            limitations.append(self.limitation("limit.v2.insufficient_coverage", "Detector coverage is insufficient for causal interpretation.", case))
            checks.append(self.check("check.v2.feature_coverage", case, "rca.v2.evidence.coverage_boundary"))
        if is_no_evidence_case(case):
            limitations.append(self.limitation("limit.v2.no_anomaly_evidence", "No active detector anomaly evidence is not verified healthy ground truth.", case))
            checks.append(self.check("check.v2.no_evidence_boundary", case, "rca.v2.evidence.coverage_boundary"))

        if iforest_positive(case):
            features = unusual_features(case)
            observations.append(
                self.observation(
                    case,
                    "obs.v2.iforest_outside_reference",
                    "detector_observation",
                    "Isolation Forest reported a pattern outside its learned reference distribution.",
                    {
                        "detector_id": "isolation_forest",
                        "most_unusual_features": features,
                        "detector_finding": finding_for(case, "isolation_forest"),
                    },
                    source_rule_id="rca.v2.iforest.detector_observation",
                    h7_rule_id="rca.iforest.multivariate_pattern",
                )
            )
            checks.append(self.check("check.v2.iforest_feature_review", case, "rca.v2.iforest.detector_observation"))

            if rpm_tps_features(features):
                observations.append(
                    self.observation(
                        case,
                        "obs.v2.rpm_tps_feature_involvement",
                        "operating_pattern_observation",
                        "RPM/TPS feature families were involved in the detector observation.",
                        {
                            "features": [feature for feature in features if feature.startswith("rpm_") or feature.startswith("tps_")],
                            "telemetry": telemetry(case),
                            "operating_context": evidence.get("operating_context"),
                        },
                        source_rule_id="rca.v2.operating_state.rpm_tps_observation",
                        h7_rule_id="rca.operating_state.throttle_related_pattern",
                    )
                )
                checks.append(self.check("check.v2.rpm_tps_reproduction", case, "rca.v2.operating_state.rpm_tps_observation"))
                checks.append(self.check("check.v2.rpm_tps_repeatability", case, "rca.v2.operating_state.rpm_tps_observation"))
                cause = self.operating_context_possible_cause(case)
                if cause is not None:
                    possible_causes.append(cause)

            constant = [feature for feature in features if "constant_signal" in feature]
            if constant:
                observations.append(
                    self.observation(
                        case,
                        "obs.v2.constant_signal_feature",
                        "signal_feature_observation",
                        "A constant or low-variance signal feature was observed in the IF unusual-feature list.",
                        {
                            "constant_signal_features": constant,
                            "all_unusual_features": features,
                        },
                        source_rule_id="rca.v2.data_quality.constant_signal_integrity_gate",
                        h7_rule_id="rca.data_quality.constant_signal_pattern",
                    )
                )
                checks.append(self.check("check.v2.raw_frame_integrity", case, "rca.v2.data_quality.constant_signal_integrity_gate"))
                checks.append(self.check("check.v2.known_stimulus_response", case, "rca.v2.data_quality.constant_signal_integrity_gate"))
                cause = self.data_quality_possible_cause(case, constant)
                if cause is not None:
                    possible_causes.append(cause)

        if contextual_positive(case) and contextual_top_deviation(case):
            top = contextual_top_deviation(case)
            observations.append(
                self.observation(
                    case,
                    "obs.v2.contextual_voltage_deviation",
                    "detector_observation",
                    "Contextual battery-voltage deviation was observed.",
                    {
                        "top_deviation": top,
                        "detector_finding": finding_for(case, "contextual_battery_voltage"),
                        "operating_context": evidence.get("operating_context"),
                    },
                    source_rule_id="rca.v2.electrical.contextual_voltage_symptom",
                    h7_rule_id="rca.electrical.contextual_voltage_deviation",
                )
            )
            symptoms.append(
                self.symptom(
                    case,
                    "sym.v2.contextual_supply_voltage_deviation",
                    "electrical_voltage",
                    "Contextual supply-voltage deviation.",
                    {
                        "top_deviation": top,
                        "telemetry": telemetry(case),
                        "operating_context": evidence.get("operating_context"),
                    },
                    source_rule_id="rca.v2.electrical.contextual_voltage_symptom",
                    h7_rule_id="rca.electrical.contextual_voltage_deviation",
                )
            )
            checks.append(self.check("check.v2.voltage_context_compare", case, "rca.v2.electrical.contextual_voltage_symptom"))
            checks.append(self.check("check.v2.external_supply_measurement", case, "rca.v2.electrical.contextual_voltage_symptom"))
            cause = self.electrical_possible_cause(case)
            if cause is not None:
                possible_causes.append(cause)

        if detector_disagreement(case):
            observations.append(
                self.observation(
                    case,
                    "obs.v2.detector_disagreement",
                    "evidence_relationship",
                    "Active detector disagreement is preserved without consensus diagnosis.",
                    {
                        "positive_detector_ids": detector_findings.get("positive_detector_ids", []),
                        "negative_detector_ids": detector_findings.get("negative_detector_ids", []),
                    },
                    source_rule_id="rca.v2.detectors.disagreement_scope",
                    h7_rule_id="rca.detectors.disagreement_preservation",
                )
            )
            checks.append(self.check("check.v2.detector_scope_review", case, "rca.v2.detectors.disagreement_scope"))
            limitations.append(self.limitation("limit.v2.detector_disagreement", "Detector disagreement is a scope relationship, not evidence that one detector is wrong.", case))

        if not possible_causes:
            limitations.append(self.limitation("limit.v2.no_possible_cause", "Available evidence does not justify a causal hypothesis under RCA v2 gates.", case))

        return {
            "schema_version": "root-cause-analysis-v2-result-v1",
            "rca_v2_result_id": f"h7-3-rca-v2:{source_event_id or case.get('case_id')}",
            "source_case_id": case.get("case_id"),
            "source_review_item_id": case.get("source_review_item_id"),
            "session_id": case.get("provenance", {}).get("session_id"),
            "source_event": event,
            "observations": dedupe_by_id(observations, "observation_id"),
            "symptoms": dedupe_by_id(symptoms, "symptom_id"),
            "possible_causes": dedupe_by_id(possible_causes, "cause_id"),
            "recommended_checks": dedupe_by_id(checks, "check_id"),
            "evidence_limitations": dedupe_by_id(limitations, "limitation_id"),
            "comparison_to_h7_v1": {
                "v1_hypothesis_count": len(case.get("review_presentation", {}).get("rca_output", {}).get("hypotheses", [])),
                "unsupported_v1_causal_claims_removed": unsupported_v1_claims_removed(case, possible_causes),
            },
            "provenance": {
                "engine_version": RCA_V2_ENGINE_VERSION,
                "rule_catalog_version": RCA_V2_RULE_CATALOG_VERSION,
                "candidate_catalog_version": self.candidate_catalog.get("schema_version"),
                "source_case_provenance": case.get("provenance"),
                "h7_v1_available_for_reproducibility": True,
            },
        }

    def observation(
        self,
        case: dict[str, Any],
        observation_id: str,
        observation_type: str,
        statement: str,
        evidence: dict[str, Any],
        *,
        source_rule_id: str,
        h7_rule_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "observation_id": observation_id,
            "observation_type": observation_type,
            "statement": statement,
            "claim_level": "observation",
            "evidence": evidence,
            "provenance": self.provenance(case, source_rule_id, h7_rule_id),
        }

    def symptom(
        self,
        case: dict[str, Any],
        symptom_id: str,
        symptom_type: str,
        statement: str,
        evidence: dict[str, Any],
        *,
        source_rule_id: str,
        h7_rule_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "symptom_id": symptom_id,
            "symptom_type": symptom_type,
            "statement": statement,
            "claim_level": "symptom",
            "evidence": evidence,
            "provenance": self.provenance(case, source_rule_id, h7_rule_id),
        }

    def possible_cause(
        self,
        case: dict[str, Any],
        cause_id: str,
        cause_type: str,
        statement: str,
        evidence: dict[str, Any],
        limiting_evidence: list[dict[str, Any]],
        *,
        source_rule_id: str,
        h7_rule_id: str | None = None,
        check_ids: list[str],
    ) -> dict[str, Any]:
        return {
            "cause_id": cause_id,
            "cause_type": cause_type,
            "statement": statement,
            "claim_level": "possible_hypothesis",
            "supporting_evidence": evidence,
            "limiting_or_contradicting_evidence": limiting_evidence,
            "grounding_source_ids": self.source_ids_for(source_rule_id, h7_rule_id),
            "maximum_claim_level": "possible_hypothesis",
            "recommended_verification_steps": [self.check(check_id, case, source_rule_id) for check_id in check_ids],
            "provenance": self.provenance(case, source_rule_id, h7_rule_id),
        }

    def check(self, check_id: str, case: dict[str, Any], source_rule_id: str) -> dict[str, Any]:
        payload = dict(self.checks_by_id.get(check_id, {"check_id": check_id, "description": check_id, "category": "unknown"}))
        payload["provenance"] = self.provenance(case, source_rule_id)
        return payload

    def limitation(self, limitation_id: str, statement: str, case: dict[str, Any]) -> dict[str, Any]:
        return {
            "limitation_id": limitation_id,
            "statement": statement,
            "provenance": {
                "source_event": case.get("provenance", {}).get("source_event"),
                "source_case_id": case.get("case_id"),
            },
        }

    def provenance(self, case: dict[str, Any], source_rule_id: str, h7_rule_id: str | None = None) -> dict[str, Any]:
        return {
            "source_event": case.get("provenance", {}).get("source_event"),
            "detector_output": observable_evidence(case).get("detector_findings"),
            "telemetry": telemetry(case),
            "grounding_source_ids": self.source_ids_for(source_rule_id, h7_rule_id),
            "rule_version": RCA_V2_RULE_CATALOG_VERSION,
            "source_rule_id": source_rule_id,
            "h7_v1_rule_id": h7_rule_id,
        }

    def source_ids_for(self, source_rule_id: str, h7_rule_id: str | None = None) -> list[str]:
        source_ids = list(self.sources_by_rule.get(source_rule_id, []))
        for mapped_rule_id in V2_TO_H7_RULE_IDS.get(source_rule_id, []):
            source_ids.extend(self.sources_by_rule.get(mapped_rule_id, []))
        if h7_rule_id:
            source_ids.extend(self.sources_by_rule.get(h7_rule_id, []))
        return sorted(set(source_ids))

    def electrical_possible_cause(self, case: dict[str, Any]) -> dict[str, Any] | None:
        evidence = additional_causal_evidence(case)
        if not evidence.get("external_voltage_measurement") and not evidence.get("physical_electrical_inspection"):
            return None
        return self.possible_cause(
            case,
            "cause.v2.electrical_supply_path_candidate",
            "electrical_supply_candidate",
            "Electrical supply-path variation is a possible cause candidate, pending physical verification.",
            evidence,
            limiting_evidence=[
                {"statement": "Contextual voltage deviation alone does not identify battery, regulator, stator or wiring failure."}
            ],
            source_rule_id="rca.v2.electrical.contextual_voltage_symptom",
            h7_rule_id="rca.electrical.contextual_voltage_deviation",
            check_ids=["check.v2.external_supply_measurement", "check.v2.voltage_context_compare"],
        )

    def operating_context_possible_cause(self, case: dict[str, Any]) -> dict[str, Any] | None:
        evidence = additional_causal_evidence(case)
        if not evidence.get("operator_context_unexplained"):
            return None
        return self.possible_cause(
            case,
            "cause.v2.unexplained_operating_state_variation",
            "operating_context_candidate",
            "Unexplained operating-state variation is a possible cause candidate.",
            evidence,
            limiting_evidence=[{"statement": "RPM/TPS features alone can reflect normal operator demand."}],
            source_rule_id="rca.v2.operating_state.rpm_tps_observation",
            h7_rule_id="rca.operating_state.throttle_related_pattern",
            check_ids=["check.v2.rpm_tps_reproduction", "check.v2.rpm_tps_repeatability"],
        )

    def data_quality_possible_cause(self, case: dict[str, Any], constant_features: list[str]) -> dict[str, Any] | None:
        evidence = additional_causal_evidence(case)
        integrity_keys = [
            "invalid_timestamps",
            "repeated_raw_frames",
            "checksum_or_header_failures",
            "implausible_saturation",
            "missing_expected_stimulus_response",
        ]
        if not any(evidence.get(key) for key in integrity_keys):
            return None
        return self.possible_cause(
            case,
            "cause.v2.acquisition_integrity_candidate",
            "data_quality_candidate",
            "Acquisition/data-integrity issue is a possible cause candidate because integrity evidence accompanies constant-signal features.",
            {"constant_signal_features": constant_features, "integrity_evidence": evidence},
            limiting_evidence=[{"statement": "Constant signal alone can also reflect stable operation."}],
            source_rule_id="rca.v2.data_quality.constant_signal_integrity_gate",
            h7_rule_id="rca.data_quality.constant_signal_pattern",
            check_ids=["check.v2.raw_frame_integrity", "check.v2.known_stimulus_response"],
        )


def summarize_v2_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "result_count": len(results),
        "observation_count": sum(len(result["observations"]) for result in results),
        "symptom_count": sum(len(result["symptoms"]) for result in results),
        "possible_cause_count": sum(len(result["possible_causes"]) for result in results),
        "recommended_check_count": sum(len(result["recommended_checks"]) for result in results),
        "empty_possible_cause_case_count": sum(1 for result in results if not result["possible_causes"]),
        "cases_with_recommended_checks_without_causes": sum(
            1 for result in results if result["recommended_checks"] and not result["possible_causes"]
        ),
        "observation_type_counts": dict(sorted(Counter(obs["observation_type"] for result in results for obs in result["observations"]).items())),
        "symptom_type_counts": dict(sorted(Counter(symptom["symptom_type"] for result in results for symptom in result["symptoms"]).items())),
        "possible_cause_type_counts": dict(sorted(Counter(cause["cause_type"] for result in results for cause in result["possible_causes"]).items())),
        "unsupported_v1_causal_claims_removed": sum(
            result["comparison_to_h7_v1"]["unsupported_v1_causal_claims_removed"] for result in results
        ),
    }


def observable_evidence(case: dict[str, Any]) -> dict[str, Any]:
    return dict(case.get("review_presentation", {}).get("observable_evidence") or {})


def telemetry(case: dict[str, Any]) -> dict[str, Any]:
    return dict(observable_evidence(case).get("telemetry") or {})


def detector_findings(case: dict[str, Any]) -> dict[str, Any]:
    return dict(observable_evidence(case).get("detector_findings") or {})


def finding_for(case: dict[str, Any], detector_id: str) -> dict[str, Any]:
    return dict((detector_findings(case).get("by_detector") or {}).get(detector_id) or {})


def iforest_positive(case: dict[str, Any]) -> bool:
    finding = finding_for(case, "isolation_forest")
    return finding.get("execution_status") == "ok" and finding.get("applicable") is True and finding.get("finding") == "positive"


def contextual_positive(case: dict[str, Any]) -> bool:
    finding = finding_for(case, "contextual_battery_voltage")
    return finding.get("execution_status") == "ok" and finding.get("applicable") is True and finding.get("finding") == "positive"


def contextual_top_deviation(case: dict[str, Any]) -> dict[str, Any] | None:
    top = observable_evidence(case).get("unusual_feature_evidence", {}).get("contextual_top_deviation")
    return top if isinstance(top, dict) else None


def detector_disagreement(case: dict[str, Any]) -> bool:
    findings = detector_findings(case)
    return bool(findings.get("positive_detector_ids")) and bool(findings.get("negative_detector_ids"))


def is_insufficient_case(case: dict[str, Any]) -> bool:
    tags = case.get("selection_tags", {})
    event = case.get("provenance", {}).get("source_event") or {}
    return tags.get("is_insufficient_evidence_case") is True or event.get("coverage_status") == "none" or event.get("evidence_state") == "insufficient_coverage"


def is_no_evidence_case(case: dict[str, Any]) -> bool:
    return case.get("selection_tags", {}).get("is_no_anomaly_evidence_case") is True


def unusual_features(case: dict[str, Any]) -> list[str]:
    raw = observable_evidence(case).get("unusual_feature_evidence", {}).get("isolation_forest_most_unusual_features")
    if isinstance(raw, str):
        return [feature.strip() for feature in raw.split(";") if feature.strip()]
    if isinstance(raw, list):
        return [str(feature) for feature in raw if str(feature)]
    return []


def rpm_tps_features(features: list[str]) -> bool:
    return any(feature.startswith("rpm_") or feature.startswith("tps_") for feature in features)


def additional_causal_evidence(case: dict[str, Any]) -> dict[str, Any]:
    return dict(case.get("additional_causal_evidence") or case.get("review_presentation", {}).get("additional_causal_evidence") or {})


def unsupported_v1_claims_removed(case: dict[str, Any], possible_causes: list[dict[str, Any]]) -> int:
    v1_hypotheses = case.get("review_presentation", {}).get("rca_output", {}).get("hypotheses", [])
    return max(0, len(v1_hypotheses) - len(possible_causes))


def dedupe_by_id(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for item in items:
        value = str(item.get(key))
        if value in seen:
            continue
        seen.add(value)
        output.append(item)
    return output
