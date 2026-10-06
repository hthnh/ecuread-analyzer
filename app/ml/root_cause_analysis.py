from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from app.ml.contextual_detector import CONTEXTUAL_BATTERY_DETECTOR_ID
from app.ml.evidence_aggregation import ACTIVE_H5_DETECTOR_IDS, REJECTED_TEMPORAL_CANDIDATE_IDS
from app.ml.iforest_detector import ISOLATION_FOREST_DETECTOR_ID


RCA_ENGINE_VERSION = "deterministic-rca-engine-v1"
RCA_RULE_CATALOG_VERSION = "evidence-based-rca-rules-v1"

HypothesisStatus = str
RuleEvaluator = Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class RCARule:
    rule_id: str
    version: str
    description: str
    required_signals: tuple[str, ...]
    required_detector_findings: tuple[str, ...]
    optional_supporting_evidence: tuple[str, ...]
    contradicting_evidence: tuple[str, ...]
    applicability_conditions: tuple[str, ...]
    resulting_hypothesis: dict[str, Any] | None
    recommended_verification_step: str
    evaluator: RuleEvaluator = field(repr=False, compare=False)

    def to_contract(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("evaluator", None)
        return payload


def rca_contract() -> dict[str, Any]:
    return {
        "schema_version": "root-cause-analysis-contract-v1",
        "engine_version": RCA_ENGINE_VERSION,
        "rule_catalog_version": RCA_RULE_CATALOG_VERSION,
        "active_detector_ids": list(ACTIVE_H5_DETECTOR_IDS),
        "excluded_detector_ids": list(REJECTED_TEMPORAL_CANDIDATE_IDS),
        "policy": {
            "llm_used": False,
            "agents_used": False,
            "production_behavior_changed": False,
            "drive_safe_behavior_changed": False,
            "score_fusion_used": False,
            "physical_fault_claims_allowed": False,
            "absolute_diagnosis_claims_allowed": False,
            "quantified_likelihood_allowed": False,
            "detector_predictions_modified": False,
        },
        "observation_vs_hypothesis_boundary": {
            "observation": "Directly supported by telemetry or detector evidence.",
            "hypothesis": "A possible explanation for observations; not a physical fault finding.",
        },
        "hypothesis_statuses": ["possible", "supported", "insufficient_evidence", "contradicted"],
        "rules": [rule.to_contract() for rule in rca_rules()],
    }


class RootCauseAnalyzer:
    def __init__(self, rules: list[RCARule] | None = None) -> None:
        self.rules = rules or rca_rules()

    def analyze_item(self, item: dict[str, Any]) -> dict[str, Any]:
        observations = observed_facts(item)
        applied_rules: list[dict[str, Any]] = []
        hypotheses: list[dict[str, Any]] = []
        supporting_evidence: list[dict[str, Any]] = []
        contradicting_evidence: list[dict[str, Any]] = []
        recommended_checks: list[dict[str, Any]] = []

        for rule in self.rules:
            evaluation = rule.evaluator(item, observations)
            applied_rules.append(
                {
                    "rule_id": rule.rule_id,
                    "version": rule.version,
                    "applied": bool(evaluation.get("applied")),
                    "status": evaluation.get("status"),
                    "reason": evaluation.get("reason"),
                }
            )
            hypothesis = evaluation.get("hypothesis")
            if hypothesis is not None:
                hypotheses.append(hypothesis)
                supporting_evidence.extend(hypothesis.get("triggering_evidence", []))
                contradicting_evidence.extend(hypothesis.get("evidence_against", []))
            recommended_checks.extend(evaluation.get("recommended_checks", []))

        limitations = evidence_limitations(item, applied_rules, hypotheses)
        result_state = rca_state(item, hypotheses)
        return {
            "schema_version": "root-cause-analysis-result-v1",
            "rca_result_id": f"h7-rca:{event_identity(item).get('event_id') or item.get('validation_item_id')}",
            "source_validation_item_id": item.get("validation_item_id"),
            "source_h5_review_item_id": item.get("source_h5_review_item_id"),
            "session_id": item.get("session_id"),
            "event": event_identity(item),
            "rca_state": result_state,
            "observed_facts": observations,
            "supporting_evidence": dedupe_evidence(supporting_evidence),
            "contradicting_evidence": dedupe_evidence(contradicting_evidence),
            "hypotheses": hypotheses,
            "recommended_checks": dedupe_checks(recommended_checks),
            "evidence_limitations": limitations,
            "applied_rules": applied_rules,
            "provenance": {
                "rule_engine_version": RCA_ENGINE_VERSION,
                "rule_catalog_version": RCA_RULE_CATALOG_VERSION,
                "active_detector_ids": list(ACTIVE_H5_DETECTOR_IDS),
                "excluded_detector_ids": list(REJECTED_TEMPORAL_CANDIDATE_IDS),
                "source_provenance": item.get("provenance"),
                "h6_independent_label": item.get("independent_label"),
            },
            "diagnostic_boundary": {
                "physical_fault_claim_made": False,
                "quantified_likelihood_provided": False,
                "requires_expert_or_physical_validation": True,
            },
        }


def rca_rules() -> list[RCARule]:
    return [
        RCARule(
            rule_id="rca.coverage.insufficient_evidence",
            version="v1",
            description="Stop causal hypothesis generation when no active detector produced applicable evidence.",
            required_signals=(),
            required_detector_findings=("zero_applicable_active_detectors",),
            optional_supporting_evidence=("missing_features", "skipped_detector_reasons"),
            contradicting_evidence=(),
            applicability_conditions=("aggregate coverage status is none", "aggregate evidence state is insufficient_coverage"),
            resulting_hypothesis=None,
            recommended_verification_step="Re-run analysis with the required feature set or inspect upstream feature extraction before RCA.",
            evaluator=evaluate_insufficient_coverage,
        ),
        RCARule(
            rule_id="rca.no_evidence.no_hypothesis",
            version="v1",
            description="Do not generate RCA hypotheses when active detectors report no anomaly evidence.",
            required_signals=(),
            required_detector_findings=("all_applicable_detectors_negative",),
            optional_supporting_evidence=("detector-local negative scores",),
            contradicting_evidence=(),
            applicability_conditions=("no positive active detector findings", "at least one active detector applicable"),
            resulting_hypothesis=None,
            recommended_verification_step="Treat as no detector anomaly evidence, not as verified healthy ground truth.",
            evaluator=evaluate_no_evidence,
        ),
        RCARule(
            rule_id="rca.electrical.contextual_voltage_deviation",
            version="v1",
            description="Generate an electrical-supply variation hypothesis from contextual battery-voltage deviation.",
            required_signals=("battery_voltage_median", "battery_voltage_mean", "battery_voltage_min", "operating_context"),
            required_detector_findings=("contextual_battery_voltage positive",),
            optional_supporting_evidence=("isolation_forest battery-related unusual features",),
            contradicting_evidence=("isolation_forest negative or unavailable for the same representative window",),
            applicability_conditions=("contextual detector execution status is ok", "top contextual deviation is available"),
            resulting_hypothesis={
                "hypothesis_id": "h.electrical_supply_variation",
                "status": "supported",
            },
            recommended_verification_step="Measure battery/charging voltage externally and record electrical load state during a repeat capture.",
            evaluator=evaluate_contextual_voltage_deviation,
        ),
        RCARule(
            rule_id="rca.iforest.multivariate_pattern",
            version="v1",
            description="Generate a broad multivariate-pattern hypothesis from Isolation Forest anomaly evidence.",
            required_signals=("detector most_unusual_features",),
            required_detector_findings=("isolation_forest positive",),
            optional_supporting_evidence=("RPM/TPS/battery telemetry", "contextual detector finding"),
            contradicting_evidence=("contextual_battery_voltage negative for an electrical-specific explanation",),
            applicability_conditions=("isolation forest execution status is ok", "isolation forest anomaly flag is true"),
            resulting_hypothesis={
                "hypothesis_id": "h.multivariate_pattern_outside_reference",
                "status": "possible",
            },
            recommended_verification_step="Review the listed feature families and operating context against raw telemetry and operator notes.",
            evaluator=evaluate_iforest_multivariate_pattern,
        ),
        RCARule(
            rule_id="rca.operating_state.throttle_related_pattern",
            version="v1",
            description="Generate a throttle/operating-state hypothesis when IF evidence highlights RPM or TPS features.",
            required_signals=("rpm_median", "tps_raw_median or tps_voltage_median"),
            required_detector_findings=("isolation_forest positive",),
            optional_supporting_evidence=("operating_context",),
            contradicting_evidence=("missing TPS/RPM telemetry",),
            applicability_conditions=("IF unusual feature list includes rpm or tps",),
            resulting_hypothesis={
                "hypothesis_id": "h.operating_state_or_throttle_input_variation",
                "status": "possible",
            },
            recommended_verification_step="Compare RPM/TPS timing with operator input and route/load notes; do not infer RPM solely from TPS.",
            evaluator=evaluate_operating_state_pattern,
        ),
        RCARule(
            rule_id="rca.data_quality.constant_signal_pattern",
            version="v1",
            description="Generate a possible acquisition/data-quality hypothesis for IF events involving constant-signal features.",
            required_signals=("detector most_unusual_features",),
            required_detector_findings=("isolation_forest positive",),
            optional_supporting_evidence=("feature names containing constant_signal",),
            contradicting_evidence=("stable operation notes or external corroboration, when available",),
            applicability_conditions=("IF unusual feature list contains constant_signal"),
            resulting_hypothesis={
                "hypothesis_id": "h.possible_acquisition_or_constant_signal_artifact",
                "status": "possible",
            },
            recommended_verification_step="Inspect raw frames for dropouts, repeated values, sensor saturation, or intentional stable operation.",
            evaluator=evaluate_constant_signal_pattern,
        ),
        RCARule(
            rule_id="rca.detectors.disagreement_preservation",
            version="v1",
            description="Preserve detector disagreement without forcing a shared diagnosis.",
            required_signals=(),
            required_detector_findings=("at least one positive detector", "at least one negative detector"),
            optional_supporting_evidence=("detector-local scores", "operating context"),
            contradicting_evidence=(),
            applicability_conditions=("aggregate evidence state is detector_disagreement"),
            resulting_hypothesis=None,
            recommended_verification_step="Review each detector's phenomenon separately; do not merge scores or infer consensus.",
            evaluator=evaluate_detector_disagreement,
        ),
    ]


def observed_facts(item: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    event = item.get("event", {})
    telemetry = telemetry_snapshot(item)
    facts.append(
        observation(
            "obs.aggregate_evidence_state",
            "aggregate_evidence",
            f"Aggregate evidence state is {event.get('evidence_state')} with {event.get('coverage_status')} detector coverage.",
            {
                "evidence_state": event.get("evidence_state"),
                "coverage_status": event.get("coverage_status"),
                "positive_detector_ids": detector_ids(item, "positive"),
                "negative_detector_ids": detector_ids(item, "negative"),
                "unavailable_detector_ids": detector_ids(item, "unavailable"),
            },
        )
    )
    if telemetry:
        facts.append(
            observation(
                "obs.telemetry_context",
                "telemetry",
                "Representative telemetry values are preserved for RCA review.",
                telemetry,
            )
        )

    for detector_id, finding in detector_findings(item).items():
        facts.append(detector_observation(detector_id, finding))

    contextual = finding_for(item, CONTEXTUAL_BATTERY_DETECTOR_ID)
    top = contextual_top_deviation(item)
    if is_positive(contextual) and top:
        facts.append(
            observation(
                "obs.contextual_voltage_deviation",
                "detector_evidence",
                "Battery-voltage telemetry deviated from contextual reference statistics.",
                {
                    "detector_id": CONTEXTUAL_BATTERY_DETECTOR_ID,
                    "operating_context": operating_context(item),
                    "top_deviation": top,
                    "score": contextual.get("detector_local_score"),
                    "threshold": contextual.get("detector_local_threshold"),
                },
            )
        )

    if is_positive(finding_for(item, ISOLATION_FOREST_DETECTOR_ID)):
        facts.append(
            observation(
                "obs.iforest_multivariate_anomaly",
                "detector_evidence",
                "Isolation Forest detected a multivariate anomaly in the representative window.",
                {
                    "detector_id": ISOLATION_FOREST_DETECTOR_ID,
                    "score": finding_for(item, ISOLATION_FOREST_DETECTOR_ID).get("detector_local_score"),
                    "threshold": finding_for(item, ISOLATION_FOREST_DETECTOR_ID).get("detector_local_threshold"),
                    "most_unusual_features": unusual_features(item),
                },
            )
        )

    if detector_ids(item, "positive") and detector_ids(item, "negative"):
        facts.append(
            observation(
                "obs.detector_disagreement",
                "aggregate_evidence",
                "Active detectors disagreed for this representative window.",
                {
                    "positive_detector_ids": detector_ids(item, "positive"),
                    "negative_detector_ids": detector_ids(item, "negative"),
                },
            )
        )
    return facts


def observation(observation_id: str, kind: str, description: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "observation_id": observation_id,
        "kind": kind,
        "description": description,
        "evidence": evidence,
        "is_hypothesis": False,
    }


def detector_observation(detector_id: str, finding: dict[str, Any]) -> dict[str, Any]:
    return observation(
        f"obs.detector.{detector_id}",
        "detector_finding",
        f"{detector_id} finding is {finding.get('finding')} with execution status {finding.get('execution_status')}.",
        {
            "detector_id": detector_id,
            "model_version": finding.get("model_version"),
            "execution_status": finding.get("execution_status"),
            "applicable": finding.get("applicable"),
            "finding": finding.get("finding"),
            "score": finding.get("detector_local_score") if is_applicable(finding) else None,
            "threshold": finding.get("detector_local_threshold") if is_applicable(finding) else None,
            "missing_features": finding.get("missing_features") or [],
            "applicability_reason": finding.get("applicability_reason"),
        },
    )


def evaluate_insufficient_coverage(item: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    event = item.get("event", {})
    no_applicable = not any(is_applicable(finding) for finding in detector_findings(item).values())
    if event.get("coverage_status") == "none" or event.get("evidence_state") == "insufficient_coverage" or no_applicable:
        return {
            "applied": True,
            "status": "insufficient_evidence",
            "reason": "No active detector produced applicable evidence for RCA.",
            "recommended_checks": [
                check(
                    "check.feature_coverage",
                    "Restore missing required features or inspect preprocessing before causal interpretation.",
                    "data_quality",
                )
            ],
        }
    return skipped("coverage is not fully insufficient")


def evaluate_no_evidence(item: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    if detector_ids(item, "positive"):
        return skipped("positive detector evidence exists")
    if not any(is_applicable(finding) for finding in detector_findings(item).values()):
        return skipped("no applicable detector findings")
    return {
        "applied": True,
        "status": "no_hypothesis",
        "reason": "Applicable detectors were negative; no RCA hypothesis is generated.",
        "recommended_checks": [
            check(
                "check.no_evidence_boundary",
                "Do not treat no detector evidence as verified healthy ground truth without independent validation.",
                "validation_boundary",
            )
        ],
    }


def evaluate_contextual_voltage_deviation(item: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    finding = finding_for(item, CONTEXTUAL_BATTERY_DETECTOR_ID)
    top = contextual_top_deviation(item)
    if not is_positive(finding):
        return skipped("contextual battery detector is not a positive applicable finding")
    if not top:
        return skipped("contextual top deviation is unavailable")

    evidence = [
        evidence_item(
            "e.contextual_voltage_deviation",
            "Contextual battery detector reported a voltage deviation.",
            {
                "top_deviation": top,
                "score": finding.get("detector_local_score"),
                "threshold": finding.get("detector_local_threshold"),
                "operating_context": operating_context(item),
            },
        )
    ]
    if iforest_features_include(item, "battery_voltage") and is_positive(finding_for(item, ISOLATION_FOREST_DETECTOR_ID)):
        evidence.append(
            evidence_item(
                "e.iforest_battery_feature_overlap",
                "Isolation Forest also listed battery-voltage features among unusual features.",
                {"most_unusual_features": unusual_features(item)},
            )
        )
    evidence_against = detector_scope_evidence_against(item, ISOLATION_FOREST_DETECTOR_ID)
    return hypothesis_result(
        rule_id="rca.electrical.contextual_voltage_deviation",
        hypothesis_id="h.electrical_supply_variation",
        description=(
            "Electrical supply variation is a plausible explanation for the observed context-conditioned "
            "battery-voltage deviation. This does not identify a component failure."
        ),
        status="supported",
        triggering_evidence=evidence,
        evidence_against=evidence_against,
        recommended_checks=[
            check(
                "check.external_voltage_measurement",
                "Measure battery and charging voltage externally during a repeat capture.",
                "electrical",
            ),
            check(
                "check.electrical_load_state",
                "Record electrical load state and operator actions around the event.",
                "electrical",
            ),
        ],
    )


def evaluate_iforest_multivariate_pattern(item: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    finding = finding_for(item, ISOLATION_FOREST_DETECTOR_ID)
    features = unusual_features(item)
    if not is_positive(finding):
        return skipped("Isolation Forest is not a positive applicable finding")
    if not features:
        return skipped("Isolation Forest unusual-feature evidence is unavailable")
    evidence = [
        evidence_item(
            "e.iforest_positive",
            "Isolation Forest reported a multivariate anomaly.",
            {
                "score": finding.get("detector_local_score"),
                "threshold": finding.get("detector_local_threshold"),
                "most_unusual_features": features,
                "window_health_score": (finding.get("evidence") or {}).get("window_health_score"),
            },
        ),
        evidence_item("e.operating_context", "Representative telemetry context is available.", telemetry_snapshot(item)),
    ]
    evidence_against = []
    contextual = finding_for(item, CONTEXTUAL_BATTERY_DETECTOR_ID)
    if is_negative(contextual):
        evidence_against.append(
            evidence_item(
                "e.contextual_voltage_not_corroborating",
                "Contextual battery detector was negative, so it does not corroborate an electrical-voltage deviation.",
                {
                    "score": contextual.get("detector_local_score"),
                    "threshold": contextual.get("detector_local_threshold"),
                },
            )
        )
    return hypothesis_result(
        rule_id="rca.iforest.multivariate_pattern",
        hypothesis_id="h.multivariate_pattern_outside_reference",
        description=(
            "The representative window appears outside the Isolation Forest reference distribution; "
            "review the listed feature families before assigning a cause."
        ),
        status="possible",
        triggering_evidence=evidence,
        evidence_against=evidence_against,
        recommended_checks=[
            check(
                "check.iforest_feature_review",
                "Inspect the listed unusual features in raw telemetry and compare with operator/session notes.",
                "multivariate_review",
            )
        ],
    )


def evaluate_operating_state_pattern(item: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not is_positive(finding_for(item, ISOLATION_FOREST_DETECTOR_ID)):
        return skipped("Isolation Forest is not positive")
    features = unusual_features(item)
    has_operating_feature = any(feature.startswith("rpm_") or feature.startswith("tps_") for feature in features)
    telemetry = telemetry_snapshot(item)
    has_signal = telemetry.get("rpm_median") is not None and (
        telemetry.get("tps_raw_median") is not None or telemetry.get("tps_voltage_median") is not None
    )
    if not has_operating_feature:
        return skipped("IF unusual features do not include RPM/TPS")
    if not has_signal:
        return skipped("RPM/TPS telemetry is unavailable")
    return hypothesis_result(
        rule_id="rca.operating_state.throttle_related_pattern",
        hypothesis_id="h.operating_state_or_throttle_input_variation",
        description=(
            "Operating-state or throttle-input-related behavior is a possible contributor to the IF anomaly. "
            "This does not assume RPM is determined solely by TPS."
        ),
        status="possible",
        triggering_evidence=[
            evidence_item(
                "e.rpm_tps_if_features",
                "Isolation Forest highlighted RPM/TPS feature families.",
                {"most_unusual_features": features, "telemetry": telemetry},
            )
        ],
        evidence_against=[],
        recommended_checks=[
            check(
                "check.operator_input_context",
                "Review operator input, route/load state and RPM/TPS traces around the event.",
                "operating_context",
            )
        ],
    )


def evaluate_constant_signal_pattern(item: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not is_positive(finding_for(item, ISOLATION_FOREST_DETECTOR_ID)):
        return skipped("Isolation Forest is not positive")
    features = unusual_features(item)
    constant_features = [feature for feature in features if "constant_signal" in feature]
    if not constant_features:
        return skipped("IF unusual features do not include constant-signal indicators")
    return hypothesis_result(
        rule_id="rca.data_quality.constant_signal_pattern",
        hypothesis_id="h.possible_acquisition_or_constant_signal_artifact",
        description=(
            "A constant-signal or acquisition/data-quality pattern may contribute to the detector evidence; "
            "physical behavior and acquisition artifacts must be separated by review."
        ),
        status="possible",
        triggering_evidence=[
            evidence_item(
                "e.constant_signal_features",
                "Isolation Forest unusual-feature list contains constant-signal indicators.",
                {"constant_signal_features": constant_features, "most_unusual_features": features},
            )
        ],
        evidence_against=[],
        recommended_checks=[
            check(
                "check.raw_capture_constant_values",
                "Inspect raw samples for repeated values, sensor saturation, missing frames or intentional stable operation.",
                "data_quality",
            )
        ],
    )


def evaluate_detector_disagreement(item: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    event = item.get("event", {})
    if event.get("evidence_state") != "detector_disagreement" and not (detector_ids(item, "positive") and detector_ids(item, "negative")):
        return skipped("no detector disagreement")
    return {
        "applied": True,
        "status": "preserved_without_consensus",
        "reason": "Detector disagreement was preserved without creating a shared diagnosis.",
        "recommended_checks": [
            check(
                "check.detector_scope_review",
                "Review each detector output in its own score semantics and phenomenon scope.",
                "evidence_review",
            )
        ],
    }


def hypothesis_result(
    *,
    rule_id: str,
    hypothesis_id: str,
    description: str,
    status: HypothesisStatus,
    triggering_evidence: list[dict[str, Any]],
    evidence_against: list[dict[str, Any]],
    recommended_checks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "applied": True,
        "status": status,
        "reason": "rule evidence requirements satisfied",
        "hypothesis": {
            "hypothesis_id": hypothesis_id,
            "description": description,
            "status": status,
            "source_rule_id": rule_id,
            "triggering_evidence": triggering_evidence,
            "evidence_against": evidence_against,
            "recommended_checks": recommended_checks,
            "physical_fault_claim_made": False,
            "quantified_likelihood_provided": False,
        },
        "recommended_checks": recommended_checks,
    }


def skipped(reason: str) -> dict[str, Any]:
    return {"applied": False, "status": "not_applicable", "reason": reason}


def check(check_id: str, description: str, category: str) -> dict[str, Any]:
    return {"check_id": check_id, "category": category, "description": description}


def evidence_item(evidence_id: str, description: str, values: dict[str, Any]) -> dict[str, Any]:
    return {"evidence_id": evidence_id, "description": description, "values": values}


def detector_scope_evidence_against(item: dict[str, Any], detector_id: str) -> list[dict[str, Any]]:
    finding = finding_for(item, detector_id)
    if is_negative(finding):
        return [
            evidence_item(
                f"e.{detector_id}.negative",
                f"{detector_id} did not report an anomaly for the representative window.",
                {
                    "finding": finding.get("finding"),
                    "score": finding.get("detector_local_score"),
                    "threshold": finding.get("detector_local_threshold"),
                },
            )
        ]
    if not is_available(finding):
        return [
            evidence_item(
                f"e.{detector_id}.unavailable",
                f"{detector_id} was unavailable/skipped and cannot corroborate this hypothesis.",
                {
                    "execution_status": finding.get("execution_status"),
                    "applicability_reason": finding.get("applicability_reason"),
                    "missing_features": finding.get("missing_features") or [],
                },
            )
        ]
    return []


def evidence_limitations(item: dict[str, Any], applied_rules: list[dict[str, Any]], hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    limitations: list[dict[str, Any]] = [
        {
            "limitation_id": "limit.no_physical_fault_claims",
            "description": "RCA hypotheses are not physical fault findings and require expert or physical validation.",
        }
    ]
    event = item.get("event", {})
    if event.get("coverage_status") != "full":
        limitations.append(
            {
                "limitation_id": "limit.partial_or_missing_detector_coverage",
                "description": "At least one active detector was unavailable or not applicable for this event.",
                "coverage_status": event.get("coverage_status"),
                "unavailable_detector_ids": detector_ids(item, "unavailable"),
            }
        )
    if not hypotheses:
        limitations.append(
            {
                "limitation_id": "limit.no_supported_causal_hypothesis",
                "description": "Available evidence did not satisfy any causal-hypothesis rule.",
            }
        )
    if detector_ids(item, "positive") and detector_ids(item, "negative"):
        limitations.append(
            {
                "limitation_id": "limit.detector_disagreement",
                "description": "Active detectors disagreed; no score fusion or consensus diagnosis was performed.",
                "positive_detector_ids": detector_ids(item, "positive"),
                "negative_detector_ids": detector_ids(item, "negative"),
            }
        )
    independent_label = item.get("independent_label") or {}
    if independent_label.get("label") in {None, "inconclusive"}:
        limitations.append(
            {
                "limitation_id": "limit.no_independent_ground_truth",
                "description": "No independent ground truth label is available for this event.",
            }
        )
    return limitations


def rca_state(item: dict[str, Any], hypotheses: list[dict[str, Any]]) -> str:
    event = item.get("event", {})
    if event.get("coverage_status") == "none" or event.get("evidence_state") == "insufficient_coverage":
        return "insufficient_evidence"
    if not detector_ids(item, "positive"):
        return "no_anomaly_evidence"
    if hypotheses:
        return "hypotheses_generated"
    return "insufficient_evidence"


def event_identity(item: dict[str, Any]) -> dict[str, Any]:
    event = item.get("event", {})
    return {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "validation_region_type": item.get("validation_region_type"),
        "evidence_state": event.get("evidence_state"),
        "coverage_status": event.get("coverage_status"),
        "start_window_index": event.get("start_window_index"),
        "end_window_index": event.get("end_window_index"),
        "start_time_ms": event.get("start_time_ms"),
        "end_time_ms": event.get("end_time_ms"),
        "duration_ms": event.get("duration_ms"),
        "window_count": event.get("window_count"),
    }


def detector_findings(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return dict((item.get("detector_outputs") or {}).get("by_detector") or {})


def finding_for(item: dict[str, Any], detector_id: str) -> dict[str, Any]:
    return detector_findings(item).get(detector_id, {})


def detector_ids(item: dict[str, Any], key: str) -> list[str]:
    return list((item.get("detector_outputs") or {}).get(f"{key}_detector_ids") or [])


def is_available(finding: dict[str, Any]) -> bool:
    return finding.get("execution_status") == "ok" and finding.get("finding") != "unavailable"


def is_applicable(finding: dict[str, Any]) -> bool:
    return is_available(finding) and finding.get("applicable") is True and finding.get("finding") in {"positive", "negative"}


def is_positive(finding: dict[str, Any]) -> bool:
    return is_applicable(finding) and finding.get("finding") == "positive" and finding.get("is_anomaly") is True


def is_negative(finding: dict[str, Any]) -> bool:
    return is_applicable(finding) and finding.get("finding") == "negative" and finding.get("is_anomaly") is False


def unusual_features(item: dict[str, Any]) -> list[str]:
    finding = finding_for(item, ISOLATION_FOREST_DETECTOR_ID)
    raw = (finding.get("evidence") or {}).get("most_unusual_features")
    if raw is None:
        raw = (item.get("telemetry_evidence") or {}).get("isolation_forest_most_unusual_features")
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(";") if part.strip()]
    if isinstance(raw, list):
        return [str(part) for part in raw if str(part)]
    return []


def iforest_features_include(item: dict[str, Any], fragment: str) -> bool:
    return any(fragment in feature for feature in unusual_features(item))


def contextual_top_deviation(item: dict[str, Any]) -> dict[str, Any] | None:
    finding = finding_for(item, CONTEXTUAL_BATTERY_DETECTOR_ID)
    evidence = finding.get("evidence") or {}
    nested = evidence.get("contextual_evidence")
    if isinstance(nested, dict) and isinstance(nested.get("top_deviation"), dict):
        return nested["top_deviation"]
    top = (item.get("telemetry_evidence") or {}).get("contextual_top_deviation")
    return top if isinstance(top, dict) else None


def operating_context(item: dict[str, Any]) -> str | None:
    finding = finding_for(item, CONTEXTUAL_BATTERY_DETECTOR_ID)
    evidence = finding.get("evidence") or {}
    nested = evidence.get("contextual_evidence")
    if isinstance(nested, dict) and nested.get("operating_context"):
        return str(nested["operating_context"])
    if evidence.get("operating_context"):
        return str(evidence["operating_context"])
    context = (item.get("telemetry_evidence") or {}).get("operating_context")
    return str(context) if context else None


def telemetry_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    telemetry = (item.get("telemetry_evidence") or {}).get("telemetry")
    return dict(telemetry) if isinstance(telemetry, dict) else {}


def dedupe_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for item in items:
        key = f"{item.get('evidence_id')}:{item.get('description')}"
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def dedupe_checks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for item in items:
        key = str(item.get("check_id"))
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def summarize_rca_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    state_counts = Counter(result["rca_state"] for result in results)
    rule_counts: Counter[str] = Counter()
    hypothesis_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    weak_rule_counts: Counter[str] = Counter()
    disagreement_preserved = 0
    untraceable = 0
    for result in results:
        for rule in result.get("applied_rules", []):
            if rule.get("applied"):
                rule_counts[rule["rule_id"]] += 1
                if rule.get("rule_id") == "rca.detectors.disagreement_preservation":
                    disagreement_preserved += 1
        for hypothesis in result.get("hypotheses", []):
            hypothesis_counts[hypothesis["hypothesis_id"]] += 1
            status_counts[hypothesis["status"]] += 1
            if hypothesis.get("status") == "possible":
                weak_rule_counts[hypothesis["source_rule_id"]] += 1
            if not hypothesis.get("triggering_evidence"):
                untraceable += 1
    return {
        "result_count": len(results),
        "rca_state_counts": dict(sorted(state_counts.items())),
        "hypothesis_count": sum(len(result.get("hypotheses", [])) for result in results),
        "results_with_hypotheses": sum(1 for result in results if result.get("hypotheses")),
        "results_insufficient_evidence": state_counts.get("insufficient_evidence", 0),
        "rule_fire_counts": dict(sorted(rule_counts.items())),
        "hypothesis_counts": dict(sorted(hypothesis_counts.items())),
        "hypothesis_status_counts": dict(sorted(status_counts.items())),
        "weak_or_ambiguous_rule_fire_counts": dict(sorted(weak_rule_counts.items())),
        "detector_disagreement_preserved_count": disagreement_preserved,
        "untraceable_hypothesis_count": untraceable,
    }
