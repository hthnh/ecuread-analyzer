from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.ml.evidence_aggregation import EVIDENCE_STATE_DESCRIPTIONS, coverage_status_for, evidence_state_for
from app.ml.iforest_detector import ISOLATION_FOREST_DETECTOR_ID


DIAGNOSTIC_EVIDENCE_SCHEMA_VERSION = "analyzer-diagnostic-evidence-v1"
DIAGNOSTIC_EVIDENCE_ANALYSIS_VERSION = "h9-drive-safe-diagnostic-evidence-v1"
RESEARCH_DETECTOR_IDS = ("contextual_battery_voltage",)
RESEARCH_COMPONENTS = ("evidence_aggregation", "rca_v2", "historical_evidence")


def diagnostic_evidence_contract() -> dict[str, Any]:
    return {
        "schema_version": "analyzer-diagnostic-evidence-contract-v1",
        "analysis_version": DIAGNOSTIC_EVIDENCE_ANALYSIS_VERSION,
        "top_level_field": "diagnostic_evidence",
        "sections": {
            "detectors": "Per-detector execution evidence and detector-local score semantics.",
            "aggregate": "Evidence aggregation state; no score fusion and no healthy-ground-truth claim.",
            "interpretation": "RCA v2 observations, symptoms, possible causes, checks and limitations.",
            "history": "Historical recurrence/trend context when explicitly computed.",
            "recommended_check_priorities": "Ordering guidance for checks, not component-failure probability.",
            "provenance": "Analyzer, detector, rule and historical cutoff provenance.",
        },
        "policy": {
            "additive_backward_compatible": True,
            "production_anomaly_status_changed": False,
            "health_score_changed": False,
            "alerts_or_notifications_changed": False,
            "score_fusion_used": False,
            "universal_anomaly_score_defined": False,
            "skipped_or_failed_detector_can_be_normal": False,
            "h7_v1_hypotheses_exposed_as_current_conclusions": False,
            "history_can_confirm_root_cause": False,
            "check_priority_is_failure_probability": False,
            "no_evidence_is_verified_healthy": False,
        },
        "capability_markers": {
            "production_baseline": [ISOLATION_FOREST_DETECTOR_ID],
            "research_evidence": [*RESEARCH_DETECTOR_IDS, *RESEARCH_COMPONENTS],
        },
        "allowed_aggregate_states": list(EVIDENCE_STATE_DESCRIPTIONS),
        "allowed_check_priorities": ["priority", "relevant", "deferred", "insufficient_evidence"],
        "unsafe_language": [
            "confirmed fault",
            "component failure",
            "probability of failure",
            "healthy vehicle",
            "diagnosis confirmed",
        ],
    }


def build_diagnostic_evidence(
    *,
    analysis_run_id: str,
    session: Any,
    model_version: str | None,
    telemetry_schema_version: str,
    feature_schema_version: str,
    signal_columns: list[str],
    detector_results: list[dict[str, Any]],
    warnings: list[str] | None = None,
    historical_mode: str = "contemporaneous_analysis",
    created_at: str | None = None,
) -> dict[str, Any]:
    detectors = {detector["detector_id"]: normalize_detector(detector) for detector in detector_results}
    aggregate = aggregate_from_detectors(detectors)
    timestamp = created_at or datetime.now(UTC).isoformat()
    return {
        "schema_version": DIAGNOSTIC_EVIDENCE_SCHEMA_VERSION,
        "analysis_version": DIAGNOSTIC_EVIDENCE_ANALYSIS_VERSION,
        "capability_status": {
            "production_baseline": [ISOLATION_FOREST_DETECTOR_ID],
            "research_evidence_available": False,
            "research_evidence_sections": [],
            "research_evidence_not_production_gating": True,
        },
        "detectors": detectors,
        "aggregate": aggregate,
        "interpretation": unavailable_interpretation(
            "RCA v2 is not computed in the production-compatible analysis path."
        ),
        "history": unavailable_history(
            session_id=getattr(session, "session_id", None),
            mode=historical_mode,
            reason="Historical evidence is not computed in the production-compatible analysis path.",
        ),
        "recommended_check_priorities": [],
        "provenance": {
            "analysis_run_id": analysis_run_id,
            "session_id": getattr(session, "session_id", None),
            "vehicle_id": getattr(session, "vehicle_id", None),
            "device_id": getattr(session, "device_id", None),
            "decoder": {
                "decoder_id": getattr(session, "decoder_id", None),
                "decoder_version": getattr(session, "decoder_version", None),
                "decoder_version_key": getattr(session, "decoder_version_key", None),
            },
            "model_version": model_version,
            "detector_versions": {
                detector_id: detector.get("model_version") for detector_id, detector in detectors.items()
            },
            "telemetry_schema_version": telemetry_schema_version,
            "feature_schema_version": feature_schema_version,
            "signal_columns": list(signal_columns),
            "created_at": timestamp,
            "warnings": list(warnings or []),
            "historical_comparison_cutoff": {
                "mode": historical_mode,
                "session_id": getattr(session, "session_id", None),
                "future_sessions_allowed": False,
            },
        },
        "production_boundary": production_boundary(),
    }


def normalize_detector(detector: dict[str, Any]) -> dict[str, Any]:
    detector_id = detector.get("detector_id")
    finding = detector_session_finding(detector)
    status = detector.get("status")
    applicable = status == "ok" and detector.get("scored_window_count", 0) > 0
    metadata = detector.get("metadata") if isinstance(detector.get("metadata"), dict) else {}
    return {
        "detector_id": detector_id,
        "capability_status": "production_baseline" if detector_id == ISOLATION_FOREST_DETECTOR_ID else "research_evidence",
        "model_name": detector.get("model_name"),
        "model_family": detector.get("model_family"),
        "model_version": detector.get("model_version"),
        "enabled": detector.get("enabled"),
        "execution_status": status,
        "applicability": {
            "applicable": applicable,
            "reason": None if applicable else detector.get("reason"),
            "missing_features": detector.get("missing_features", []),
        },
        "finding": finding,
        "finding_semantics": "session positive when one or more applicable detector windows are anomalous",
        "scored_window_count": detector.get("scored_window_count"),
        "anomaly_window_count": detector.get("anomaly_window_count"),
        "anomaly_ratio": detector.get("anomaly_ratio"),
        "detector_local_score": {
            **(detector.get("anomaly_score") or {}),
            "summary": detector.get("detector_local_score"),
            "value_scope": "per_window",
            "universal_score": False,
        },
        "detector_local_threshold": detector_threshold(detector),
        "evidence": {
            "available_columns": detector.get("available_evidence", []),
            "per_window_values_available_in_analysis_windows": True,
            "metadata": metadata,
        },
        "skip_or_failure_reason": None if status == "ok" else detector.get("reason"),
        "error": detector.get("error"),
        "warnings": detector.get("warnings", []),
    }


def detector_session_finding(detector: dict[str, Any]) -> str:
    status = detector.get("status")
    if status != "ok":
        return "unavailable"
    anomaly_count = detector.get("anomaly_window_count")
    scored_count = detector.get("scored_window_count") or 0
    if scored_count <= 0:
        return "not_applicable"
    if anomaly_count is not None and anomaly_count > 0:
        return "positive"
    return "negative"


def detector_threshold(detector: dict[str, Any]) -> dict[str, Any] | None:
    if detector.get("detector_id") == ISOLATION_FOREST_DETECTOR_ID:
        return {
            "column": "decision_score",
            "value": 0.0,
            "source": "isolation_forest_decision_function",
            "rule": "prediction=-1 when decision_score < 0",
        }
    metadata = detector.get("metadata") if isinstance(detector.get("metadata"), dict) else {}
    if detector.get("detector_id") == "contextual_battery_voltage" and metadata.get("threshold") is not None:
        return {
            "column": "contextual_anomaly_score",
            "value": metadata.get("threshold"),
            "source": "contextual_reference",
            "rule": "robust_z >= threshold maps to contextual anomaly",
        }
    return None


def aggregate_from_detectors(detectors: dict[str, dict[str, Any]]) -> dict[str, Any]:
    applicable = [
        detector
        for detector in detectors.values()
        if detector["finding"] in {"positive", "negative"}
        and detector["execution_status"] == "ok"
        and detector["applicability"]["applicable"] is True
    ]
    positive = [detector for detector in applicable if detector["finding"] == "positive"]
    negative = [detector for detector in applicable if detector["finding"] == "negative"]
    unavailable = [detector for detector in detectors.values() if detector["finding"] == "unavailable"]
    not_applicable = [detector for detector in detectors.values() if detector["finding"] == "not_applicable"]
    state = evidence_state_for(len(applicable), len(positive), len(negative))
    coverage = coverage_status_for(len(applicable), len(detectors))
    return {
        "schema_version": "diagnostic-evidence-aggregate-v1",
        "evidence_state": state,
        "description": EVIDENCE_STATE_DESCRIPTIONS[state],
        "coverage_status": coverage,
        "no_evidence_is_verified_healthy": False,
        "score_fusion_used": False,
        "counts": {
            "detector_count": len(detectors),
            "applicable_detector_count": len(applicable),
            "positive_detector_count": len(positive),
            "negative_detector_count": len(negative),
            "not_applicable_detector_count": len(not_applicable),
            "unavailable_detector_count": len(unavailable),
        },
        "detector_ids": {
            "positive": [detector["detector_id"] for detector in positive],
            "negative": [detector["detector_id"] for detector in negative],
            "not_applicable": [detector["detector_id"] for detector in not_applicable],
            "unavailable": [detector["detector_id"] for detector in unavailable],
        },
        "detector_disagreement": {
            "present": state == "detector_disagreement",
            "positive_detector_ids": [detector["detector_id"] for detector in positive],
            "negative_detector_ids": [detector["detector_id"] for detector in negative],
        },
        "event_provenance": None,
    }


def unavailable_interpretation(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "diagnostic-evidence-rca-v2-interpretation-v1",
        "status": "unavailable",
        "reason": reason,
        "sections_distinct": True,
        "h7_v1_hypotheses_exposed": False,
        "observations": [],
        "symptoms": [],
        "possible_causes": [],
        "recommended_checks": [],
        "limitations": [
            {
                "limitation_id": "limit.integration.rca_v2_unavailable",
                "statement": reason,
            }
        ],
    }


def unavailable_history(*, session_id: str | None, mode: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "diagnostic-evidence-history-v1",
        "status": "unavailable",
        "mode": mode,
        "reason": reason,
        "session_id": session_id,
        "future_sessions_allowed": False,
        "comparisons": [],
        "summary": {
            "comparable_prior_session_count": 0,
            "occurrence_count": 0,
            "recurrence_classification": "historical_comparison_unavailable",
            "trend_classification": "insufficient_historical_data",
        },
        "limitations": [reason],
    }


def production_boundary() -> dict[str, Any]:
    return {
        "production_anomaly_status_changed": False,
        "health_score_changed": False,
        "alerting_changed": False,
        "research_evidence_can_change_alerts": False,
        "root_cause_confirmed": False,
        "predictive_maintenance_claim": False,
    }


def build_research_diagnostic_evidence(result: dict[str, Any]) -> dict[str, Any]:
    detectors = detectors_from_research_result(result)
    aggregate = aggregate_from_research_result(result, detectors)
    history = history_from_research_result(result)
    interpretation = {
        "schema_version": "diagnostic-evidence-rca-v2-interpretation-v1",
        "status": "available",
        "sections_distinct": True,
        "h7_v1_hypotheses_exposed": False,
        "observations": result.get("observations", []),
        "symptoms": result.get("symptoms", []),
        "possible_causes": result.get("possible_causes", []),
        "recommended_checks": result.get("recommended_checks", []),
        "limitations": result.get("evidence_limitations", []),
    }
    return {
        "schema_version": DIAGNOSTIC_EVIDENCE_SCHEMA_VERSION,
        "analysis_version": DIAGNOSTIC_EVIDENCE_ANALYSIS_VERSION,
        "capability_status": {
            "production_baseline": [ISOLATION_FOREST_DETECTOR_ID],
            "research_evidence_available": True,
            "research_evidence_sections": ["aggregate", "rca_v2", "historical_evidence"],
            "research_evidence_not_production_gating": True,
        },
        "detectors": detectors,
        "aggregate": aggregate,
        "interpretation": interpretation,
        "history": history,
        "recommended_check_priorities": result.get("recommended_check_priorities", []),
        "provenance": {
            "analysis_run_id": None,
            "session_id": result.get("session_id"),
            "source_case_id": result.get("source_case_id"),
            "source_review_item_id": result.get("source_review_item_id"),
            "source_event": result.get("source_event"),
            "model_version": detector_versions_from_research(result).get(ISOLATION_FOREST_DETECTOR_ID),
            "detector_versions": detector_versions_from_research(result),
            "rca_rule_version": (result.get("provenance") or {}).get("rule_catalog_version"),
            "historical_comparison_cutoff": {
                "mode": "retrospective_research_analysis",
                "session_id": result.get("session_id"),
                "future_sessions_allowed": False,
                "verified_by_h8_temporal_filter": True,
            },
        },
        "production_boundary": production_boundary(),
    }


def detectors_from_research_result(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    detector_output = detector_output_from_research(result)
    by_detector = detector_output.get("by_detector") or {}
    detectors = {}
    for detector_id, finding in by_detector.items():
        status = finding.get("execution_status")
        session_finding = finding.get("finding") or ("unavailable" if status != "ok" else "not_applicable")
        detectors[detector_id] = {
            "detector_id": detector_id,
            "capability_status": "production_baseline" if detector_id == ISOLATION_FOREST_DETECTOR_ID else "research_evidence",
            "model_name": None,
            "model_family": None,
            "model_version": finding.get("model_version"),
            "enabled": True,
            "execution_status": status,
            "applicability": {
                "applicable": bool(finding.get("applicable")),
                "reason": finding.get("applicability_reason"),
                "missing_features": finding.get("missing_features", []),
            },
            "finding": session_finding,
            "finding_semantics": "event-level detector finding from H5/H7.3/H8 research artifacts",
            "scored_window_count": None,
            "anomaly_window_count": None,
            "anomaly_ratio": None,
            "detector_local_score": finding.get("detector_local_score"),
            "detector_local_threshold": finding.get("detector_local_threshold"),
            "evidence": finding.get("evidence", {}),
            "skip_or_failure_reason": None if status == "ok" else finding.get("applicability_reason"),
            "error": finding.get("error"),
            "warnings": [],
        }
    return detectors


def aggregate_from_research_result(result: dict[str, Any], detectors: dict[str, dict[str, Any]]) -> dict[str, Any]:
    event = result.get("source_event") or {}
    detector_output = detector_output_from_research(result)
    state = event.get("evidence_state")
    if state not in EVIDENCE_STATE_DESCRIPTIONS:
        state = aggregate_from_detectors(detectors)["evidence_state"]
    return {
        "schema_version": "diagnostic-evidence-aggregate-v1",
        "evidence_state": state,
        "description": EVIDENCE_STATE_DESCRIPTIONS[state],
        "coverage_status": event.get("coverage_status"),
        "no_evidence_is_verified_healthy": False,
        "score_fusion_used": False,
        "counts": {
            "positive_detector_count": len(detector_output.get("positive_detector_ids") or []),
            "negative_detector_count": len(detector_output.get("negative_detector_ids") or []),
            "unavailable_detector_count": len(detector_output.get("unavailable_detector_ids") or []),
        },
        "detector_ids": {
            "positive": detector_output.get("positive_detector_ids") or [],
            "negative": detector_output.get("negative_detector_ids") or [],
            "unavailable": detector_output.get("unavailable_detector_ids") or [],
        },
        "detector_disagreement": {
            "present": state == "detector_disagreement",
            "positive_detector_ids": detector_output.get("positive_detector_ids") or [],
            "negative_detector_ids": detector_output.get("negative_detector_ids") or [],
        },
        "event_provenance": event,
    }


def history_from_research_result(result: dict[str, Any]) -> dict[str, Any]:
    source = result.get("historical_evidence") or {}
    comparisons = source.get("comparisons") or []
    recurrence_counts = Counter(
        ((comparison.get("recurrence") or {}).get("category") or "unknown")
        for comparison in comparisons
    )
    trend_counts = Counter(
        ((comparison.get("trend") or {}).get("trend_status") or "unknown")
        for comparison in comparisons
    )
    comparable_prior = sum((comparison.get("compatibility") or {}).get("comparable_session_count", 0) for comparison in comparisons)
    occurrences = sum((comparison.get("historical_occurrences") or {}).get("occurrence_count", 0) for comparison in comparisons)
    return {
        "schema_version": "diagnostic-evidence-history-v1",
        "status": source.get("status", "unavailable"),
        "mode": "retrospective_research_analysis",
        "future_sessions_allowed": False,
        "summary": {
            "comparable_prior_session_count": comparable_prior,
            "occurrence_count": occurrences,
            "recurrence_classification_counts": dict(sorted(recurrence_counts.items())),
            "trend_classification_counts": dict(sorted(trend_counts.items())),
        },
        "comparisons": comparisons,
        "limitations": source.get("summary", {}).get("limitations", []),
    }


def detector_output_from_research(result: dict[str, Any]) -> dict[str, Any]:
    for observation in result.get("observations", []):
        detector_output = ((observation.get("provenance") or {}).get("detector_output") or {})
        if detector_output:
            return detector_output
    return {}


def detector_versions_from_research(result: dict[str, Any]) -> dict[str, str | None]:
    output = detector_output_from_research(result)
    return {
        detector_id: finding.get("model_version")
        for detector_id, finding in (output.get("by_detector") or {}).items()
    }
