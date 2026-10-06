from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import pandas as pd

from app.config import Settings
from app.domain.model import FEATURE_SCHEMA_VERSION
from app.domain.telemetry import CanonicalTelemetrySession
from app.integration.diagnostic_evidence import (
    DIAGNOSTIC_EVIDENCE_ANALYSIS_VERSION,
    DIAGNOSTIC_EVIDENCE_SCHEMA_VERSION,
    aggregate_from_detectors,
    normalize_detector,
    production_boundary,
    unavailable_history,
    unavailable_interpretation,
)
from app.ml.contextual_detector import (
    CONTEXTUAL_BATTERY_DETECTOR_ID,
    CONTEXTUAL_BATTERY_MODEL_FAMILY,
    CONTEXTUAL_BATTERY_MODEL_NAME,
    CONTEXTUAL_REQUIRED_FEATURES,
    ContextualBatteryVoltageDetector,
)
from app.ml.evidence_aggregation import (
    EVIDENCE_STATE_DESCRIPTIONS,
    AggregationPolicy,
    aggregate_window_results,
    build_aggregate_events,
    representative_events,
    summarize_aggregate_windows,
)
from app.ml.harness import DetectorContext, DetectorIdentity, DetectorResult, ModelHarness
from app.ml.iforest_detector import ISOLATION_FOREST_DETECTOR_ID, isolation_forest_identity
from app.ml.root_cause_analysis_v2 import (
    RCA_V2_ENGINE_VERSION,
    RCA_V2_RULE_CATALOG_VERSION,
    RootCauseAnalyzerV2,
)


LIVE_RESEARCH_ANALYSIS_VERSION = "h9c-live-research-harness-v1"
H8_RCA_EXTENSION_VERSION = "root-cause-analysis-v2-history-extension-v1"


@dataclass(slots=True)
class LiveResearchHarnessOutput:
    diagnostic_evidence: dict[str, Any]
    windows: pd.DataFrame
    detector_results: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


def run_research_harness(
    *,
    analysis_run_id: str,
    session: CanonicalTelemetrySession,
    settings: Settings,
    frame_data: pd.DataFrame,
    feature_frame: pd.DataFrame,
    production_detector_results: list[DetectorResult],
    model_version: str | None,
    telemetry_schema_version: str,
    signal_columns: list[str],
    warnings: list[str] | None = None,
    created_at: str | None = None,
) -> LiveResearchHarnessOutput:
    started = perf_counter()
    local_warnings = list(warnings or [])
    timings: dict[str, float | None] = {}

    production_if = _production_if_result(production_detector_results, feature_frame)
    timings["production_if_inference_ms"] = _metadata_latency_ms(production_if)

    contextual_started = perf_counter()
    contextual_result, contextual_reference, contextual_warning = _contextual_result(settings, feature_frame, telemetry_schema_version, signal_columns)
    timings["contextual_detector_ms"] = round((perf_counter() - contextual_started) * 1000.0, 6)
    if contextual_warning:
        local_warnings.append(contextual_warning)

    detector_results = [production_if, contextual_result]
    detector_summaries = [result.to_summary() for result in detector_results]

    aggregation_started = perf_counter()
    aggregate_rows = aggregate_window_results(
        session_id=session.session_id or analysis_run_id,
        feature_frame=feature_frame,
        results=detector_results,
        frame_data=frame_data,
        policy=AggregationPolicy(),
    ) if not feature_frame.empty else []
    aggregate_events = build_aggregate_events(aggregate_rows) if aggregate_rows else _empty_aggregate_events()
    aggregate_summary = summarize_aggregate_windows(aggregate_rows) if aggregate_rows else _empty_aggregate_summary()
    events = representative_events(aggregate_events, limit_per_type=5)
    rca_events = events or _summary_events_for_rca(session.session_id or analysis_run_id, aggregate_rows)
    timings["aggregation_ms"] = round((perf_counter() - aggregation_started) * 1000.0, 6)

    rca_started = perf_counter()
    rca_results = _run_rca_v2(
        analysis_run_id=analysis_run_id,
        session=session,
        events=rca_events,
        signal_columns=signal_columns,
        telemetry_schema_version=telemetry_schema_version,
    )
    timings["rca_ms"] = round((perf_counter() - rca_started) * 1000.0, 6)

    history_started = perf_counter()
    history, recommended_check_priorities = _historical_evidence(
        settings=settings,
        analysis_run_id=analysis_run_id,
        session=session,
        signal_columns=signal_columns,
        telemetry_schema_version=telemetry_schema_version,
        feature_frame=feature_frame,
        rca_results=rca_results,
    )
    timings["history_lookup_ms"] = round((perf_counter() - history_started) * 1000.0, 6)
    timings["total_research_shadow_ms"] = round((perf_counter() - started) * 1000.0, 6)

    evidence = _diagnostic_evidence(
        analysis_run_id=analysis_run_id,
        session=session,
        model_version=model_version,
        telemetry_schema_version=telemetry_schema_version,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        signal_columns=signal_columns,
        detector_summaries=detector_summaries,
        aggregate_summary=aggregate_summary,
        aggregate_events=aggregate_events,
        representative_event_items=events,
        rca_results=rca_results,
        history=history,
        recommended_check_priorities=recommended_check_priorities,
        contextual_reference=contextual_reference,
        timings=timings,
        warnings=local_warnings,
        created_at=created_at,
    )
    return LiveResearchHarnessOutput(
        diagnostic_evidence=_json_clone(evidence),
        windows=_merge_research_windows(production_if.windows, contextual_result.windows),
        detector_results=detector_summaries,
        warnings=local_warnings,
    )


def _production_if_result(results: list[DetectorResult], feature_frame: pd.DataFrame) -> DetectorResult:
    for result in results:
        if result.identity.detector_id == ISOLATION_FOREST_DETECTOR_ID:
            return result
    return DetectorResult(
        identity=isolation_forest_identity({}),
        status="skipped",
        reason="not_scored",
        required_features=(),
        windows=feature_frame.copy(),
        scored_window_count=0,
    )


def _contextual_result(
    settings: Settings,
    feature_frame: pd.DataFrame,
    telemetry_schema_version: str,
    signal_columns: list[str],
) -> tuple[DetectorResult, dict[str, Any] | None, str | None]:
    reference_path = settings.data_dir / "evaluation" / "h3" / "contextual_reference.json"
    if not reference_path.exists():
        return (
            _contextual_skipped_result(feature_frame, reason="contextual_reference_unavailable"),
            None,
            f"contextual battery reference not found at {reference_path}; research detector marked skipped",
        )
    try:
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - optional research component failure is isolated.
        return (
            _contextual_failed_result(feature_frame, type(exc).__name__, str(exc)),
            None,
            f"contextual battery reference could not be loaded: {type(exc).__name__}: {exc}",
        )

    context = DetectorContext(
        telemetry_schema_version=telemetry_schema_version,
        signal_columns=tuple(signal_columns),
        minimum_windows_for_status=settings.min_session_windows_for_status,
    )
    result = ModelHarness([ContextualBatteryVoltageDetector(reference)]).run(feature_frame, context).result_for(
        CONTEXTUAL_BATTERY_DETECTOR_ID
    )
    if result is None:
        return (
            _contextual_failed_result(feature_frame, "RuntimeError", "contextual detector was not registered"),
            reference,
            "contextual battery detector did not return a result",
        )
    return result, reference, None


def _contextual_skipped_result(feature_frame: pd.DataFrame, *, reason: str) -> DetectorResult:
    return DetectorResult(
        identity=DetectorIdentity(
            detector_id=CONTEXTUAL_BATTERY_DETECTOR_ID,
            model_name=CONTEXTUAL_BATTERY_MODEL_NAME,
            model_family=CONTEXTUAL_BATTERY_MODEL_FAMILY,
            model_version=None,
        ),
        status="skipped",
        reason=reason,
        required_features=CONTEXTUAL_REQUIRED_FEATURES,
        windows=feature_frame.copy(),
        scored_window_count=0,
    )


def _contextual_failed_result(feature_frame: pd.DataFrame, error_type: str, error_message: str) -> DetectorResult:
    return DetectorResult(
        identity=DetectorIdentity(
            detector_id=CONTEXTUAL_BATTERY_DETECTOR_ID,
            model_name=CONTEXTUAL_BATTERY_MODEL_NAME,
            model_family=CONTEXTUAL_BATTERY_MODEL_FAMILY,
            model_version=None,
        ),
        status="failed",
        reason="contextual_reference_error",
        required_features=CONTEXTUAL_REQUIRED_FEATURES,
        windows=feature_frame.copy(),
        scored_window_count=0,
        error_type=error_type,
        error_message=error_message,
    )


def _diagnostic_evidence(
    *,
    analysis_run_id: str,
    session: CanonicalTelemetrySession,
    model_version: str | None,
    telemetry_schema_version: str,
    feature_schema_version: str,
    signal_columns: list[str],
    detector_summaries: list[dict[str, Any]],
    aggregate_summary: dict[str, Any],
    aggregate_events: dict[str, Any],
    representative_event_items: list[dict[str, Any]],
    rca_results: list[dict[str, Any]],
    history: dict[str, Any],
    recommended_check_priorities: list[dict[str, Any]],
    contextual_reference: dict[str, Any] | None,
    timings: dict[str, float | None],
    warnings: list[str],
    created_at: str | None,
) -> dict[str, Any]:
    detectors = {detector["detector_id"]: normalize_detector(detector) for detector in detector_summaries}
    aggregate = aggregate_from_detectors(detectors)
    state = _session_evidence_state(aggregate_summary, aggregate["evidence_state"])
    aggregate["evidence_state"] = state
    aggregate["description"] = EVIDENCE_STATE_DESCRIPTIONS[state]
    aggregate["coverage_status"] = _session_coverage_status(aggregate_summary, aggregate.get("coverage_status"))
    aggregate["window_summary"] = aggregate_summary
    aggregate["detector_disagreement"] = {
        **aggregate.get("detector_disagreement", {}),
        "disagreement_window_count": int(aggregate_summary.get("disagreement_windows", 0) or 0),
    }
    aggregate["agreement"] = {
        "full_coverage_agreement_window_count": int(aggregate_summary.get("both_applicable_agreement_windows", 0) or 0),
        "no_score_fusion_or_voting": True,
    }

    component_status = _component_status(detectors, history, rca_results)
    return {
        "schema_version": DIAGNOSTIC_EVIDENCE_SCHEMA_VERSION,
        "analysis_version": DIAGNOSTIC_EVIDENCE_ANALYSIS_VERSION,
        "live_research_analysis_version": LIVE_RESEARCH_ANALYSIS_VERSION,
        "status": component_status["overall_status"],
        "capability_status": {
            "production_baseline": [ISOLATION_FOREST_DETECTOR_ID],
            "research_evidence_available": True,
            "research_evidence_sections": ["contextual_battery_voltage", "aggregate", "rca_v2", "historical_evidence"],
            "research_evidence_not_production_gating": True,
            "component_status": component_status,
        },
        "detectors": detectors,
        "aggregate": aggregate,
        "interpretation": _interpretation_from_rca(rca_results),
        "history": history,
        "events": {
            "schema_version": "diagnostic-evidence-events-v1",
            "bounded": True,
            "max_items": 15,
            "finding_event_count": int(aggregate_events.get("summary", {}).get("finding_event_count", 0) or 0),
            "coverage_gap_region_count": int(aggregate_events.get("summary", {}).get("coverage_gap_region_count", 0) or 0),
            "items": representative_event_items[:15],
        },
        "recommended_check_priorities": recommended_check_priorities,
        "provenance": {
            "analysis_run_id": analysis_run_id,
            "session_id": session.session_id,
            "vehicle_id": session.vehicle_id,
            "device_id": session.device_id,
            "decoder": {
                "decoder_id": session.decoder_id,
                "decoder_version": session.decoder_version,
                "decoder_version_key": session.decoder_version_key,
            },
            "model_version": model_version,
            "detector_versions": {
                detector_id: detector.get("model_version") for detector_id, detector in detectors.items()
            },
            "contextual_reference_version": (contextual_reference or {}).get("model_version"),
            "contextual_reference_schema_version": (contextual_reference or {}).get("schema_version"),
            "rca_engine_version": RCA_V2_ENGINE_VERSION,
            "rca_rule_version": RCA_V2_RULE_CATALOG_VERSION,
            "telemetry_schema_version": telemetry_schema_version,
            "feature_schema_version": feature_schema_version,
            "signal_columns": list(signal_columns),
            "created_at": created_at,
            "warnings": warnings,
            "historical_comparison_cutoff": {
                "mode": history.get("mode", "live_research_analysis"),
                "session_id": session.session_id,
                "analysis_run_id": analysis_run_id,
                "future_sessions_allowed": False,
            },
            "runtime_ms": timings,
        },
        "production_boundary": production_boundary(),
    }


def _component_status(
    detectors: dict[str, dict[str, Any]],
    history: dict[str, Any],
    rca_results: list[dict[str, Any]],
) -> dict[str, Any]:
    contextual = detectors.get(CONTEXTUAL_BATTERY_DETECTOR_ID, {})
    detector_statuses = [detector.get("execution_status") for detector in detectors.values()]
    failed = any(status == "failed" for status in detector_statuses)
    skipped_or_unavailable = any(status in {"skipped", None} for status in detector_statuses)
    overall = "error" if failed else "partial" if skipped_or_unavailable or history.get("status") != "available" else "available"
    return {
        "overall_status": overall,
        "isolation_forest": detectors.get(ISOLATION_FOREST_DETECTOR_ID, {}).get("execution_status"),
        "contextual_battery_voltage": contextual.get("execution_status"),
        "evidence_aggregation": "available",
        "rca_v2": "available" if rca_results else "unavailable",
        "historical_evidence": history.get("status"),
    }


def _interpretation_from_rca(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return unavailable_interpretation("RCA v2 did not receive any live aggregate evidence event.")
    return {
        "schema_version": "diagnostic-evidence-rca-v2-interpretation-v1",
        "status": "available",
        "sections_distinct": True,
        "h7_v1_hypotheses_exposed": False,
        "observations": _dedupe(_flatten(result.get("observations", []) for result in results), "observation_id"),
        "symptoms": _dedupe(_flatten(result.get("symptoms", []) for result in results), "symptom_id"),
        "possible_causes": _dedupe(_flatten(result.get("possible_causes", []) for result in results), "cause_id"),
        "recommended_checks": _dedupe(_flatten(result.get("recommended_checks", []) for result in results), "check_id"),
        "limitations": _dedupe(_flatten(result.get("evidence_limitations", []) for result in results), "limitation_id"),
    }


def _run_rca_v2(
    *,
    analysis_run_id: str,
    session: CanonicalTelemetrySession,
    events: list[dict[str, Any]],
    signal_columns: list[str],
    telemetry_schema_version: str,
) -> list[dict[str, Any]]:
    analyzer = RootCauseAnalyzerV2()
    results: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        case = _rca_case(
            analysis_run_id=analysis_run_id,
            session=session,
            event=event,
            index=index,
            signal_columns=signal_columns,
            telemetry_schema_version=telemetry_schema_version,
        )
        results.append(analyzer.analyze_case(case))
    return results


def _rca_case(
    *,
    analysis_run_id: str,
    session: CanonicalTelemetrySession,
    event: dict[str, Any],
    index: int,
    signal_columns: list[str],
    telemetry_schema_version: str,
) -> dict[str, Any]:
    representative = event.get("representative_window") or {}
    findings = representative.get("detector_findings") or []
    by_detector = {finding.get("detector_id"): finding for finding in findings if finding.get("detector_id")}
    contextual = by_detector.get(CONTEXTUAL_BATTERY_DETECTOR_ID, {})
    iforest = by_detector.get(ISOLATION_FOREST_DETECTOR_ID, {})
    contextual_evidence = (contextual.get("evidence") or {}).get("contextual_evidence") or {}
    iforest_evidence = iforest.get("evidence") or {}
    detector_findings = {
        "positive_detector_ids": list(event.get("positive_detector_ids") or representative.get("detector_ids", {}).get("positive") or []),
        "negative_detector_ids": list(event.get("negative_detector_ids") or representative.get("detector_ids", {}).get("negative") or []),
        "unavailable_detector_ids": list(event.get("unavailable_detector_ids") or representative.get("detector_ids", {}).get("unavailable") or []),
        "not_applicable_detector_ids": list(event.get("not_applicable_detector_ids") or representative.get("detector_ids", {}).get("not_applicable") or []),
        "by_detector": by_detector,
    }
    return {
        "case_id": f"h9c-live-case-{index:04d}",
        "source_review_item_id": None,
        "review_presentation": {
            "observable_evidence": {
                "event_timing": {
                    "start_time_ms": event.get("start_time_ms"),
                    "end_time_ms": event.get("end_time_ms"),
                    "duration_ms": event.get("duration_ms"),
                    "start_window_index": event.get("start_window_index"),
                    "end_window_index": event.get("end_window_index"),
                    "window_count": event.get("window_count"),
                },
                "operating_context": contextual_evidence.get("operating_context"),
                "telemetry": representative.get("telemetry") or {},
                "detector_findings": detector_findings,
                "unusual_feature_evidence": {
                    "isolation_forest_most_unusual_features": iforest_evidence.get("most_unusual_features"),
                    "contextual_top_deviation": contextual_evidence.get("top_deviation"),
                },
            }
        },
        "selection_tags": {
            "detector_evidence_state": event.get("evidence_state"),
            "is_insufficient_evidence_case": event.get("coverage_status") == "none"
            or event.get("evidence_state") == "insufficient_coverage",
            "is_no_anomaly_evidence_case": event.get("evidence_state") == "no_evidence",
        },
        "provenance": {
            "analysis_run_id": analysis_run_id,
            "session_id": session.session_id,
            "source_event": event,
            "capture_provenance": {
                "vehicle_id": session.vehicle_id,
                "device_id": session.device_id,
                "ecu_profile_id": session.ecu_profile_id,
                "source_type": session.source_type,
            },
            "decoder": {
                "decoder_id": session.decoder_id,
                "decoder_version": session.decoder_version,
                "decoder_version_key": session.decoder_version_key,
            },
            "telemetry_schema_version": telemetry_schema_version,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "signal_columns": list(signal_columns),
            "rca_rule_version": RCA_V2_RULE_CATALOG_VERSION,
        },
    }


def _historical_evidence(
    *,
    settings: Settings,
    analysis_run_id: str,
    session: CanonicalTelemetrySession,
    signal_columns: list[str],
    telemetry_schema_version: str,
    feature_frame: pd.DataFrame,
    rca_results: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from app.evaluation.historical_evidence import (
        compare_current_record_to_history,
        prioritize_checks,
        rca_v2_result_records,
        summarize_case_history,
    )

    if not rca_results:
        return (
            unavailable_history(
                session_id=session.session_id,
                mode="live_research_analysis",
                reason="Historical evidence is unavailable because RCA v2 produced no live result.",
            ),
            [],
        )

    index_path = settings.data_dir / "evaluation" / "h8" / "historical_observation_index.json"
    manifest_path = settings.data_dir / "evaluation" / "h2" / "evaluation_manifest.json"
    if not index_path.exists() or not manifest_path.exists():
        history = unavailable_history(
            session_id=session.session_id,
            mode="live_research_analysis",
            reason="Historical evidence index or evaluation manifest is unavailable for live analysis.",
        )
        return history, _priorities_without_history(rca_results)

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - optional research component failure is isolated.
        history = unavailable_history(
            session_id=session.session_id,
            mode="live_research_analysis",
            reason=f"Historical evidence could not be loaded: {type(exc).__name__}: {exc}",
        )
        return history, _priorities_without_history(rca_results)

    current_session = _current_session_manifest(
        session=session,
        analysis_run_id=analysis_run_id,
        signal_columns=signal_columns,
        telemetry_schema_version=telemetry_schema_version,
        feature_frame=feature_frame,
    )
    session_by_id = {session.session_id: current_session}
    current_records: list[dict[str, Any]] = []
    sequence = 1
    for result in rca_results:
        records = rca_v2_result_records(result, session_by_id, sequence, source_artifact="h9c_live_rca_v2")
        current_records.extend(records)
        sequence += len(records)

    comparisons: list[dict[str, Any]] = []
    by_case: dict[str, list[dict[str, Any]]] = {}
    historical_sessions = manifest.get("sessions", [])
    historical_records = index.get("records", [])
    for comparison_index, current in enumerate(current_records, start=1):
        comparison = compare_current_record_to_history(comparison_index, current, historical_records, historical_sessions)
        comparisons.append(comparison)
        if current.get("source_case_id"):
            by_case.setdefault(current["source_case_id"], []).append(comparison)

    priority_items: list[dict[str, Any]] = []
    for result in rca_results:
        priority_items.extend(prioritize_checks(result, by_case.get(result.get("source_case_id"), [])))

    summary = summarize_case_history(comparisons)
    history = {
        "schema_version": H8_RCA_EXTENSION_VERSION,
        "status": "available" if comparisons else "unavailable",
        "mode": "live_research_analysis",
        "future_sessions_allowed": False,
        "can_create_root_cause": False,
        "history_can_create_root_cause": False,
        "comparison_ids": [comparison["comparison_id"] for comparison in comparisons],
        "summary": summary,
        "comparisons": comparisons,
        "limitations": _history_limitations(comparisons),
    }
    return history, _dedupe(priority_items, "check_id")


def _priorities_without_history(rca_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.evaluation.historical_evidence import prioritize_checks

    priorities: list[dict[str, Any]] = []
    for result in rca_results:
        priorities.extend(prioritize_checks(result, []))
    return _dedupe(priorities, "check_id")


def _current_session_manifest(
    *,
    session: CanonicalTelemetrySession,
    analysis_run_id: str,
    signal_columns: list[str],
    telemetry_schema_version: str,
    feature_frame: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "temporal_order_key": analysis_run_id,
        "evaluation_ready": True,
        "window_count": int(len(feature_frame)),
        "sample_count": len(session.samples),
        "verified_signals": list(signal_columns),
        "signal_columns": list(signal_columns),
        "telemetry_schema_version": telemetry_schema_version,
        "capture_provenance": {
            "vehicle_id": session.vehicle_id,
            "device_id": session.device_id,
            "ecu_profile_id": session.ecu_profile_id,
            "source_type": session.source_type,
        },
        "decoder": {
            "decoder_id": session.decoder_id,
            "decoder_version": session.decoder_version,
            "decoder_version_key": session.decoder_version_key,
        },
        "artifacts": {},
    }


def _history_limitations(comparisons: list[dict[str, Any]]) -> list[str]:
    limitations = sorted(
        {
            limitation
            for comparison in comparisons
            for limitation in comparison.get("comparison_limitations", [])
        }
    )
    return limitations or ["Historical evidence is descriptive only and cannot confirm root cause."]


def _session_evidence_state(summary: dict[str, Any], fallback: str) -> str:
    counts = summary.get("evidence_state_counts") or {}
    for state in ("detector_disagreement", "multiple_detector_evidence", "single_detector_evidence", "no_evidence"):
        if int(counts.get(state, 0) or 0) > 0:
            return state
    if int(counts.get("insufficient_coverage", 0) or 0) > 0:
        return "insufficient_coverage"
    return fallback


def _session_coverage_status(summary: dict[str, Any], fallback: str | None) -> str:
    counts = summary.get("coverage_status_counts") or {}
    if int(counts.get("full", 0) or 0) > 0:
        return "full"
    if int(counts.get("partial", 0) or 0) > 0:
        return "partial"
    if int(counts.get("none", 0) or 0) > 0:
        return "none"
    return fallback or "none"


def _summary_events_for_rca(session_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    first = rows[0]
    detector_versions = {
        finding["detector_id"]: finding.get("model_version")
        for finding in first.get("detector_findings", [])
    }
    return [
        {
            "event_id": f"{session_id}:session-summary",
            "event_type": "session_summary",
            "session_id": session_id,
            "start_window_index": first.get("window_index"),
            "end_window_index": first.get("window_index"),
            "start_window_number": first.get("window_number"),
            "end_window_number": first.get("window_number"),
            "start_frame_index": first.get("start_frame_index"),
            "end_frame_index": first.get("end_frame_index"),
            "start_time_ms": first.get("start_time_ms"),
            "end_time_ms": first.get("end_time_ms"),
            "window_count": 1,
            "duration_ms": first.get("duration_ms"),
            "evidence_state": first.get("aggregate_evidence_status", {}).get("state"),
            "coverage_status": first.get("coverage", {}).get("status"),
            "positive_detector_ids": list(first.get("detector_ids", {}).get("positive", [])),
            "negative_detector_ids": list(first.get("detector_ids", {}).get("negative", [])),
            "not_applicable_detector_ids": list(first.get("detector_ids", {}).get("not_applicable", [])),
            "unavailable_detector_ids": list(first.get("detector_ids", {}).get("unavailable", [])),
            "detector_versions": detector_versions,
            "source_window_range": {
                "start_window_number": first.get("window_number"),
                "end_window_number": first.get("window_number"),
            },
            "representative_window": first,
            "interpretation": "aggregate evidence relationship only; not a diagnostic root-cause claim",
        }
    ]


def _merge_research_windows(production_windows: pd.DataFrame, contextual_windows: pd.DataFrame) -> pd.DataFrame:
    windows = production_windows.copy()
    for column in contextual_windows.columns:
        if column.startswith("contextual_") or column == "operating_context":
            windows[column] = contextual_windows[column].values
    return windows


def _metadata_latency_ms(result: DetectorResult) -> float | None:
    value = result.metadata.get("harness_latency_ms")
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _empty_aggregate_events() -> dict[str, Any]:
    return {
        "schema_version": "multi-detector-event-aggregation-v1",
        "finding_events": [],
        "coverage_gap_regions": [],
        "summary": {
            "finding_event_count": 0,
            "coverage_gap_region_count": 0,
            "event_type_counts": {},
        },
    }


def _empty_aggregate_summary() -> dict[str, Any]:
    return {
        "window_count": 0,
        "evidence_state_counts": {},
        "coverage_status_counts": {},
        "applicable_detector_count_distribution": {},
        "detector_finding_counts": {},
        "if_only_finding_windows": 0,
        "contextual_only_finding_windows": 0,
        "overlapping_finding_windows": 0,
        "disagreement_windows": 0,
        "insufficient_coverage_windows": 0,
        "only_one_detector_applicable_windows": 0,
        "both_applicable_agreement_windows": 0,
        "no_evidence_windows": 0,
        "no_evidence_is_verified_healthy": False,
        "score_fusion_used": False,
    }


def _flatten(groups: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in groups:
        items.extend(group)
    return items


def _dedupe(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        value = item.get(key)
        if value is None:
            output.append(item)
            continue
        token = str(value)
        if token in seen:
            continue
        seen.add(token)
        output.append(item)
    return output


def _json_clone(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str))
