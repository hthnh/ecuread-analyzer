from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Literal

import pandas as pd

from app.ml.contextual_detector import CONTEXTUAL_BATTERY_DETECTOR_ID
from app.ml.harness import DetectorResult
from app.ml.iforest_detector import ISOLATION_FOREST_DETECTOR_ID


ACTIVE_H5_DETECTOR_IDS = (
    ISOLATION_FOREST_DETECTOR_ID,
    CONTEXTUAL_BATTERY_DETECTOR_ID,
)
REJECTED_TEMPORAL_CANDIDATE_IDS = (
    "temporal_battery_shift",
    "temporal_rpm_stability",
)

EvidenceState = Literal[
    "no_evidence",
    "single_detector_evidence",
    "multiple_detector_evidence",
    "detector_disagreement",
    "insufficient_coverage",
]
CoverageStatus = Literal["full", "partial", "none"]
FindingState = Literal["positive", "negative", "not_applicable", "unavailable"]


EVIDENCE_STATE_DESCRIPTIONS = {
    "no_evidence": (
        "At least one active detector was applicable and no active detector produced anomaly evidence; "
        "this is not a verified-healthy label."
    ),
    "single_detector_evidence": "Exactly one applicable active detector produced anomaly evidence.",
    "multiple_detector_evidence": "More than one applicable active detector produced anomaly evidence.",
    "detector_disagreement": "At least one applicable active detector was positive and at least one was negative.",
    "insufficient_coverage": "No active detector was applicable for this window.",
}


@dataclass(frozen=True, slots=True)
class AggregationPolicy:
    detector_ids: tuple[str, ...] = ACTIVE_H5_DETECTOR_IDS
    score_fusion_used: bool = False
    no_evidence_is_verified_healthy: bool = False
    insufficient_coverage_rule: str = "zero_applicable_active_detectors"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregation_contract(policy: AggregationPolicy | None = None) -> dict[str, Any]:
    resolved = policy or AggregationPolicy()
    return {
        "schema_version": "multi-detector-evidence-aggregation-contract-v1",
        "active_detector_ids": list(resolved.detector_ids),
        "excluded_detector_ids": list(REJECTED_TEMPORAL_CANDIDATE_IDS),
        "policy": resolved.to_dict(),
        "window_detector_finding_fields": [
            "analysis/window identity",
            "detector id",
            "detector version",
            "execution status",
            "applicability",
            "prediction when available",
            "detector-local score",
            "detector-local threshold when available",
            "evidence",
            "reason for skipped/failed/unavailable state",
        ],
        "aggregate_fields": [
            "available detector count",
            "applicable detector count",
            "positive finding count",
            "negative finding count",
            "unavailable detector count",
            "detector findings",
            "detector disagreements",
            "coverage summary",
            "aggregate evidence status",
        ],
        "evidence_state_descriptions": EVIDENCE_STATE_DESCRIPTIONS,
        "score_policy": "Detector-local scores are preserved with their own semantics; no universal score is defined.",
    }


def aggregate_window_results(
    *,
    session_id: str,
    feature_frame: pd.DataFrame,
    results: list[DetectorResult],
    frame_data: pd.DataFrame | None = None,
    policy: AggregationPolicy | None = None,
    telemetry_columns: tuple[str, ...] = (
        "rpm_median",
        "tps_raw_median",
        "tps_voltage_median",
        "battery_voltage_median",
        "battery_voltage_min",
        "battery_voltage_mean",
    ),
) -> list[dict[str, Any]]:
    resolved_policy = policy or AggregationPolicy()
    by_id = {result.identity.detector_id: result for result in results}
    rows: list[dict[str, Any]] = []
    for row_number in range(len(feature_frame)):
        feature_row = feature_frame.iloc[row_number]
        findings = [
            detector_window_finding(by_id.get(detector_id), detector_id, row_number)
            for detector_id in resolved_policy.detector_ids
        ]
        rows.append(
            aggregate_window(
                session_id=session_id,
                window_identity=window_identity(feature_row, row_number, frame_data),
                detector_findings=findings,
                telemetry=telemetry_snapshot(feature_row, telemetry_columns),
                policy=resolved_policy,
            )
        )
    return rows


def detector_window_finding(
    result: DetectorResult | None,
    detector_id: str,
    row_number: int,
) -> dict[str, Any]:
    if result is None:
        return {
            "detector_id": detector_id,
            "model_version": None,
            "execution_status": "skipped",
            "available": False,
            "applicable": False,
            "applicability_reason": "detector_not_registered",
            "finding": "unavailable",
            "prediction": None,
            "is_anomaly": None,
            "detector_local_score": None,
            "detector_local_threshold": None,
            "evidence": {},
            "missing_features": [],
            "error": None,
        }

    payload = result.window_payload(row_number)
    execution_status = str(payload.get("status") or result.status)
    evidence = payload.get("evidence") or {}
    is_anomaly = _bool_or_none(payload.get("is_anomaly"))
    available = execution_status == "ok"
    applicable = available and is_anomaly is not None
    finding: FindingState
    if not available:
        finding = "unavailable"
    elif not applicable:
        finding = "not_applicable"
    elif is_anomaly is True:
        finding = "positive"
    else:
        finding = "negative"

    return {
        "detector_id": result.identity.detector_id,
        "model_version": result.identity.model_version,
        "execution_status": execution_status,
        "available": available,
        "applicable": applicable,
        "applicability_reason": None if applicable else _applicability_reason(payload, evidence),
        "finding": finding,
        "prediction": _json_safe(payload.get("prediction")) if applicable else None,
        "is_anomaly": is_anomaly if applicable else None,
        "detector_local_score": detector_local_score(result, payload),
        "detector_local_threshold": detector_local_threshold(result, evidence),
        "evidence": _json_safe(evidence),
        "missing_features": list(payload.get("missing_features") or result.missing_features),
        "error": (
            {"type": result.error_type, "message": result.error_message}
            if result.error_type or result.error_message
            else None
        ),
    }


def detector_local_score(result: DetectorResult, payload: dict[str, Any]) -> dict[str, Any] | None:
    score = payload.get("anomaly_score")
    if score is None:
        return None
    return {
        "value": _json_safe(score),
        "column": result.anomaly_score_column,
        "direction": result.score_direction,
    }


def detector_local_threshold(result: DetectorResult, evidence: dict[str, Any]) -> dict[str, Any] | None:
    for key, value in evidence.items():
        if key.endswith("_threshold") and value is not None:
            return {"column": key, "value": _json_safe(value), "source": "detector_evidence"}
    nested = evidence.get("contextual_evidence")
    if isinstance(nested, dict) and nested.get("threshold") is not None:
        return {"column": "contextual_anomaly_score", "value": _json_safe(nested["threshold"]), "source": "detector_evidence"}
    if result.identity.detector_id == ISOLATION_FOREST_DETECTOR_ID:
        return {
            "column": "decision_score",
            "value": 0.0,
            "source": "isolation_forest_decision_function",
            "rule": "prediction=-1 when decision_score < 0",
        }
    return None


def aggregate_window(
    *,
    session_id: str,
    window_identity: dict[str, Any],
    detector_findings: list[dict[str, Any]],
    telemetry: dict[str, Any] | None = None,
    policy: AggregationPolicy | None = None,
) -> dict[str, Any]:
    resolved_policy = policy or AggregationPolicy()
    available = [finding for finding in detector_findings if finding["available"]]
    applicable = [finding for finding in detector_findings if finding["applicable"]]
    positive = [finding for finding in applicable if finding["finding"] == "positive"]
    negative = [finding for finding in applicable if finding["finding"] == "negative"]
    not_applicable = [finding for finding in detector_findings if finding["available"] and not finding["applicable"]]
    unavailable = [finding for finding in detector_findings if not finding["available"]]
    coverage_status = coverage_status_for(len(applicable), len(resolved_policy.detector_ids))
    evidence_state = evidence_state_for(len(applicable), len(positive), len(negative))
    detector_disagreements = []
    if evidence_state == "detector_disagreement":
        detector_disagreements.append(
            {
                "positive_detector_ids": [finding["detector_id"] for finding in positive],
                "negative_detector_ids": [finding["detector_id"] for finding in negative],
                "basis": "applicable detectors produced different anomaly flags",
            }
        )
    return {
        "schema_version": "multi-detector-window-aggregation-v1",
        "session_id": session_id,
        **window_identity,
        "telemetry": telemetry or {},
        "active_detector_ids": list(resolved_policy.detector_ids),
        "detector_findings": detector_findings,
        "counts": {
            "available_detector_count": len(available),
            "applicable_detector_count": len(applicable),
            "positive_finding_count": len(positive),
            "negative_finding_count": len(negative),
            "not_applicable_detector_count": len(not_applicable),
            "unavailable_detector_count": len(unavailable),
        },
        "detector_ids": {
            "positive": [finding["detector_id"] for finding in positive],
            "negative": [finding["detector_id"] for finding in negative],
            "not_applicable": [finding["detector_id"] for finding in not_applicable],
            "unavailable": [finding["detector_id"] for finding in unavailable],
        },
        "coverage": {
            "status": coverage_status,
            "full_detector_count": len(resolved_policy.detector_ids),
            "applicable_ratio": round(len(applicable) / max(1, len(resolved_policy.detector_ids)), 6),
        },
        "aggregate_evidence_status": {
            "state": evidence_state,
            "description": EVIDENCE_STATE_DESCRIPTIONS[evidence_state],
            "no_evidence_is_verified_healthy": False,
            "score_fusion_used": False,
        },
        "detector_disagreements": detector_disagreements,
    }


def coverage_status_for(applicable_count: int, detector_count: int) -> CoverageStatus:
    if applicable_count == 0:
        return "none"
    if applicable_count == detector_count:
        return "full"
    return "partial"


def evidence_state_for(applicable_count: int, positive_count: int, negative_count: int) -> EvidenceState:
    if applicable_count == 0:
        return "insufficient_coverage"
    if positive_count > 0 and negative_count > 0:
        return "detector_disagreement"
    if positive_count > 1:
        return "multiple_detector_evidence"
    if positive_count == 1:
        return "single_detector_evidence"
    return "no_evidence"


def build_aggregate_events(window_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_session: dict[str, list[dict[str, Any]]] = {}
    for row in window_rows:
        by_session.setdefault(str(row["session_id"]), []).append(row)
    finding_events: list[dict[str, Any]] = []
    coverage_gap_regions: list[dict[str, Any]] = []
    for session_id, rows in sorted(by_session.items()):
        sorted_rows = sorted(rows, key=lambda item: int(item.get("window_number", item.get("window_index", 0))))
        finding_events.extend(
            _group_rows(
                sorted_rows,
                lambda row: bool(row["detector_ids"]["positive"]),
                lambda row: (
                    row["aggregate_evidence_status"]["state"],
                    tuple(row["detector_ids"]["positive"]),
                    tuple(row["detector_ids"]["negative"]),
                ),
                event_id_prefix=f"{session_id}:finding",
                event_type_fn=finding_event_type,
            )
        )
        coverage_gap_regions.extend(
            _group_rows(
                sorted_rows,
                lambda row: row["coverage"]["status"] != "full",
                lambda row: (
                    row["coverage"]["status"],
                    tuple(row["detector_ids"]["not_applicable"]),
                    tuple(row["detector_ids"]["unavailable"]),
                ),
                event_id_prefix=f"{session_id}:coverage",
                event_type_fn=lambda _: "coverage_gap_region",
            )
        )
    all_events = [*finding_events, *coverage_gap_regions]
    return {
        "schema_version": "multi-detector-event-aggregation-v1",
        "finding_events": finding_events,
        "coverage_gap_regions": coverage_gap_regions,
        "summary": {
            "finding_event_count": len(finding_events),
            "coverage_gap_region_count": len(coverage_gap_regions),
            "event_type_counts": dict(sorted(Counter(event["event_type"] for event in all_events).items())),
        },
    }


def finding_event_type(row: dict[str, Any]) -> str:
    state = row["aggregate_evidence_status"]["state"]
    if state == "detector_disagreement":
        return "disagreement_region"
    if state == "multiple_detector_evidence":
        return "overlapping_detector_event"
    return "detector_specific_event"


def summarize_aggregate_windows(window_rows: list[dict[str, Any]]) -> dict[str, Any]:
    states = Counter(row["aggregate_evidence_status"]["state"] for row in window_rows)
    coverage = Counter(row["coverage"]["status"] for row in window_rows)
    applicable_distribution = Counter(str(row["counts"]["applicable_detector_count"]) for row in window_rows)
    detector_counts: dict[str, Counter[str]] = {}
    for row in window_rows:
        for finding in row["detector_findings"]:
            detector_counts.setdefault(finding["detector_id"], Counter())[finding["finding"]] += 1

    def _state_count(state: str) -> int:
        return int(states.get(state, 0))

    if_only = sum(
        1
        for row in window_rows
        if row["detector_ids"]["positive"] == [ISOLATION_FOREST_DETECTOR_ID]
    )
    contextual_only = sum(
        1
        for row in window_rows
        if row["detector_ids"]["positive"] == [CONTEXTUAL_BATTERY_DETECTOR_ID]
    )
    both_applicable_agree = sum(
        1
        for row in window_rows
        if row["coverage"]["status"] == "full"
        and row["aggregate_evidence_status"]["state"] in {"no_evidence", "multiple_detector_evidence"}
    )
    return {
        "window_count": len(window_rows),
        "evidence_state_counts": dict(sorted(states.items())),
        "coverage_status_counts": dict(sorted(coverage.items())),
        "applicable_detector_count_distribution": dict(sorted(applicable_distribution.items())),
        "detector_finding_counts": {
            detector_id: dict(sorted(counter.items()))
            for detector_id, counter in sorted(detector_counts.items())
        },
        "if_only_finding_windows": if_only,
        "contextual_only_finding_windows": contextual_only,
        "overlapping_finding_windows": _state_count("multiple_detector_evidence"),
        "disagreement_windows": _state_count("detector_disagreement"),
        "insufficient_coverage_windows": _state_count("insufficient_coverage"),
        "only_one_detector_applicable_windows": int(applicable_distribution.get("1", 0)),
        "both_applicable_agreement_windows": both_applicable_agree,
        "no_evidence_windows": _state_count("no_evidence"),
        "no_evidence_is_verified_healthy": False,
        "score_fusion_used": False,
    }


def representative_events(events: dict[str, Any], limit_per_type: int = 3) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for event in [*events.get("finding_events", []), *events.get("coverage_gap_regions", [])]:
        event_type = event["event_type"]
        if counts[event_type] >= limit_per_type:
            continue
        selected.append(event)
        counts[event_type] += 1
    return selected


def window_identity(
    feature_row: pd.Series,
    row_number: int,
    frame_data: pd.DataFrame | None,
) -> dict[str, Any]:
    start_frame = _int_or_none(feature_row.get("start_frame_index"))
    end_frame = _int_or_none(feature_row.get("end_frame_index"))
    start_time_ms, end_time_ms = frame_times(frame_data, start_frame, end_frame)
    return {
        "window_number": row_number,
        "window_index": _int_or_none(feature_row.get("window_index")) or row_number,
        "start_frame_index": start_frame,
        "end_frame_index": end_frame,
        "start_time_ms": start_time_ms,
        "end_time_ms": end_time_ms,
        "duration_ms": _float_or_none(feature_row.get("duration_ms")),
    }


def telemetry_snapshot(feature_row: pd.Series, columns: tuple[str, ...]) -> dict[str, Any]:
    return {
        column: _float_or_none(feature_row.get(column))
        for column in columns
        if column in feature_row
    }


def frame_times(
    frame_data: pd.DataFrame | None,
    start_frame: int | None,
    end_frame: int | None,
) -> tuple[float | None, float | None]:
    if frame_data is None or frame_data.empty or "frame_index" not in frame_data or "relative_time_ms" not in frame_data:
        return None, None
    indexed = frame_data.set_index("frame_index")
    start_time = indexed.loc[start_frame, "relative_time_ms"] if start_frame in indexed.index else None
    end_time = indexed.loc[end_frame, "relative_time_ms"] if end_frame in indexed.index else None
    return _float_or_none(start_time), _float_or_none(end_time)


def _group_rows(
    rows: list[dict[str, Any]],
    predicate: Any,
    key_fn: Any,
    *,
    event_id_prefix: str,
    event_type_fn: Any,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    active_key: Any = None
    for row in [*rows, None]:
        matches = row is not None and predicate(row)
        key = key_fn(row) if matches else None
        contiguous = bool(
            active
            and row is not None
            and int(row["window_number"]) == int(active[-1]["window_number"]) + 1
        )
        if matches and active and key == active_key and contiguous:
            active.append(row)
            continue
        if active:
            events.append(_event_from_rows(active, event_id=f"{event_id_prefix}-{len(events) + 1:04d}", event_type=event_type_fn(active[0])))
        active = [row] if matches else []
        active_key = key
    return events


def _event_from_rows(rows: list[dict[str, Any]], *, event_id: str, event_type: str) -> dict[str, Any]:
    first = rows[0]
    last = rows[-1]
    detector_versions: dict[str, str | None] = {}
    for finding in first["detector_findings"]:
        detector_versions[finding["detector_id"]] = finding.get("model_version")
    return {
        "event_id": event_id,
        "event_type": event_type,
        "session_id": first["session_id"],
        "start_window_index": first["window_index"],
        "end_window_index": last["window_index"],
        "start_window_number": first["window_number"],
        "end_window_number": last["window_number"],
        "start_frame_index": first.get("start_frame_index"),
        "end_frame_index": last.get("end_frame_index"),
        "start_time_ms": first.get("start_time_ms"),
        "end_time_ms": last.get("end_time_ms"),
        "window_count": len(rows),
        "duration_ms": _event_duration(rows),
        "evidence_state": first["aggregate_evidence_status"]["state"],
        "coverage_status": first["coverage"]["status"],
        "positive_detector_ids": list(first["detector_ids"]["positive"]),
        "negative_detector_ids": list(first["detector_ids"]["negative"]),
        "not_applicable_detector_ids": list(first["detector_ids"]["not_applicable"]),
        "unavailable_detector_ids": list(first["detector_ids"]["unavailable"]),
        "detector_versions": detector_versions,
        "source_window_range": {
            "start_window_number": first["window_number"],
            "end_window_number": last["window_number"],
        },
        "representative_window": first,
        "interpretation": "aggregate evidence relationship only; not a diagnostic root-cause claim",
    }


def _event_duration(rows: list[dict[str, Any]]) -> float | None:
    if rows[0].get("start_time_ms") is not None and rows[-1].get("end_time_ms") is not None:
        return round(max(0.0, float(rows[-1]["end_time_ms"]) - float(rows[0]["start_time_ms"])), 6)
    durations = [_float_or_none(row.get("duration_ms")) for row in rows]
    if all(duration is not None for duration in durations):
        return round(sum(float(duration) for duration in durations), 6)
    return None


def _applicability_reason(payload: dict[str, Any], evidence: dict[str, Any]) -> str:
    if payload.get("reason"):
        return str(payload["reason"])
    nested = evidence.get("contextual_evidence")
    if isinstance(nested, dict) and nested.get("reason"):
        return str(nested["reason"])
    for key in ("contextual_status", "temporal_status", "rpm_stability_status"):
        if evidence.get(key):
            return str(evidence[key])
    return "prediction_unavailable"


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return bool(value)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(resolved) else round(resolved, 10)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "item"):
        return value.item()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value
