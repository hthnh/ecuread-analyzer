from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.evaluation.model_harness_evaluation import EvaluationPaths, default_paths, write_json


H8_SCHEMA_VERSION = "historical-evidence-v1"
H8_POLICY_VERSION = "historical-evidence-compatibility-v1"
H8_RECURRENCE_VERSION = "historical-recurrence-v1"
H8_TREND_VERSION = "historical-trend-v1"
H8_RCA_EXTENSION_VERSION = "root-cause-analysis-v2-history-extension-v1"
RCA_V2_RULE_CATALOG_VERSION = "evidence-grounded-rca-v2"

DETECTOR_IFOREST = "isolation_forest"
DETECTOR_CONTEXTUAL_VOLTAGE = "contextual_battery_voltage"

FAMILY_IFOREST = "isolation_forest_positive"
FAMILY_CONTEXTUAL_VOLTAGE = "contextual_voltage_deviation"
FAMILY_DETECTOR_DISAGREEMENT = "detector_disagreement"
FAMILY_CONSTANT_SIGNAL = "constant_signal_feature"
FAMILY_RPM_TPS = "rpm_tps_pattern"
FAMILY_INSUFFICIENT_COVERAGE = "insufficient_coverage"
FAMILY_NO_ANOMALY_EVIDENCE = "no_anomaly_evidence"

CHECK_FAMILY_MAP = {
    "check.v2.iforest_feature_review": FAMILY_IFOREST,
    "check.v2.voltage_context_compare": FAMILY_CONTEXTUAL_VOLTAGE,
    "check.v2.external_supply_measurement": FAMILY_CONTEXTUAL_VOLTAGE,
    "check.v2.rpm_tps_reproduction": FAMILY_RPM_TPS,
    "check.v2.rpm_tps_repeatability": FAMILY_RPM_TPS,
    "check.v2.raw_frame_integrity": FAMILY_CONSTANT_SIGNAL,
    "check.v2.known_stimulus_response": FAMILY_CONSTANT_SIGNAL,
    "check.v2.feature_coverage": FAMILY_INSUFFICIENT_COVERAGE,
    "check.v2.detector_scope_review": FAMILY_DETECTOR_DISAGREEMENT,
    "check.v2.no_evidence_boundary": FAMILY_NO_ANOMALY_EVIDENCE,
}


def default_h8_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h8")


def run_all(
    paths: EvaluationPaths,
    *,
    h2_dir: Path | None = None,
    h5_dir: Path | None = None,
    h73_dir: Path | None = None,
) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h2_dir = (h2_dir or paths.repo_root / "data" / "evaluation" / "h2").resolve()
    resolved_h5_dir = (h5_dir or paths.repo_root / "data" / "evaluation" / "h5").resolve()
    resolved_h73_dir = (h73_dir or paths.repo_root / "data" / "evaluation" / "h7_3").resolve()

    h2_manifest = read_json(resolved_h2_dir / "evaluation_manifest.json")
    h5_review = read_json(resolved_h5_dir / "aggregate_event_review.json")
    h73_results = read_json(resolved_h73_dir / "rca_v2_results.json")

    contract = historical_evidence_contract()
    index = build_historical_observation_index(h2_manifest, h5_review, h73_results)
    governance = build_baseline_governance(h2_manifest, index)
    comparisons = build_historical_comparisons(h2_manifest, index, h73_results)
    rca_with_history = attach_historical_evidence_to_rca_v2_results(h73_results, comparisons)
    evaluation = build_offline_evaluation(comparisons, rca_with_history)
    counterfactual = build_counterfactual_evaluation(h73_results, rca_with_history)
    decision = build_decision_report(contract, index, governance, comparisons, evaluation, counterfactual)

    outputs = {
        "contract": paths.output_dir / "historical_evidence_contract.json",
        "index": paths.output_dir / "historical_observation_index.json",
        "baseline_governance": paths.output_dir / "baseline_governance.json",
        "comparisons": paths.output_dir / "historical_comparisons.json",
        "rca_v2_with_history": paths.output_dir / "rca_v2_with_history_results.json",
        "evaluation": paths.output_dir / "offline_evaluation.json",
        "counterfactual": paths.output_dir / "counterfactual_evaluation.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["contract"], contract)
    write_json(outputs["index"], index)
    write_json(outputs["baseline_governance"], governance)
    write_json(outputs["comparisons"], comparisons)
    write_json(outputs["rca_v2_with_history"], rca_with_history)
    write_json(outputs["evaluation"], evaluation)
    write_json(outputs["counterfactual"], counterfactual)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], evaluation, counterfactual, decision)
    return outputs


def historical_evidence_contract() -> dict[str, Any]:
    return {
        "schema_version": H8_SCHEMA_VERSION,
        "policy_version": H8_POLICY_VERSION,
        "rca_v2_history_extension_version": H8_RCA_EXTENSION_VERSION,
        "policy": {
            "llm_used": False,
            "agents_used": False,
            "vector_database_used": False,
            "embedding_similarity_used": False,
            "production_behavior_changed": False,
            "drive_safe_behavior_changed": False,
            "new_detector_added": False,
            "score_fusion_used": False,
            "history_can_create_root_cause": False,
            "unsupported_h7_v1_hypotheses_indexed_as_facts": False,
            "future_sessions_allowed_in_history": False,
            "diagnostic_confidence_percentages_allowed": False,
        },
        "historical_observation_fields": [
            "vehicle_identity",
            "session_id",
            "capture_timestamp",
            "detector_versions",
            "decoder",
            "operating_context",
            "observation_family",
            "observation_type",
            "signals",
            "features",
            "event",
            "event_magnitude",
            "source_provenance",
        ],
        "comparison_output_fields": [
            "comparable_session_count",
            "occurrence_count",
            "occurrence_rate",
            "first_observed",
            "most_recent_prior_occurrence",
            "recurrence_category",
            "persistence",
            "trend",
            "comparison_limitations",
        ],
        "compatibility_requirements": {
            "temporal_order": "Only sessions captured before the current session may be considered.",
            "independence": "The current session, same sample hash, and same source path are excluded.",
            "vehicle_source": "Vehicle identity must match when available.",
            "decoder": "Decoder version key must match when available.",
            "detector_rule_version": "Detector/rule versions must match when both records expose a comparable version.",
            "signals": "Current observation required signals must be available in the historical session.",
            "operating_context": "Operating contexts must match when both current and historical records expose one; otherwise the comparison is limited.",
            "recording_quality": "Historical session must be evaluation-ready with at least one window.",
        },
        "recurrence_categories": recurrence_category_definitions(),
        "trend_categories": trend_category_definitions(),
        "check_priority_categories": {
            "priority": "History shows recurrent or persistent comparable evidence for the check family.",
            "relevant": "The check remains tied to current evidence, but history does not justify prioritization.",
            "deferred": "Another evidence-tied check is prioritized, so this check can be reviewed later without being removed.",
            "insufficient_evidence": "Historical compatibility is too weak to prioritize or defer the check.",
        },
        "baseline_governance": {
            "historical_evidence_corpus": "All suitable prior structured observations, never assumed normal.",
            "nominal_reference_baseline": "Only explicit reference-candidate sessions that have not produced indexed anomaly observations.",
            "trusted_normal_ground_truth": "Not created by H8.",
        },
    }


def recurrence_category_definitions() -> dict[str, str]:
    return {
        "historical_comparison_unavailable": "No compatible prior sessions or required comparison metadata are available.",
        "first_observed": "Compatible prior sessions exist, but no materially similar prior observation was found.",
        "rare_recurrence": "A materially similar observation occurred in exactly one prior comparable session.",
        "recurrent": "A materially similar observation occurred in at least two prior comparable sessions.",
        "persistent_across_sessions": (
            "A materially similar observation occurred in at least three prior comparable sessions and in at least half of "
            "all comparable prior sessions. This is a conservative recurrence category, not a degradation claim."
        ),
    }


def trend_category_definitions() -> dict[str, str]:
    return {
        "insufficient_historical_data": "Fewer than three compatible numeric occurrence points are available.",
        "no_observable_trend": "Numeric occurrence points are not monotonically increasing or decreasing.",
        "possible_increasing_trend": "At least three compatible numeric points increase monotonically in detector-local severity semantics.",
        "possible_decreasing_trend": "At least three compatible numeric points decrease monotonically in detector-local severity semantics.",
    }


def build_historical_observation_index(
    h2_manifest: dict[str, Any],
    h5_review: dict[str, Any],
    h73_results: dict[str, Any],
) -> dict[str, Any]:
    session_by_id = sessions_by_id(h2_manifest)
    records: list[dict[str, Any]] = []
    sequence = 1
    for item in h5_review.get("items", []):
        new_records = h5_item_records(item, session_by_id, sequence)
        records.extend(new_records)
        sequence += len(new_records)
    for result in h73_results.get("results", []):
        new_records = rca_v2_result_records(result, session_by_id, sequence, source_artifact="h7_3_rca_v2_results")
        records.extend(new_records)
        sequence += len(new_records)

    family_counts = Counter(record["observation_family"] for record in records)
    source_counts = Counter(record["source_artifact"] for record in records)
    session_counts = Counter(record["session_id"] for record in records)
    return {
        "schema_version": "historical-observation-index-v1",
        "policy": {
            "historical_evidence_is_not_nominal_baseline": True,
            "h7_v1_hypotheses_indexed": False,
            "historical_detector_results_recomputed": False,
            "source_versions_preserved": True,
            "production_behavior_changed": False,
        },
        "sources": {
            "h2_evaluation_manifest": "data/evaluation/h2/evaluation_manifest.json",
            "h5_aggregate_event_review": "data/evaluation/h5/aggregate_event_review.json",
            "h7_3_rca_v2_results": "data/evaluation/h7_3/rca_v2_results.json",
        },
        "summary": {
            "record_count": len(records),
            "session_count": len(session_counts),
            "observation_family_counts": dict(sorted(family_counts.items())),
            "source_artifact_counts": dict(sorted(source_counts.items())),
        },
        "records": records,
    }


def h5_item_records(item: dict[str, Any], session_by_id: dict[str, dict[str, Any]], sequence_start: int) -> list[dict[str, Any]]:
    event = item.get("event") or {}
    representative = event.get("representative_window") or {}
    detector_findings = representative.get("detector_findings") or []
    session_id = event.get("session_id") or (item.get("provenance") or {}).get("session_id")
    provenance = item.get("provenance") or {}
    session = session_by_id.get(session_id, {})
    records: list[dict[str, Any]] = []
    sequence = sequence_start

    for finding in detector_findings:
        if finding.get("execution_status") != "ok" or finding.get("finding") != "positive":
            continue
        detector_id = finding.get("detector_id")
        features = parse_feature_list((finding.get("evidence") or {}).get("most_unusual_features"))
        if detector_id == DETECTOR_IFOREST:
            records.append(
                make_historical_record(
                    sequence,
                    source_artifact="h5_aggregate_event_review",
                    source_record_id=item.get("review_item_id"),
                    observation_family=FAMILY_IFOREST,
                    observation_type="detector_positive",
                    statement="Isolation Forest positive event from aggregate evidence.",
                    session=session,
                    fallback_provenance=provenance,
                    event=event,
                    detector_finding=finding,
                    features=features,
                    operating_context=representative.get("operating_context"),
                    source_section="detector_event",
                )
            )
            sequence += 1
            if any("constant_signal" in feature for feature in features):
                records.append(
                    make_historical_record(
                        sequence,
                        source_artifact="h5_aggregate_event_review",
                        source_record_id=item.get("review_item_id"),
                        observation_family=FAMILY_CONSTANT_SIGNAL,
                        observation_type="derived_feature_observation",
                        statement="Constant-signal feature appeared in IF event evidence.",
                        session=session,
                        fallback_provenance=provenance,
                        event=event,
                        detector_finding=finding,
                        features=[feature for feature in features if "constant_signal" in feature],
                        operating_context=representative.get("operating_context"),
                        source_section="derived_detector_feature",
                    )
                )
                sequence += 1
            if rpm_tps_features(features):
                records.append(
                    make_historical_record(
                        sequence,
                        source_artifact="h5_aggregate_event_review",
                        source_record_id=item.get("review_item_id"),
                        observation_family=FAMILY_RPM_TPS,
                        observation_type="derived_feature_observation",
                        statement="RPM/TPS feature family appeared in IF event evidence.",
                        session=session,
                        fallback_provenance=provenance,
                        event=event,
                        detector_finding=finding,
                        features=[feature for feature in features if is_rpm_tps_feature(feature)],
                        operating_context=representative.get("operating_context"),
                        source_section="derived_detector_feature",
                    )
                )
                sequence += 1
        elif detector_id == DETECTOR_CONTEXTUAL_VOLTAGE:
            records.append(
                make_historical_record(
                    sequence,
                    source_artifact="h5_aggregate_event_review",
                    source_record_id=item.get("review_item_id"),
                    observation_family=FAMILY_CONTEXTUAL_VOLTAGE,
                    observation_type="contextual_voltage_detector_positive",
                    statement="Contextual battery-voltage detector positive event from aggregate evidence.",
                    session=session,
                    fallback_provenance=provenance,
                    event=event,
                    detector_finding=finding,
                    features=features or ["battery_voltage"],
                    operating_context=contextual_operating_context(finding) or representative.get("operating_context"),
                    source_section="detector_event",
                )
            )
            sequence += 1

    if event.get("evidence_state") == "detector_disagreement":
        records.append(
            make_historical_record(
                sequence,
                source_artifact="h5_aggregate_event_review",
                source_record_id=item.get("review_item_id"),
                observation_family=FAMILY_DETECTOR_DISAGREEMENT,
                observation_type="aggregate_evidence_relationship",
                statement="Detector disagreement event from aggregate evidence.",
                session=session,
                fallback_provenance=provenance,
                event=event,
                detector_finding=None,
                features=[],
                operating_context=representative.get("operating_context"),
                source_section="aggregate_event",
            )
        )
        sequence += 1
    if event.get("coverage_status") == "none" or event.get("evidence_state") == "insufficient_coverage":
        records.append(
            make_historical_record(
                sequence,
                source_artifact="h5_aggregate_event_review",
                source_record_id=item.get("review_item_id"),
                observation_family=FAMILY_INSUFFICIENT_COVERAGE,
                observation_type="coverage_limitation",
                statement="Insufficient detector coverage event.",
                session=session,
                fallback_provenance=provenance,
                event=event,
                detector_finding=None,
                features=[],
                operating_context=representative.get("operating_context"),
                source_section="aggregate_event",
            )
        )
        sequence += 1
    return records


def rca_v2_result_records(
    result: dict[str, Any],
    session_by_id: dict[str, dict[str, Any]],
    sequence_start: int,
    *,
    source_artifact: str,
) -> list[dict[str, Any]]:
    session_id = result.get("session_id")
    session = session_by_id.get(session_id, {})
    provenance = (result.get("provenance") or {}).get("source_case_provenance") or {}
    event = result.get("source_event") or {}
    records: list[dict[str, Any]] = []
    sequence = sequence_start

    for observation in result.get("observations", []):
        family = family_for_rca_observation(observation)
        if family is None:
            continue
        records.append(
            make_historical_record(
                sequence,
                source_artifact=source_artifact,
                source_record_id=result.get("source_case_id"),
                observation_family=family,
                observation_type=observation.get("observation_type"),
                statement=observation.get("statement"),
                session=session,
                fallback_provenance=provenance,
                event=event,
                detector_finding=detector_finding_from_rca_evidence(observation),
                features=features_from_rca_item(observation),
                operating_context=operating_context_from_rca_item(observation),
                source_section="rca_v2_observation",
                rca_item=observation,
                source_case_id=result.get("source_case_id"),
            )
        )
        sequence += 1

    for symptom in result.get("symptoms", []):
        family = family_for_rca_symptom(symptom)
        if family is None:
            continue
        records.append(
            make_historical_record(
                sequence,
                source_artifact=source_artifact,
                source_record_id=result.get("source_case_id"),
                observation_family=family,
                observation_type=symptom.get("symptom_type"),
                statement=symptom.get("statement"),
                session=session,
                fallback_provenance=provenance,
                event=event,
                detector_finding=detector_finding_from_rca_evidence(symptom),
                features=features_from_rca_item(symptom),
                operating_context=operating_context_from_rca_item(symptom),
                source_section="rca_v2_symptom",
                rca_item=symptom,
                source_case_id=result.get("source_case_id"),
            )
        )
        sequence += 1

    for limitation in result.get("evidence_limitations", []):
        family = family_for_rca_limitation(limitation)
        if family is None:
            continue
        records.append(
            make_historical_record(
                sequence,
                source_artifact=source_artifact,
                source_record_id=result.get("source_case_id"),
                observation_family=family,
                observation_type="evidence_limitation",
                statement=limitation.get("statement"),
                session=session,
                fallback_provenance=provenance,
                event=event,
                detector_finding=None,
                features=[],
                operating_context=None,
                source_section="rca_v2_limitation",
                rca_item=limitation,
                source_case_id=result.get("source_case_id"),
            )
        )
        sequence += 1
    return records


def make_historical_record(
    sequence: int,
    *,
    source_artifact: str,
    source_record_id: str | None,
    observation_family: str,
    observation_type: str | None,
    statement: str | None,
    session: dict[str, Any],
    fallback_provenance: dict[str, Any],
    event: dict[str, Any],
    detector_finding: dict[str, Any] | None,
    features: list[str],
    operating_context: str | None,
    source_section: str,
    rca_item: dict[str, Any] | None = None,
    source_case_id: str | None = None,
) -> dict[str, Any]:
    session_id = session.get("session_id") or fallback_provenance.get("session_id") or event.get("session_id")
    signal_names = sorted({signal for feature in features for signal in signals_for_feature(feature)})
    if observation_family == FAMILY_CONTEXTUAL_VOLTAGE:
        signal_names = sorted(set(signal_names) | {"battery_voltage", "rpm", "tps_raw", "tps_voltage"})
    elif observation_family == FAMILY_RPM_TPS:
        signal_names = sorted(set(signal_names) | {"rpm", "tps_raw", "tps_voltage"})
    elif observation_family == FAMILY_CONSTANT_SIGNAL and not signal_names:
        signal_names = sorted({signal for feature in features for signal in signals_for_feature(feature)})
    detector_versions = detector_versions_for_record(event, detector_finding, rca_item)
    decoder = session.get("decoder") or fallback_provenance.get("decoder") or {}
    temporal_order_key = session.get("temporal_order_key") or session_id
    return {
        "record_id": f"h8-historical-record-{sequence:05d}",
        "schema_version": "historical-observation-record-v1",
        "source_artifact": source_artifact,
        "source_section": source_section,
        "source_record_id": source_record_id,
        "source_case_id": source_case_id,
        "vehicle_identity": {
            "vehicle_id": (session.get("capture_provenance") or fallback_provenance.get("capture_provenance") or {}).get("vehicle_id"),
            "device_id": (session.get("capture_provenance") or fallback_provenance.get("capture_provenance") or {}).get("device_id"),
            "ecu_profile_id": (session.get("capture_provenance") or fallback_provenance.get("capture_provenance") or {}).get("ecu_profile_id"),
            "source_type": (session.get("capture_provenance") or fallback_provenance.get("capture_provenance") or {}).get("source_type"),
        },
        "session_id": session_id,
        "capture_timestamp": capture_timestamp(temporal_order_key, session_id),
        "temporal_order_key": temporal_order_key,
        "detector_versions": detector_versions,
        "decoder": {
            "decoder_id": decoder.get("decoder_id"),
            "decoder_version": decoder.get("decoder_version"),
            "decoder_version_key": decoder.get("decoder_version_key"),
        },
        "telemetry_schema_version": session.get("telemetry_schema_version") or fallback_provenance.get("telemetry_schema_version"),
        "operating_context": operating_context,
        "observation_family": observation_family,
        "observation_type": observation_type,
        "statement": statement,
        "signals": signal_names,
        "features": sorted(set(features)),
        "event": {
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "evidence_state": event.get("evidence_state"),
            "coverage_status": event.get("coverage_status"),
            "start_time_ms": event.get("start_time_ms"),
            "end_time_ms": event.get("end_time_ms"),
            "duration_ms": event.get("duration_ms"),
            "start_window_index": event.get("start_window_index"),
            "end_window_index": event.get("end_window_index"),
            "window_count": event.get("window_count"),
        },
        "event_magnitude": event_magnitude(detector_finding, rca_item, event),
        "source_provenance": {
            "path": session.get("path") or fallback_provenance.get("path"),
            "artifacts": session.get("artifacts") or fallback_provenance.get("artifacts"),
            "training_evaluation_split": session.get("training_evaluation_split") or fallback_provenance.get("training_evaluation_split"),
            "rca_rule_version": (rca_item.get("provenance") or {}).get("rule_version") if rca_item else None,
            "source_rule_id": (rca_item.get("provenance") or {}).get("source_rule_id") if rca_item else None,
        },
    }


def build_baseline_governance(h2_manifest: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    anomaly_sessions = {
        record["session_id"]
        for record in index.get("records", [])
        if record.get("observation_family") not in {FAMILY_NO_ANOMALY_EVIDENCE, FAMILY_INSUFFICIENT_COVERAGE}
    }
    reference_candidates: list[dict[str, Any]] = []
    excluded_candidates: list[dict[str, Any]] = []
    for session in h2_manifest.get("sessions", []):
        split = session.get("training_evaluation_split") or {}
        is_explicit_candidate = split.get("split") in {"normal_train", "normal_holdout"} and split.get("label") == "normal_ride"
        if not is_explicit_candidate:
            continue
        payload = {
            "session_id": session.get("session_id"),
            "split": split.get("split"),
            "label": split.get("label"),
            "label_source": split.get("label_source"),
            "vehicle_identity": session.get("capture_provenance"),
            "decoder": session.get("decoder"),
            "artifacts": session.get("artifacts"),
            "not_verified_healthy_ground_truth": True,
        }
        if session.get("session_id") in anomaly_sessions:
            payload["exclusion_reason"] = "indexed_anomaly_or_symptom_observation_present"
            excluded_candidates.append(payload)
        else:
            payload["baseline_role"] = "nominal_reference_candidate"
            reference_candidates.append(payload)
    return {
        "schema_version": "historical-baseline-governance-v1",
        "policy": {
            "historical_evidence_corpus_is_nominal_baseline": False,
            "automatic_baseline_absorption": False,
            "normal_train_means_verified_healthy": False,
            "sessions_with_detected_anomalies_can_define_nominal_baseline": False,
        },
        "historical_evidence_corpus": {
            "record_count": index.get("summary", {}).get("record_count", 0),
            "session_count": index.get("summary", {}).get("session_count", 0),
            "role": "prior structured evidence only",
        },
        "nominal_reference_baseline": {
            "reference_candidate_count": len(reference_candidates),
            "excluded_candidate_count": len(excluded_candidates),
            "trusted_normal_ground_truth_count": 0,
            "sessions": reference_candidates,
            "excluded_sessions": excluded_candidates,
        },
    }


def build_historical_comparisons(
    h2_manifest: dict[str, Any],
    index: dict[str, Any],
    h73_results: dict[str, Any],
) -> dict[str, Any]:
    session_by_id = sessions_by_id(h2_manifest)
    current_records = []
    sequence = 1
    for result in h73_results.get("results", []):
        records = rca_v2_result_records(result, session_by_id, sequence, source_artifact="h7_3_current_rca_v2")
        current_records.extend(records)
        sequence += len(records)

    comparisons: list[dict[str, Any]] = []
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    records = index.get("records", [])
    sessions = h2_manifest.get("sessions", [])
    for comparison_index, current in enumerate(current_records, start=1):
        comparison = compare_current_record_to_history(comparison_index, current, records, sessions)
        comparisons.append(comparison)
        if current.get("source_case_id"):
            by_case[current["source_case_id"]].append(comparison)

    category_counts = Counter(comparison["recurrence"]["category"] for comparison in comparisons)
    trend_counts = Counter(comparison["trend"]["trend_status"] for comparison in comparisons)
    return {
        "schema_version": "historical-comparisons-v1",
        "policy": {
            "future_data_leakage_prevented": all(
                not occurrence.get("temporal_leakage")
                for comparison in comparisons
                for occurrence in comparison["historical_occurrences"]["occurrences"]
            ),
            "same_session_excluded": True,
            "same_capture_hash_excluded": True,
            "detector_versions_preserved": True,
        },
        "summary": {
            "current_observation_count": len(current_records),
            "comparison_count": len(comparisons),
            "recurrence_category_counts": dict(sorted(category_counts.items())),
            "trend_status_counts": dict(sorted(trend_counts.items())),
            "cases_with_comparable_history": len(
                {
                    comparison["current"]["source_case_id"]
                    for comparison in comparisons
                    if comparison["compatibility"]["comparable_session_count"] > 0 and comparison["current"].get("source_case_id")
                }
            ),
        },
        "comparisons": comparisons,
        "comparison_ids_by_case": {
            case_id: [comparison["comparison_id"] for comparison in comparisons_for_case]
            for case_id, comparisons_for_case in sorted(by_case.items())
        },
    }


def compare_current_record_to_history(
    comparison_index: int,
    current: dict[str, Any],
    historical_records: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    comparable_sessions = []
    excluded_session_reasons: Counter[str] = Counter()
    for session in sessions:
        compatible, reasons = session_is_comparable(current, session)
        if compatible:
            comparable_sessions.append(session)
        else:
            for reason in reasons:
                excluded_session_reasons[reason] += 1

    comparable_session_ids = {session.get("session_id") for session in comparable_sessions}
    matched_records = []
    comparison_limitations: list[str] = []
    for record in historical_records:
        if record.get("session_id") not in comparable_session_ids:
            continue
        if record.get("observation_family") != current.get("observation_family"):
            continue
        compatible_record, reasons = record_is_comparable(current, record)
        if compatible_record:
            matched_records.append(record)
        else:
            comparison_limitations.extend(reasons)

    deduped_records = dedupe_occurrences(matched_records)
    occurrence_sessions = sorted({record["session_id"] for record in deduped_records if record.get("session_id")})
    occurrence_rate = (
        round(len(occurrence_sessions) / len(comparable_sessions), 6) if comparable_sessions else None
    )
    category = recurrence_category(len(comparable_sessions), len(occurrence_sessions))
    persistence = persistence_category(len(comparable_sessions), len(occurrence_sessions))
    trend = build_trend_evidence(current, deduped_records)
    current_timestamp = current.get("capture_timestamp")
    first_prior = min(deduped_records, key=record_sort_key) if deduped_records else None
    most_recent_prior = max(deduped_records, key=record_sort_key) if deduped_records else None
    if not comparable_sessions:
        comparison_limitations.append("no compatible prior sessions")
    if any(record.get("operating_context") is None for record in deduped_records) and current.get("operating_context"):
        comparison_limitations.append("some historical occurrences lack operating-context detail")
    comparison_limitations = sorted(set(comparison_limitations))

    occurrences = [occurrence_summary(record, current) for record in deduped_records]
    return {
        "comparison_id": f"h8-comparison-{comparison_index:05d}",
        "schema_version": "historical-comparison-v1",
        "current": record_summary(current),
        "compatibility": {
            "policy_version": H8_POLICY_VERSION,
            "comparable_session_count": len(comparable_sessions),
            "comparable_session_ids": sorted(comparable_session_ids),
            "excluded_session_reason_counts": dict(sorted(excluded_session_reasons.items())),
            "required_signals": current.get("signals", []),
            "same_vehicle_required": current.get("vehicle_identity", {}).get("vehicle_id") is not None,
            "same_decoder_required": current.get("decoder", {}).get("decoder_version_key") is not None,
        },
        "historical_occurrences": {
            "occurrence_count": len(deduped_records),
            "occurrence_session_count": len(occurrence_sessions),
            "occurrence_session_ids": occurrence_sessions,
            "occurrence_rate": occurrence_rate,
            "first_observed": record_observed_at(first_prior) if first_prior else current_timestamp,
            "most_recent_prior_occurrence": record_observed_at(most_recent_prior) if most_recent_prior else None,
            "occurrences": occurrences,
        },
        "recurrence": {
            "schema_version": H8_RECURRENCE_VERSION,
            "category": category,
            "persistence": persistence,
            "category_semantics": recurrence_category_definitions()[category],
        },
        "trend": trend,
        "comparison_limitations": comparison_limitations,
    }


def attach_historical_evidence_to_rca_v2_results(
    h73_results: dict[str, Any],
    comparisons: dict[str, Any],
) -> dict[str, Any]:
    comparisons_by_id = {comparison["comparison_id"]: comparison for comparison in comparisons.get("comparisons", [])}
    ids_by_case = comparisons.get("comparison_ids_by_case", {})
    results_with_history = []
    for result in h73_results.get("results", []):
        enriched = copy.deepcopy(result)
        case_ids = ids_by_case.get(result.get("source_case_id"), [])
        case_comparisons = [comparisons_by_id[comparison_id] for comparison_id in case_ids if comparison_id in comparisons_by_id]
        check_priorities = prioritize_checks(enriched, case_comparisons)
        enriched["schema_version"] = "root-cause-analysis-v2-result-with-history-v1"
        enriched["historical_evidence"] = {
            "schema_version": H8_RCA_EXTENSION_VERSION,
            "status": "available" if case_comparisons else "unavailable",
            "can_create_root_cause": False,
            "history_can_create_root_cause": False,
            "comparison_ids": case_ids,
            "summary": summarize_case_history(case_comparisons),
            "comparisons": case_comparisons,
        }
        enriched["recommended_check_priorities"] = check_priorities
        results_with_history.append(enriched)

    priority_counts = Counter(
        priority["priority"]
        for result in results_with_history
        for priority in result.get("recommended_check_priorities", [])
    )
    return {
        "schema_version": "root-cause-analysis-v2-with-history-results-v1",
        "policy": {
            "production_behavior_changed": False,
            "drive_safe_behavior_changed": False,
            "history_created_possible_causes": False,
            "possible_causes_preserved_from_h7_3": True,
            "recommended_checks_removed": False,
        },
        "summary": {
            "result_count": len(results_with_history),
            "results_with_history": sum(1 for result in results_with_history if result["historical_evidence"]["status"] == "available"),
            "possible_cause_count": sum(len(result.get("possible_causes", [])) for result in results_with_history),
            "recommended_check_count": sum(len(result.get("recommended_checks", [])) for result in results_with_history),
            "check_priority_counts": dict(sorted(priority_counts.items())),
        },
        "results": results_with_history,
    }


def prioritize_checks(result: dict[str, Any], comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for comparison in comparisons:
        comparisons_by_family[comparison["current"]["observation_family"]].append(comparison)

    priorities = []
    provisional = []
    for check in result.get("recommended_checks", []):
        check_id = check.get("check_id")
        family = CHECK_FAMILY_MAP.get(check_id)
        matching = comparisons_by_family.get(family, [])
        priority, reason = priority_for_family(matching)
        provisional.append(
            {
                "check_id": check_id,
                "family": family,
                "priority": priority,
                "reason": reason,
                "supporting_comparison_ids": [comparison["comparison_id"] for comparison in matching],
                "deterministic": True,
            }
        )

    has_priority = any(item["priority"] == "priority" for item in provisional)
    for item in provisional:
        if has_priority and item["priority"] == "relevant" and item["family"] == FAMILY_IFOREST:
            item = dict(item)
            item["priority"] = "deferred"
            item["reason"] = "Specific recurrent historical evidence prioritized another check family; generic IF feature review is deferred, not removed."
        priorities.append(item)
    return priorities


def priority_for_family(comparisons: list[dict[str, Any]]) -> tuple[str, str]:
    if not comparisons:
        return "insufficient_evidence", "No historical comparison for this check family."
    categories = {comparison["recurrence"]["category"] for comparison in comparisons}
    if "persistent_across_sessions" in categories or "recurrent" in categories:
        return "priority", "Comparable history shows recurrent or persistent evidence."
    if categories == {"historical_comparison_unavailable"}:
        return "insufficient_evidence", "Compatible historical evidence is unavailable."
    return "relevant", "Check remains evidence-tied, but recurrence is not strong enough for priority."


def build_offline_evaluation(comparisons: dict[str, Any], rca_with_history: dict[str, Any]) -> dict[str, Any]:
    comparison_list = comparisons.get("comparisons", [])
    results = rca_with_history.get("results", [])
    usable_session_ids = {
        comparison["current"]["session_id"]
        for comparison in comparison_list
        if comparison["compatibility"]["comparable_session_count"] > 0 and comparison["current"].get("source_case_id")
    }
    usable_case_ids = {
        comparison["current"]["source_case_id"]
        for comparison in comparison_list
        if comparison["compatibility"]["comparable_session_count"] > 0 and comparison["current"].get("source_case_id")
    }
    all_case_ids = {result.get("source_case_id") for result in results}
    all_session_ids = {result.get("session_id") for result in results}
    recurrence_counts = Counter(comparison["recurrence"]["category"] for comparison in comparison_list)
    trend_counts = Counter(comparison["trend"]["trend_status"] for comparison in comparison_list)
    priority_counts = Counter(
        priority["priority"]
        for result in results
        for priority in result.get("recommended_check_priorities", [])
    )
    changed_cases = []
    no_value_cases = []
    for result in results:
        summary = result.get("historical_evidence", {}).get("summary", {})
        priorities = result.get("recommended_check_priorities", [])
        priority_or_deferred = any(item["priority"] in {"priority", "deferred"} for item in priorities)
        added_info = summary.get("prior_occurrence_count", 0) > 0 or priority_or_deferred
        if added_info:
            changed_cases.append(result.get("source_case_id"))
        else:
            no_value_cases.append(result.get("source_case_id"))
    original_check_count = sum(len(result.get("recommended_checks", [])) for result in results)
    prioritized_check_count = sum(
        1
        for result in results
        for priority in result.get("recommended_check_priorities", [])
        if priority["priority"] == "priority"
    )
    deferred_check_count = sum(
        1
        for result in results
        for priority in result.get("recommended_check_priorities", [])
        if priority["priority"] == "deferred"
    )
    measurable_trends = sum(
        1
        for comparison in comparison_list
        if comparison["trend"]["trend_status"] in {"possible_increasing_trend", "possible_decreasing_trend", "no_observable_trend"}
    )
    return {
        "schema_version": "historical-evidence-offline-evaluation-v1",
        "summary": {
            "case_count": len(results),
            "sessions_with_usable_history": len(usable_session_ids),
            "sessions_without_adequate_history": len(all_session_ids - usable_session_ids),
            "cases_with_usable_history": len(usable_case_ids),
            "cases_without_adequate_history": len(all_case_ids - usable_case_ids),
            "current_observation_comparison_count": len(comparison_list),
            "recurrence_category_counts": dict(sorted(recurrence_counts.items())),
            "observations_with_measurable_trend": measurable_trends,
            "trend_status_counts": dict(sorted(trend_counts.items())),
            "check_priority_counts": dict(sorted(priority_counts.items())),
            "cases_where_history_changed_interpretation": len(changed_cases),
            "cases_where_history_added_no_value": len(no_value_cases),
            "original_recommended_check_count": original_check_count,
            "history_augmented_recommended_check_count": original_check_count,
            "priority_check_count": prioritized_check_count,
            "deferred_check_count": deferred_check_count,
            "checks_removed_by_history": 0,
        },
        "case_ids_where_history_changed_interpretation": sorted(case_id for case_id in changed_cases if case_id),
        "case_ids_where_history_added_no_value": sorted(case_id for case_id in no_value_cases if case_id),
        "interpretation": {
            "history_reduces_check_noise_by_prioritization_not_deletion": deferred_check_count > 0 or prioritized_check_count > 0,
            "history_increases_check_count": False,
            "history_confirms_root_cause": False,
        },
    }


def build_counterfactual_evaluation(h73_results: dict[str, Any], rca_with_history: dict[str, Any]) -> dict[str, Any]:
    original_by_case = {result["source_case_id"]: result for result in h73_results.get("results", [])}
    examples = []
    for result in rca_with_history.get("results", []):
        original = original_by_case[result["source_case_id"]]
        priorities = result.get("recommended_check_priorities", [])
        history_summary = result.get("historical_evidence", {}).get("summary", {})
        priority_changed = any(item["priority"] in {"priority", "deferred"} for item in priorities)
        history_added_info = history_summary.get("prior_occurrence_count", 0) > 0 or priority_changed
        assessment = "adds_meaningful_information" if history_added_info else "adds_no_value"
        if priority_changed:
            assessment = "changes_check_priority"
        examples.append(
            {
                "source_case_id": result.get("source_case_id"),
                "session_id": result.get("session_id"),
                "source_event_id": (result.get("source_event") or {}).get("event_id"),
                "without_history": {
                    "possible_cause_count": len(original.get("possible_causes", [])),
                    "recommended_check_count": len(original.get("recommended_checks", [])),
                    "recommended_check_ids": [check.get("check_id") for check in original.get("recommended_checks", [])],
                },
                "with_history": {
                    "possible_cause_count": len(result.get("possible_causes", [])),
                    "recommended_check_count": len(result.get("recommended_checks", [])),
                    "priority_counts": dict(Counter(item["priority"] for item in priorities)),
                    "historical_recurrence_categories": history_summary.get("recurrence_category_counts", {}),
                },
                "assessment": assessment,
                "causal_hypotheses_changed": original.get("possible_causes", []) != result.get("possible_causes", []),
                "checks_removed": False,
            }
        )

    representative = sorted(
        examples,
        key=lambda item: (
            item["assessment"] != "changes_check_priority",
            item["assessment"] != "adds_meaningful_information",
            item["source_case_id"] or "",
        ),
    )[:12]
    assessment_counts = Counter(item["assessment"] for item in examples)
    return {
        "schema_version": "historical-counterfactual-evaluation-v1",
        "summary": {
            "case_count": len(examples),
            "assessment_counts": dict(sorted(assessment_counts.items())),
            "cases_with_causal_hypothesis_change": sum(1 for item in examples if item["causal_hypotheses_changed"]),
            "cases_with_check_priority_change": sum(1 for item in examples if item["assessment"] == "changes_check_priority"),
            "cases_with_checks_removed": 0,
        },
        "representative_cases": representative,
    }


def build_decision_report(
    contract: dict[str, Any],
    index: dict[str, Any],
    governance: dict[str, Any],
    comparisons: dict[str, Any],
    evaluation: dict[str, Any],
    counterfactual: dict[str, Any],
) -> dict[str, Any]:
    summary = evaluation["summary"]
    policy_ok = (
        contract["policy"]["production_behavior_changed"] is False
        and contract["policy"]["history_can_create_root_cause"] is False
        and index["policy"]["h7_v1_hypotheses_indexed"] is False
        and governance["policy"]["historical_evidence_corpus_is_nominal_baseline"] is False
        and comparisons["policy"]["future_data_leakage_prevented"] is True
        and counterfactual["summary"]["cases_with_causal_hypothesis_change"] == 0
    )
    adds_information = summary["cases_where_history_changed_interpretation"] > 0
    prioritizes_checks = summary["priority_check_count"] > 0 or summary["deferred_check_count"] > 0
    if policy_ok and adds_information:
        recommendation = "advance"
    elif policy_ok:
        recommendation = "revise"
    else:
        recommendation = "reject"
    return {
        "schema_version": "historical-evidence-decision-v1",
        "recommendation": recommendation,
        "production_behavior_changed": False,
        "drive_safe_behavior_changed": False,
        "suitable_for_later_drivesafe_integration": bool(policy_ok and adds_information),
        "answers": {
            "cross_session_history_adds_single_session_unavailable_information": adds_information,
            "recurrence_can_be_measured_reliably": summary["sessions_with_usable_history"] > 0 and comparisons["policy"]["future_data_leakage_prevented"],
            "trends_can_be_measured_without_overinterpretation": (
                "simple monotonic numeric evidence only; most sparse cases remain insufficient"
            ),
            "history_improves_recommended_check_prioritization": prioritizes_checks,
            "history_reduces_or_worsens_diagnostic_noise": (
                "reduces triage noise through deterministic priority/deferred labels without removing checks"
                if prioritizes_checks
                else "adds context but does not materially reduce check noise"
            ),
            "historical_layer_suitable_for_later_drivesafe_integration": bool(policy_ok and adds_information),
            "future_data_leakage_prevented": comparisons["policy"]["future_data_leakage_prevented"],
            "root_cause_claims_created_from_history": False,
            "h7_v1_hypotheses_indexed_as_facts": False,
        },
        "limitations": [
            "Temporal order falls back to session manifest order keys when a parsed capture timestamp is unavailable.",
            "Operating context is unavailable for some historical aggregate events, so those comparisons are marked limited.",
            "History is offline research evidence only and does not update production inference, detector thresholds or DriveSafe APIs.",
            "Nominal reference candidates are not verified healthy ground truth.",
        ],
    }


def write_markdown_report(path: Path, evaluation: dict[str, Any], counterfactual: dict[str, Any], decision: dict[str, Any]) -> None:
    summary = evaluation["summary"]
    lines = [
        "# H8 Historical Evidence and Long-Term Baseline",
        "",
        "## Boundary",
        "",
        "- Historical evidence is structured prior evidence, not chatbot memory.",
        "- Historical evidence is separate from nominal/reference baseline data.",
        "- Production inference and DriveSafe behavior remain unchanged.",
        "- History can prioritize checks but cannot create a root-cause hypothesis.",
        "",
        "## Evaluation",
        "",
        f"- Cases evaluated: `{summary['case_count']}`",
        f"- Sessions with usable history: `{summary['sessions_with_usable_history']}`",
        f"- Sessions without adequate history: `{summary['sessions_without_adequate_history']}`",
        f"- Recurrence categories: `{summary['recurrence_category_counts']}`",
        f"- Observations with measurable trend evidence: `{summary['observations_with_measurable_trend']}`",
        f"- Check priority counts: `{summary['check_priority_counts']}`",
        f"- Cases where history changed interpretation: `{summary['cases_where_history_changed_interpretation']}`",
        f"- Cases where history added no value: `{summary['cases_where_history_added_no_value']}`",
        f"- Recommended checks before/after history: `{summary['original_recommended_check_count']}/{summary['history_augmented_recommended_check_count']}`",
        f"- Checks prioritized/deferred: `{summary['priority_check_count']}`/`{summary['deferred_check_count']}`",
        "",
        "## Counterfactual",
        "",
        f"- Assessment counts: `{counterfactual['summary']['assessment_counts']}`",
        f"- Cases with causal-hypothesis change: `{counterfactual['summary']['cases_with_causal_hypothesis_change']}`",
        f"- Cases with check-priority change: `{counterfactual['summary']['cases_with_check_priority_change']}`",
        "",
        "## Decision",
        "",
        f"- Recommendation: `{decision['recommendation']}`",
        f"- Suitable for later DriveSafe integration: `{decision['suitable_for_later_drivesafe_integration']}`",
        "- Final answer: historical evidence adds useful information when comparable prior sessions exist, but remains an offline research context layer.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def session_is_comparable(current: dict[str, Any], session: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not temporal_precedes(session.get("temporal_order_key") or session.get("session_id"), current.get("temporal_order_key") or current.get("session_id")):
        reasons.append("not_prior_session")
    if session.get("session_id") == current.get("session_id"):
        reasons.append("same_session")
    current_artifacts = ((current.get("source_provenance") or {}).get("artifacts") or {})
    session_artifacts = session.get("artifacts") or {}
    if current_artifacts.get("samples_sha256") and current_artifacts.get("samples_sha256") == session_artifacts.get("samples_sha256"):
        reasons.append("same_sample_hash")
    if (current.get("source_provenance") or {}).get("path") and (current.get("source_provenance") or {}).get("path") == session.get("path"):
        reasons.append("same_source_path")
    current_vehicle = (current.get("vehicle_identity") or {}).get("vehicle_id")
    session_vehicle = (session.get("capture_provenance") or {}).get("vehicle_id")
    if current_vehicle and session_vehicle and current_vehicle != session_vehicle:
        reasons.append("vehicle_mismatch")
    current_decoder = (current.get("decoder") or {}).get("decoder_version_key")
    session_decoder = (session.get("decoder") or {}).get("decoder_version_key")
    if current_decoder and session_decoder and current_decoder != session_decoder:
        reasons.append("decoder_mismatch")
    if not session.get("evaluation_ready") or not session.get("window_count") or session.get("sample_count", 0) <= 0:
        reasons.append("recording_quality_insufficient")
    available_signals = set(session.get("verified_signals") or session.get("signal_columns") or [])
    required_signals = set(current.get("signals") or [])
    missing = required_signals - available_signals
    if missing:
        reasons.append("missing_required_signals")
    return not reasons, reasons


def record_is_comparable(current: dict[str, Any], record: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    current_context = current.get("operating_context")
    record_context = record.get("operating_context")
    if current_context and record_context and current_context != record_context:
        reasons.append("operating_context_mismatch")
    current_detector_versions = current.get("detector_versions") or {}
    record_detector_versions = record.get("detector_versions") or {}
    for detector_id, current_version in current_detector_versions.items():
        record_version = record_detector_versions.get(detector_id)
        if current_version and record_version and current_version != record_version:
            reasons.append(f"detector_version_mismatch:{detector_id}")
    current_rule = (current.get("source_provenance") or {}).get("rca_rule_version")
    record_rule = (record.get("source_provenance") or {}).get("rca_rule_version")
    if current_rule and record_rule and current_rule != record_rule:
        reasons.append("rca_rule_version_mismatch")
    return not reasons, reasons


def recurrence_category(comparable_session_count: int, occurrence_session_count: int) -> str:
    if comparable_session_count <= 0:
        return "historical_comparison_unavailable"
    if occurrence_session_count == 0:
        return "first_observed"
    if occurrence_session_count == 1:
        return "rare_recurrence"
    if occurrence_session_count >= 3 and occurrence_session_count / comparable_session_count >= 0.5:
        return "persistent_across_sessions"
    return "recurrent"


def persistence_category(comparable_session_count: int, occurrence_session_count: int) -> str:
    if comparable_session_count <= 0:
        return "unavailable"
    if occurrence_session_count == 0:
        return "not_observed_in_prior_comparable_sessions"
    if occurrence_session_count == comparable_session_count:
        return "persistent_across_all_comparable_prior_sessions"
    if occurrence_session_count == 1:
        return "single_prior_recurrence"
    return "intermittent_across_comparable_prior_sessions"


def build_trend_evidence(current: dict[str, Any], occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    points = []
    for record in dedupe_occurrences(occurrences + [current]):
        severity = severity_value(record.get("event_magnitude") or {})
        if severity is None:
            continue
        points.append(
            {
                "record_id": record.get("record_id"),
                "session_id": record.get("session_id"),
                "capture_timestamp": record.get("capture_timestamp"),
                "severity_value": severity,
                "raw_magnitude": record.get("event_magnitude"),
            }
        )
    points.sort(key=lambda point: temporal_sort_token(point.get("capture_timestamp") or point.get("session_id")))
    unique_session_points = []
    seen_sessions = set()
    for point in points:
        if point["session_id"] in seen_sessions:
            continue
        seen_sessions.add(point["session_id"])
        unique_session_points.append(point)
    if len(unique_session_points) < 3:
        status = "insufficient_historical_data"
    else:
        values = [point["severity_value"] for point in unique_session_points]
        if all(values[index] <= values[index + 1] for index in range(len(values) - 1)) and any(
            values[index] < values[index + 1] for index in range(len(values) - 1)
        ):
            status = "possible_increasing_trend"
        elif all(values[index] >= values[index + 1] for index in range(len(values) - 1)) and any(
            values[index] > values[index + 1] for index in range(len(values) - 1)
        ):
            status = "possible_decreasing_trend"
        else:
            status = "no_observable_trend"
    return {
        "schema_version": H8_TREND_VERSION,
        "trend_status": status,
        "point_count": len(unique_session_points),
        "method": "monotonic detector-local severity sequence; no regression or predictive-maintenance claim",
        "points": unique_session_points,
        "semantics": trend_category_definitions()[status],
    }


def summarize_case_history(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    recurrence_counts = Counter(comparison["recurrence"]["category"] for comparison in comparisons)
    trend_counts = Counter(comparison["trend"]["trend_status"] for comparison in comparisons)
    prior_occurrence_count = sum(comparison["historical_occurrences"]["occurrence_count"] for comparison in comparisons)
    families = sorted({comparison["current"]["observation_family"] for comparison in comparisons})
    return {
        "comparison_count": len(comparisons),
        "prior_occurrence_count": prior_occurrence_count,
        "observation_families": families,
        "recurrence_category_counts": dict(sorted(recurrence_counts.items())),
        "trend_status_counts": dict(sorted(trend_counts.items())),
        "history_can_create_root_cause": False,
    }


def occurrence_summary(record: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "session_id": record.get("session_id"),
        "event_id": (record.get("event") or {}).get("event_id"),
        "capture_timestamp": record.get("capture_timestamp"),
        "event_duration_ms": (record.get("event") or {}).get("duration_ms"),
        "event_magnitude": record.get("event_magnitude"),
        "source_artifact": record.get("source_artifact"),
        "source_record_id": record.get("source_record_id"),
        "temporal_leakage": not temporal_precedes(record.get("temporal_order_key") or record.get("session_id"), current.get("temporal_order_key") or current.get("session_id")),
        "provenance": {
            "path": (record.get("source_provenance") or {}).get("path"),
            "artifacts": (record.get("source_provenance") or {}).get("artifacts"),
        },
    }


def record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "source_case_id": record.get("source_case_id"),
        "session_id": record.get("session_id"),
        "capture_timestamp": record.get("capture_timestamp"),
        "observation_family": record.get("observation_family"),
        "observation_type": record.get("observation_type"),
        "operating_context": record.get("operating_context"),
        "signals": record.get("signals", []),
        "features": record.get("features", []),
        "event": record.get("event"),
        "event_magnitude": record.get("event_magnitude"),
        "detector_versions": record.get("detector_versions"),
        "decoder": record.get("decoder"),
    }


def detector_versions_for_record(
    event: dict[str, Any],
    detector_finding: dict[str, Any] | None,
    rca_item: dict[str, Any] | None,
) -> dict[str, str]:
    versions = dict(event.get("detector_versions") or {})
    if detector_finding and detector_finding.get("detector_id") and detector_finding.get("model_version"):
        versions[detector_finding["detector_id"]] = detector_finding["model_version"]
    if rca_item:
        detector_output = ((rca_item.get("provenance") or {}).get("detector_output") or {}).get("by_detector") or {}
        for detector_id, finding in detector_output.items():
            if finding.get("model_version"):
                versions[detector_id] = finding["model_version"]
    return dict(sorted(versions.items()))


def event_magnitude(
    detector_finding: dict[str, Any] | None,
    rca_item: dict[str, Any] | None,
    event: dict[str, Any],
) -> dict[str, Any]:
    if rca_item:
        evidence = rca_item.get("evidence") or {}
        top = evidence.get("top_deviation") or {}
        if isinstance(top, dict) and top.get("robust_z") is not None:
            return {
                "value": top.get("robust_z"),
                "unit": "robust_z",
                "direction": "higher_is_more_anomalous",
                "source": "contextual_top_deviation",
                "event_duration_ms": event.get("duration_ms"),
            }
        finding = evidence.get("detector_finding") or {}
        if finding:
            return event_magnitude(finding, None, event)
    if detector_finding:
        score = detector_finding.get("detector_local_score") or {}
        top = (((detector_finding.get("evidence") or {}).get("contextual_evidence") or {}).get("top_deviation") or {})
        if isinstance(top, dict) and top.get("robust_z") is not None:
            return {
                "value": top.get("robust_z"),
                "unit": "robust_z",
                "direction": "higher_is_more_anomalous",
                "source": "contextual_top_deviation",
                "event_duration_ms": event.get("duration_ms"),
            }
        if score.get("value") is not None:
            return {
                "value": score.get("value"),
                "column": score.get("column"),
                "direction": score.get("direction"),
                "source": "detector_local_score",
                "threshold": detector_finding.get("detector_local_threshold"),
                "event_duration_ms": event.get("duration_ms"),
            }
    return {
        "value": event.get("duration_ms"),
        "unit": "ms",
        "direction": "higher_is_longer_event",
        "source": "event_duration",
    }


def severity_value(magnitude: dict[str, Any]) -> float | None:
    value = magnitude.get("value")
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    direction = magnitude.get("direction")
    if direction == "lower_is_more_anomalous":
        return -numeric
    return numeric


def family_for_rca_observation(observation: dict[str, Any]) -> str | None:
    observation_id = observation.get("observation_id")
    if observation_id == "obs.v2.iforest_outside_reference":
        return FAMILY_IFOREST
    if observation_id == "obs.v2.contextual_voltage_deviation":
        return FAMILY_CONTEXTUAL_VOLTAGE
    if observation_id == "obs.v2.detector_disagreement":
        return FAMILY_DETECTOR_DISAGREEMENT
    if observation_id == "obs.v2.constant_signal_feature":
        return FAMILY_CONSTANT_SIGNAL
    if observation_id == "obs.v2.rpm_tps_feature_involvement":
        return FAMILY_RPM_TPS
    if observation_id == "obs.v2.aggregate_evidence":
        evidence_state = (observation.get("evidence") or {}).get("evidence_state")
        if evidence_state == "insufficient_coverage":
            return FAMILY_INSUFFICIENT_COVERAGE
        if evidence_state == "no_evidence":
            return FAMILY_NO_ANOMALY_EVIDENCE
    return None


def family_for_rca_symptom(symptom: dict[str, Any]) -> str | None:
    if symptom.get("symptom_id") == "sym.v2.contextual_supply_voltage_deviation":
        return FAMILY_CONTEXTUAL_VOLTAGE
    return None


def family_for_rca_limitation(limitation: dict[str, Any]) -> str | None:
    if limitation.get("limitation_id") == "limit.v2.insufficient_coverage":
        return FAMILY_INSUFFICIENT_COVERAGE
    if limitation.get("limitation_id") == "limit.v2.no_anomaly_evidence":
        return FAMILY_NO_ANOMALY_EVIDENCE
    return None


def detector_finding_from_rca_evidence(item: dict[str, Any]) -> dict[str, Any] | None:
    evidence = item.get("evidence") or {}
    finding = evidence.get("detector_finding")
    if isinstance(finding, dict):
        if not finding.get("detector_id"):
            detector_id = evidence.get("detector_id")
            if detector_id:
                finding = {**finding, "detector_id": detector_id}
        return finding
    return None


def features_from_rca_item(item: dict[str, Any]) -> list[str]:
    evidence = item.get("evidence") or {}
    candidates: list[Any] = [
        evidence.get("most_unusual_features"),
        evidence.get("features"),
        evidence.get("constant_signal_features"),
        evidence.get("all_unusual_features"),
    ]
    top = evidence.get("top_deviation")
    if isinstance(top, dict):
        candidates.append(top.get("feature"))
    features: list[str] = []
    for candidate in candidates:
        features.extend(parse_feature_list(candidate))
    return sorted(set(features))


def operating_context_from_rca_item(item: dict[str, Any]) -> str | None:
    evidence = item.get("evidence") or {}
    context = evidence.get("operating_context")
    if isinstance(context, str):
        return context
    if isinstance(context, dict):
        value = context.get("operating_context") or context.get("context")
        return str(value) if value else None
    top = evidence.get("top_deviation")
    if isinstance(top, dict) and top.get("operating_context"):
        return str(top["operating_context"])
    return None


def contextual_operating_context(finding: dict[str, Any]) -> str | None:
    evidence = finding.get("evidence") or {}
    context = evidence.get("operating_context")
    contextual = evidence.get("contextual_evidence") or {}
    return context or contextual.get("operating_context")


def parse_feature_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [feature.strip() for feature in raw.split(";") if feature.strip()]
    if isinstance(raw, list):
        return [str(feature) for feature in raw if str(feature)]
    return [str(raw)] if raw else []


def is_rpm_tps_feature(feature: str) -> bool:
    return feature.startswith("rpm_") or feature.startswith("tps_")


def rpm_tps_features(features: list[str]) -> bool:
    return any(is_rpm_tps_feature(feature) for feature in features)


def signals_for_feature(feature: str) -> set[str]:
    if feature.startswith("battery_voltage"):
        return {"battery_voltage"}
    if feature.startswith("rpm"):
        return {"rpm"}
    if feature.startswith("tps_raw"):
        return {"tps_raw"}
    if feature.startswith("tps_voltage"):
        return {"tps_voltage"}
    if feature.startswith("ect_c"):
        return {"ect_c"}
    if feature.startswith("iat_c"):
        return {"iat_c"}
    return set()


def sessions_by_id(h2_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {session.get("session_id"): session for session in h2_manifest.get("sessions", []) if session.get("session_id")}


def dedupe_occurrences(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str | None, str | None, str | None], dict[str, Any]] = {}
    priority = {"h7_3_rca_v2_results": 0, "h7_3_current_rca_v2": 0, "h5_aggregate_event_review": 1}
    for record in records:
        key = (
            record.get("session_id"),
            (record.get("event") or {}).get("event_id"),
            record.get("observation_family"),
        )
        current = by_key.get(key)
        if current is None or priority.get(record.get("source_artifact"), 9) < priority.get(current.get("source_artifact"), 9):
            by_key[key] = record
    return sorted(by_key.values(), key=record_sort_key)


def record_sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        temporal_sort_token(record.get("capture_timestamp") or record.get("temporal_order_key") or record.get("session_id")),
        str(record.get("session_id") or ""),
        str((record.get("event") or {}).get("event_id") or ""),
    )


def record_observed_at(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "session_id": record.get("session_id"),
        "capture_timestamp": record.get("capture_timestamp"),
        "event_id": (record.get("event") or {}).get("event_id"),
        "record_id": record.get("record_id"),
    }


def temporal_precedes(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    left_parsed = parsed_timestamp_token(left)
    right_parsed = parsed_timestamp_token(right)
    if left_parsed and right_parsed:
        return left_parsed < right_parsed
    return str(left) < str(right)


def temporal_sort_token(value: str | None) -> str:
    if not value:
        return ""
    parsed = parsed_timestamp_token(value)
    return parsed or str(value)


def capture_timestamp(temporal_order_key: str | None, session_id: str | None) -> str | None:
    return parsed_timestamp_token(temporal_order_key or "") or parsed_timestamp_token(session_id or "") or temporal_order_key or session_id


def parsed_timestamp_token(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    match = re.search(r"(20\d{12})", text)
    if match:
        return match.group(1)
    match = re.search(r"(20\d{6})[-_T]?(\d{6})", text)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    match = re.search(r"(20\d{6})[-_T]?(\d{4})", text)
    if match:
        return f"{match.group(1)}{match.group(2)}00"
    return None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
