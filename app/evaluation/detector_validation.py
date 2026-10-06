from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.evaluation.model_harness_evaluation import EvaluationPaths, default_paths, write_json
from app.ml.contextual_detector import CONTEXTUAL_BATTERY_DETECTOR_ID
from app.ml.evidence_aggregation import ACTIVE_H5_DETECTOR_IDS
from app.ml.iforest_detector import ISOLATION_FOREST_DETECTOR_ID


VALIDATION_LABELS = (
    "observed_normal_behavior",
    "observed_unusual_behavior",
    "controlled_condition",
    "data_artifact",
    "inconclusive",
)
CONTROLLED_LABEL_SOURCE = "explicit_demo_seed_generation"


def default_h6_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h6")


def run_all(
    paths: EvaluationPaths,
    *,
    h2_dir: Path | None = None,
    h5_dir: Path | None = None,
    h3_dir: Path | None = None,
) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h2_dir = (h2_dir or paths.repo_root / "data" / "evaluation" / "h2").resolve()
    resolved_h5_dir = (h5_dir or paths.repo_root / "data" / "evaluation" / "h5").resolve()
    resolved_h3_dir = (h3_dir or paths.repo_root / "data" / "evaluation" / "h3").resolve()
    h2_context = load_h2_context(resolved_h2_dir)
    h5_benchmark = read_json(resolved_h5_dir / "aggregate_benchmark_results.json")
    h5_review = read_json(resolved_h5_dir / "aggregate_event_review.json")
    h5_windows = read_jsonl(resolved_h5_dir / "aggregate_window_results.jsonl")
    h3_manifest = read_json(resolved_h3_dir / "evaluation_manifest.json") if (resolved_h3_dir / "evaluation_manifest.json").exists() else {"sessions": []}

    label_schema = build_label_schema()
    validation_manifest = build_validation_manifest(h5_review, h5_windows, h3_manifest, h2_context)
    controlled_plan = build_controlled_validation_plan()
    blind_review = build_blind_review_manifest(validation_manifest)
    metrics = build_validation_metrics(h5_benchmark, validation_manifest)
    decision = build_detector_decision_report(validation_manifest, metrics)

    outputs = {
        "label_schema": paths.output_dir / "validation_label_schema.json",
        "validation_manifest": paths.output_dir / "detector_validation_manifest.json",
        "blind_review": paths.output_dir / "blind_review_manifest.json",
        "controlled_plan": paths.output_dir / "controlled_validation_plan.json",
        "metrics": paths.output_dir / "validation_metrics.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["label_schema"], label_schema)
    write_json(outputs["validation_manifest"], validation_manifest)
    write_json(outputs["blind_review"], blind_review)
    write_json(outputs["controlled_plan"], controlled_plan)
    write_json(outputs["metrics"], metrics)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], validation_manifest, metrics, decision)
    return outputs


def load_h2_context(h2_dir: Path) -> dict[str, Any]:
    manifest_path = h2_dir / "evaluation_manifest.json"
    parity_path = h2_dir / "parity_report.json"
    benchmark_path = h2_dir / "benchmark_results.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    parity = read_json(parity_path) if parity_path.exists() else {}
    benchmark = read_json(benchmark_path) if benchmark_path.exists() else {}
    return {
        "source_files": {
            "h2_evaluation_manifest": "data/evaluation/h2/evaluation_manifest.json",
            "h2_parity_report": "data/evaluation/h2/parity_report.json",
            "h2_benchmark_results": "data/evaluation/h2/benchmark_results.json",
        },
        "parity": {
            "passed": parity.get("passed"),
            "historical_source": parity.get("historical_source"),
            "public_api_compatibility": parity.get("public_api_compatibility"),
            "limitations": parity.get("limitations", []),
        },
        "dataset": {
            "session_count": len(manifest.get("sessions", [])),
            "evaluation_ready_session_count": manifest.get("summary", {}).get("evaluation_ready_session_count")
            or manifest.get("summary", {}).get("evaluation_ready_canonical_session_count"),
            "calibration_dataset_count": len(manifest.get("calibration_datasets", [])),
            "raw_real_run_source_count": len(manifest.get("raw_real_run_sources", [])),
            "label_metrics": benchmark.get("label_metrics"),
        },
    }


def build_label_schema() -> dict[str, Any]:
    return {
        "schema_version": "active-detector-validation-label-schema-v1",
        "allowed_labels": list(VALIDATION_LABELS),
        "evidence_sources": [
            "controlled_operating_experiment",
            "explicit_demo_seed_generation",
            "independent_physical_observation",
            "external_measurement",
            "known_operator_action",
            "expert_review",
            "documented_acquisition_artifact",
            "unavailable",
        ],
        "policy": {
            "detector_predictions_are_ground_truth": False,
            "detector_anomaly_as_label_source_allowed": False,
            "unlabeled_events_remain_unlabeled": True,
            "metrics_require_independent_or_controlled_labels": True,
            "expert_only_labels_are_separated_from_controlled_cases": True,
        },
        "label_definitions": {
            "observed_normal_behavior": "Expert or independent evidence indicates normal operation for the reviewed region.",
            "observed_unusual_behavior": "Expert or independent evidence indicates unusual observed behavior, without assigning root cause.",
            "controlled_condition": "A known controlled/synthetic operating condition was introduced independently of detector output.",
            "data_artifact": "Documented acquisition or data-quality issue explains the reviewed region.",
            "inconclusive": "Available evidence is insufficient for a validation label.",
        },
    }


def build_validation_manifest(
    h5_review: dict[str, Any],
    h5_windows: list[dict[str, Any]],
    h3_manifest: dict[str, Any],
    h2_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = list(h5_review.get("items", []))
    no_evidence_events = build_no_evidence_events(h5_windows)
    manifest_by_id = {item["session_id"]: item for item in h3_manifest.get("sessions", [])}

    selected: list[tuple[str, dict[str, Any]]] = []
    selected.extend((category, item) for category, item in pick_representatives(events, "if_only", 6))
    selected.extend((category, item) for category, item in pick_representatives(events, "contextual_only", 6))
    selected.extend((category, item) for category, item in pick_representatives(events, "overlapping", 6))
    selected.extend((category, item) for category, item in pick_representatives(events, "disagreement", 8))
    selected.extend((category, item) for category, item in pick_representatives(events, "insufficient_coverage", 6))
    selected.extend((category, item) for category, item in pick_no_evidence_representatives(no_evidence_events, 8))
    selected.extend((event_category(item.get("event", {})), item) for item in controlled_event_items(events))

    deduped: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for category, item in selected:
        key = item.get("review_item_id") or item.get("event", {}).get("event_id") or f"{category}:{len(seen)}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append((category, item))

    items = [
        validation_item(index, category, item, manifest_by_id)
        for index, (category, item) in enumerate(deduped, start=1)
    ]
    label_counts = Counter(item["independent_label"]["label"] for item in items)
    category_counts = Counter(item["validation_region_type"] for item in items)
    detector_positive_counts: Counter[str] = Counter()
    for item in items:
        for detector_id in item["detector_outputs"]["positive_detector_ids"]:
            detector_positive_counts[detector_id] += 1

    return {
        "schema_version": "active-detector-validation-manifest-v1",
        "source": {
            **(h2_context or {}).get("source_files", {}),
            "h5_event_review": "data/evaluation/h5/aggregate_event_review.json",
            "h5_window_results": "data/evaluation/h5/aggregate_window_results.jsonl",
            "h3_manifest": "data/evaluation/h3/evaluation_manifest.json",
            "h3_contextual_reference": "data/evaluation/h3/contextual_reference.json",
            "h3_1_disagreements": "data/evaluation/h3_1/disagreements.jsonl",
            "h4_decision": "data/evaluation/h4/decision_report.json",
            "h4_1_target_revision": "data/evaluation/h4_1/candidate_selection.json",
        },
        "upstream_h2_foundation": (h2_context or {}),
        "active_detector_ids": list(ACTIVE_H5_DETECTOR_IDS),
        "rejected_temporal_candidates_excluded": ["temporal_battery_shift", "temporal_rpm_stability"],
        "label_policy": {
            "detector_predictions_used_as_labels": False,
            "controlled_or_independent_labels_only_for_metrics": True,
            "unlabeled_events_remain_unlabeled": True,
        },
        "summary": {
            "item_count": len(items),
            "validation_region_type_counts": dict(sorted(category_counts.items())),
            "independent_label_counts": dict(sorted(label_counts.items())),
            "detector_positive_item_counts": dict(sorted(detector_positive_counts.items())),
        },
        "items": items,
    }


def event_category(event: dict[str, Any]) -> str:
    positives = list(event.get("positive_detector_ids") or [])
    event_type = event.get("event_type")
    if event_type == "coverage_gap_region" or (event.get("coverage_status") == "none" and not positives):
        return "insufficient_coverage_region"
    if event_type == "no_evidence_region" or event.get("evidence_state") == "no_evidence":
        return "no_evidence_region"
    if set(positives) == set(ACTIVE_H5_DETECTOR_IDS):
        return "overlapping_event"
    if positives == [ISOLATION_FOREST_DETECTOR_ID]:
        return "isolation_forest_only_event"
    if positives == [CONTEXTUAL_BATTERY_DETECTOR_ID]:
        return "contextual_only_event"
    if event_type == "disagreement_region":
        return "disagreement_region"
    return str(event_type or "unclassified")


def pick_representatives(events: list[dict[str, Any]], category: str, limit: int) -> list[tuple[str, dict[str, Any]]]:
    candidates = [item for item in events if event_category(item.get("event", {})) == normalize_category(category)]
    if category == "disagreement":
        candidates = [item for item in events if item.get("event", {}).get("event_type") == "disagreement_region"]
    if category == "insufficient_coverage":
        candidates = [item for item in events if item.get("event", {}).get("event_type") == "coverage_gap_region"]
    ordered = sorted(candidates, key=representative_sort_key)
    return [(normalize_category(category), item) for item in ordered[:limit]]


def pick_no_evidence_representatives(events: list[dict[str, Any]], limit: int) -> list[tuple[str, dict[str, Any]]]:
    ordered = sorted(events, key=representative_sort_key)
    return [("no_evidence_region", item) for item in ordered[:limit]]


def normalize_category(category: str) -> str:
    return {
        "if_only": "isolation_forest_only_event",
        "contextual_only": "contextual_only_event",
        "overlapping": "overlapping_event",
        "disagreement": "disagreement_region",
        "insufficient_coverage": "insufficient_coverage_region",
    }.get(category, category)


def representative_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    event = item.get("event", {})
    split = item.get("provenance", {}).get("training_evaluation_split", {})
    controlled = split.get("label_source") == CONTROLLED_LABEL_SOURCE
    label = split.get("label") or ""
    return (
        0 if controlled else 1,
        -int(event.get("window_count") or 0),
        f"{label}:{event.get('session_id', '')}:{event.get('start_window_number', 0)}",
    )


def controlled_event_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in events
        if item.get("event", {}).get("event_type") != "coverage_gap_region"
        and item.get("provenance", {}).get("training_evaluation_split", {}).get("label_source") == CONTROLLED_LABEL_SOURCE
    ]


def validation_item(
    index: int,
    category: str,
    source_item: dict[str, Any],
    manifest_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    event = source_item["event"]
    provenance = source_item.get("provenance") or event_provenance_from_manifest(event.get("session_id"), manifest_by_id)
    split = provenance.get("training_evaluation_split") or {}
    label = independent_label(split)
    representative = event.get("representative_window", {})
    detector_outputs = detector_output_summary(event, representative)
    return {
        "validation_item_id": f"h6-validation-{index:04d}",
        "source_h5_review_item_id": source_item.get("review_item_id"),
        "validation_region_type": category,
        "session_id": event.get("session_id"),
        "provenance": provenance,
        "event": event_summary(event),
        "detector_outputs": detector_outputs,
        "telemetry_evidence": telemetry_evidence(event, representative),
        "independent_label": label,
        "metric_eligibility": metric_eligibility(label, detector_outputs),
        "review_annotation": empty_review_annotation(),
    }


def event_summary(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "evidence_state": event.get("evidence_state"),
        "coverage_status": event.get("coverage_status"),
        "start_window_number": event.get("start_window_number"),
        "end_window_number": event.get("end_window_number"),
        "start_window_index": event.get("start_window_index"),
        "end_window_index": event.get("end_window_index"),
        "start_frame_index": event.get("start_frame_index"),
        "end_frame_index": event.get("end_frame_index"),
        "start_time_ms": event.get("start_time_ms"),
        "end_time_ms": event.get("end_time_ms"),
        "duration_ms": event.get("duration_ms"),
        "window_count": event.get("window_count"),
        "source_window_range": event.get("source_window_range"),
    }


def detector_output_summary(event: dict[str, Any], representative: dict[str, Any]) -> dict[str, Any]:
    findings = representative.get("detector_findings", [])
    by_detector = {}
    for finding in findings:
        by_detector[finding["detector_id"]] = {
            "model_version": finding.get("model_version"),
            "execution_status": finding.get("execution_status"),
            "applicable": finding.get("applicable"),
            "finding": finding.get("finding"),
            "is_anomaly": finding.get("is_anomaly"),
            "prediction": finding.get("prediction"),
            "detector_local_score": finding.get("detector_local_score"),
            "detector_local_threshold": finding.get("detector_local_threshold"),
            "evidence": finding.get("evidence"),
            "applicability_reason": finding.get("applicability_reason"),
            "missing_features": finding.get("missing_features"),
            "error": finding.get("error"),
        }
    return {
        "positive_detector_ids": list(event.get("positive_detector_ids") or []),
        "negative_detector_ids": list(event.get("negative_detector_ids") or []),
        "not_applicable_detector_ids": list(event.get("not_applicable_detector_ids") or []),
        "unavailable_detector_ids": list(event.get("unavailable_detector_ids") or []),
        "by_detector": by_detector,
    }


def telemetry_evidence(event: dict[str, Any], representative: dict[str, Any]) -> dict[str, Any]:
    findings = representative.get("detector_findings", [])
    context = None
    contextual_top = None
    unusual_features = None
    for finding in findings:
        evidence = finding.get("evidence") or {}
        if finding.get("detector_id") == CONTEXTUAL_BATTERY_DETECTOR_ID:
            context = evidence.get("operating_context")
            contextual_top = evidence.get("contextual_evidence", {}).get("top_deviation") if isinstance(evidence.get("contextual_evidence"), dict) else None
        if finding.get("detector_id") == ISOLATION_FOREST_DETECTOR_ID:
            unusual_features = evidence.get("most_unusual_features")
    return {
        "telemetry": representative.get("telemetry", {}),
        "operating_context": context,
        "contextual_top_deviation": contextual_top,
        "isolation_forest_most_unusual_features": unusual_features,
        "timing": {
            "start_time_ms": event.get("start_time_ms"),
            "end_time_ms": event.get("end_time_ms"),
            "duration_ms": event.get("duration_ms"),
        },
    }


def independent_label(split: dict[str, Any]) -> dict[str, Any]:
    label = split.get("label")
    source = split.get("label_source")
    notes = split.get("notes")
    if source == CONTROLLED_LABEL_SOURCE:
        condition = controlled_condition_type(notes or label or "")
        return {
            "label": "controlled_condition",
            "evidence_source": "explicit_demo_seed_generation",
            "source_label": label,
            "source_notes": notes,
            "controlled_condition_type": condition,
            "expert_only": False,
            "suitable_for_controlled_metrics": True,
            "assigned_from_detector_output": False,
        }
    if source == "human_review" and label == "normal_ride":
        return {
            "label": "observed_normal_behavior",
            "evidence_source": "expert_review",
            "source_label": label,
            "source_notes": notes,
            "expert_only": True,
            "suitable_for_controlled_metrics": False,
            "assigned_from_detector_output": False,
        }
    if source == "human_review" and label in {"battery_low_candidate", "high_rpm_candidate", "warmup_candidate"}:
        return {
            "label": "observed_unusual_behavior",
            "evidence_source": "expert_review",
            "source_label": label,
            "source_notes": notes,
            "expert_only": True,
            "suitable_for_controlled_metrics": False,
            "assigned_from_detector_output": False,
        }
    return {
        "label": "inconclusive",
        "evidence_source": source or "unavailable",
        "source_label": label,
        "source_notes": notes,
        "expert_only": False,
        "suitable_for_controlled_metrics": False,
        "assigned_from_detector_output": False,
    }


def controlled_condition_type(text: str) -> str:
    lower = text.lower()
    if "low battery" in lower:
        return "synthetic_low_battery"
    if "high rpm" in lower or "load" in lower:
        return "synthetic_high_rpm_load"
    if "high ect" in lower or "overheat" in lower:
        return "synthetic_high_ect"
    return "synthetic_known_abnormal"


def metric_eligibility(label: dict[str, Any], detector_outputs: dict[str, Any]) -> dict[str, Any]:
    controlled = label.get("label") == "controlled_condition"
    condition = label.get("controlled_condition_type")
    return {
        "eligible_for_controlled_case_recall": controlled,
        "eligible_for_event_precision": False,
        "event_precision_reason": "No independently verified detector-positive normal/artifact label is available for this item.",
        "target_relevant_detectors": target_relevant_detectors(condition) if controlled else [],
        "detector_predictions_available": bool(detector_outputs.get("by_detector")),
    }


def target_relevant_detectors(condition: str | None) -> list[str]:
    if condition == "synthetic_low_battery":
        return [ISOLATION_FOREST_DETECTOR_ID, CONTEXTUAL_BATTERY_DETECTOR_ID]
    if condition in {"synthetic_high_rpm_load", "synthetic_high_ect", "synthetic_known_abnormal"}:
        return [ISOLATION_FOREST_DETECTOR_ID]
    return []


def build_no_evidence_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_session[str(row.get("session_id"))].append(row)
    items: list[dict[str, Any]] = []
    for session_id, session_rows in sorted(by_session.items()):
        sorted_rows = sorted(session_rows, key=lambda row: int(row.get("window_number", row.get("window_index", 0))))
        active: list[dict[str, Any]] = []
        for row in [*sorted_rows, None]:
            matches = row is not None and row.get("aggregate_evidence_status", {}).get("state") == "no_evidence"
            contiguous = bool(
                active
                and row is not None
                and int(row["window_number"]) == int(active[-1]["window_number"]) + 1
            )
            if matches and (not active or contiguous):
                active.append(row)
                continue
            if active:
                event = no_evidence_event(session_id, active, len(items) + 1)
                items.append({"review_item_id": f"h5-derived-no-evidence-{len(items) + 1:04d}", "event": event})
            active = [row] if matches else []
    return items


def no_evidence_event(session_id: str, rows: list[dict[str, Any]], index: int) -> dict[str, Any]:
    first = rows[0]
    last = rows[-1]
    return {
        "event_id": f"{session_id}:no-evidence-{index:04d}",
        "event_type": "no_evidence_region",
        "session_id": session_id,
        "evidence_state": "no_evidence",
        "coverage_status": first.get("coverage", {}).get("status"),
        "start_window_number": first.get("window_number"),
        "end_window_number": last.get("window_number"),
        "start_window_index": first.get("window_index"),
        "end_window_index": last.get("window_index"),
        "start_frame_index": first.get("start_frame_index"),
        "end_frame_index": last.get("end_frame_index"),
        "start_time_ms": first.get("start_time_ms"),
        "end_time_ms": last.get("end_time_ms"),
        "duration_ms": event_duration(rows),
        "window_count": len(rows),
        "source_window_range": {
            "start_window_number": first.get("window_number"),
            "end_window_number": last.get("window_number"),
        },
        "positive_detector_ids": [],
        "negative_detector_ids": list(first.get("detector_ids", {}).get("negative", [])),
        "not_applicable_detector_ids": list(first.get("detector_ids", {}).get("not_applicable", [])),
        "unavailable_detector_ids": list(first.get("detector_ids", {}).get("unavailable", [])),
        "representative_window": first,
    }


def event_duration(rows: list[dict[str, Any]]) -> float | None:
    if rows[0].get("start_time_ms") is not None and rows[-1].get("end_time_ms") is not None:
        return round(float(rows[-1]["end_time_ms"]) - float(rows[0]["start_time_ms"]), 6)
    durations = [row.get("duration_ms") for row in rows if row.get("duration_ms") is not None]
    return round(sum(float(value) for value in durations), 6) if durations else None


def event_provenance_from_manifest(session_id: str | None, manifest_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = manifest_by_id.get(str(session_id), {})
    return {
        "session_id": item.get("session_id", session_id),
        "path": item.get("path"),
        "capture_provenance": item.get("capture_provenance"),
        "decoder": item.get("decoder"),
        "telemetry_schema_version": item.get("telemetry_schema_version"),
        "training_evaluation_split": item.get("training_evaluation_split", {}),
        "artifacts": item.get("artifacts"),
    }


def empty_review_annotation() -> dict[str, Any]:
    return {
        "reviewer": None,
        "reviewed_at": None,
        "label": None,
        "evidence_source": None,
        "uncertainty": None,
        "comments": None,
        "fault_label_assigned": False,
    }


def build_controlled_validation_plan() -> dict[str, Any]:
    return {
        "schema_version": "controlled-validation-plan-v1",
        "status": "proposed_not_executed",
        "safety_policy": [
            "Do not introduce unsafe mechanical or electrical faults.",
            "Prefer reversible operating changes and repeated nominal baselines.",
            "Record raw capture, ECU profile, environmental notes, operator action and timestamps.",
        ],
        "experiments": [
            {
                "experiment_id": "stable-idle-reference",
                "purpose": "Verify no-evidence/low-alert behavior during stable idle/reference operation.",
                "target_detectors": list(ACTIVE_H5_DETECTOR_IDS),
                "procedure": "Warm engine normally, hold stable idle, avoid accessory toggles, record at least 5 minutes.",
                "required_provenance": ["vehicle", "ECU profile", "ambient conditions", "start/end time", "operator notes"],
            },
            {
                "experiment_id": "controlled-throttle-transitions",
                "purpose": "Check IF response to repeatable operating-state transitions without treating them as faults.",
                "target_detectors": [ISOLATION_FOREST_DETECTOR_ID],
                "procedure": "Run several gentle throttle transitions from closed to low/mid load in a safe stationary or controlled setting.",
                "required_provenance": ["operator action timestamps", "RPM/TPS traces", "capture hash"],
            },
            {
                "experiment_id": "safe-electrical-load-toggle",
                "purpose": "Check contextual battery detector response to independently observed voltage changes.",
                "target_detectors": [CONTEXTUAL_BATTERY_DETECTOR_ID],
                "procedure": "Toggle safe factory electrical loads where appropriate and record external voltage if available.",
                "required_provenance": ["load state timestamps", "external voltage measurement if available", "battery/charging notes"],
            },
            {
                "experiment_id": "repeated-nominal-runs",
                "purpose": "Estimate repeatability and normal operating variation without using detector output as truth.",
                "target_detectors": list(ACTIVE_H5_DETECTOR_IDS),
                "procedure": "Repeat the same route/idle protocol across sessions under similar conditions.",
                "required_provenance": ["route/protocol", "operator", "ambient conditions", "capture hashes"],
            },
        ],
    }


def build_blind_review_manifest(validation_manifest: dict[str, Any]) -> dict[str, Any]:
    items = []
    for index, item in enumerate(validation_manifest["items"], start=1):
        items.append(
            {
                "blind_review_id": f"h6-blind-review-{index:04d}",
                "source_validation_item_id": item["validation_item_id"],
                "masked_detector_outputs": True,
                "masked_existing_label": True,
                "session_id": item["session_id"],
                "provenance_for_traceability": {
                    "path": item["provenance"].get("path"),
                    "artifacts": item["provenance"].get("artifacts"),
                    "telemetry_schema_version": item["provenance"].get("telemetry_schema_version"),
                },
                "event": item["event"],
                "telemetry_evidence": item["telemetry_evidence"],
                "review_form": {
                    "allowed_labels": list(VALIDATION_LABELS),
                    "reviewer_judgment": None,
                    "evidence": None,
                    "uncertainty": None,
                    "comments": None,
                },
            }
        )
    return {
        "schema_version": "active-detector-blind-review-manifest-v1",
        "purpose": "Expert review packet with detector outputs and existing labels masked where practical.",
        "detector_outputs_available_in_source_manifest": True,
        "item_count": len(items),
        "items": items,
    }


def build_validation_metrics(h5_benchmark: dict[str, Any], validation_manifest: dict[str, Any]) -> dict[str, Any]:
    controlled_cases = controlled_case_results(h5_benchmark)
    detector_metrics = {
        detector_id: detector_controlled_metrics(detector_id, controlled_cases)
        for detector_id in ACTIVE_H5_DETECTOR_IDS
    }
    expert_only = [
        item for item in validation_manifest["items"]
        if item["independent_label"].get("expert_only")
    ]
    return {
        "schema_version": "active-detector-validation-metrics-v1",
        "metric_policy": {
            "unlabeled_events_treated_as_normal": False,
            "expert_only_labels_used_for_precision_recall": False,
            "detector_outputs_used_as_ground_truth": False,
        },
        "controlled_cases": controlled_cases,
        "detectors": detector_metrics,
        "expert_only_review_label_counts": dict(sorted(Counter(item["independent_label"]["label"] for item in expert_only).items())),
        "not_computed": {
            "event_level_precision": "No independently verified detector-positive normal/artifact event labels are available.",
            "event_level_recall": "Controlled labels are session/condition-level in current data, not exhaustive event-level annotations.",
            "false_alerts_per_hour": "No verified normal operating-hour set is available; alert-rate can be counted but not called false alerts.",
            "agreement_with_expert_labels": "Blind expert review packet is prepared but not completed.",
        },
    }


def controlled_case_results(h5_benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    for session in h5_benchmark.get("sessions", []):
        split = session.get("training_evaluation_split") or {}
        if split.get("label_source") != CONTROLLED_LABEL_SOURCE:
            continue
        label = independent_label(split)
        condition = label.get("controlled_condition_type")
        target_detectors = target_relevant_detectors(condition)
        detectors = {detector.get("detector_id"): detector for detector in session.get("detectors", [])}
        responses = {}
        for detector_id in ACTIVE_H5_DETECTOR_IDS:
            detector = detectors.get(detector_id, {})
            events = (detector.get("events") or {}).get("items") or []
            first_event = events[0] if events else None
            responses[detector_id] = {
                "target_relevant": detector_id in target_detectors,
                "detected": bool(events),
                "event_count": len(events),
                "first_event": first_event,
                "detection_latency_windows": (
                    int(first_event.get("start_window_index", 0)) if first_event is not None and detector_id in target_detectors else None
                ),
                "detection_latency_ms": None,
                "detection_latency_ms_reason": "Condition start timestamps are not independently preserved in the current benchmark event payload.",
                "status": detector.get("status"),
            }
        cases.append(
            {
                "session_id": session["session_id"],
                "source_label": split.get("label"),
                "source_notes": split.get("notes"),
                "controlled_condition_type": condition,
                "target_relevant_detectors": target_detectors,
                "responses": responses,
            }
        )
    return cases


def detector_controlled_metrics(detector_id: str, controlled_cases: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [case for case in controlled_cases if detector_id in case["target_relevant_detectors"]]
    detected = [case for case in eligible if case["responses"][detector_id]["detected"]]
    missed = [case for case in eligible if not case["responses"][detector_id]["detected"]]
    latencies = [
        case["responses"][detector_id]["detection_latency_windows"]
        for case in detected
        if case["responses"][detector_id]["detection_latency_windows"] is not None
    ]
    return {
        "controlled_condition_recall": {
            "computed": bool(eligible),
            "eligible_case_count": len(eligible),
            "detected_case_count": len(detected),
            "missed_case_count": len(missed),
            "value": None if not eligible else round(len(detected) / len(eligible), 6),
            "eligible_sessions": [case["session_id"] for case in eligible],
            "missed_sessions": [case["session_id"] for case in missed],
        },
        "detection_latency_windows": numeric_summary(latencies),
        "event_level_precision": {
            "computed": False,
            "reason": "No independently verified detector-positive normal/artifact event labels are available.",
        },
        "false_alerts_per_hour": {
            "computed": False,
            "reason": "No verified normal operating-hour denominator is available.",
        },
    }


def numeric_summary(values: list[Any]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {"count": len(clean), "min": min(clean), "median": statistics.median(clean), "max": max(clean)}


def build_detector_decision_report(validation_manifest: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    decisions = {}
    for detector_id in ACTIVE_H5_DETECTOR_IDS:
        recall = metrics["detectors"][detector_id]["controlled_condition_recall"]
        if not recall["computed"]:
            recommendation = "insufficient_evidence"
        elif recall["value"] == 1.0:
            recommendation = "retain_with_limitations"
        elif recall["value"] and recall["value"] > 0:
            recommendation = "revise"
        else:
            recommendation = "reject"
        decisions[detector_id] = detector_decision(detector_id, recommendation, recall, validation_manifest)
    return {
        "schema_version": "active-detector-validation-decision-v1",
        "production_behavior_changed": False,
        "drive_safe_behavior_changed": False,
        "detector_decisions": decisions,
        "ground_truth_limitations": [
            "Controlled labels are sparse and condition-level.",
            "Human-review labels are useful expert context but are not used as precision/recall ground truth.",
            "Most aggregate events remain inconclusive until blind/manual review is completed.",
        ],
    }


def detector_decision(
    detector_id: str,
    recommendation: str,
    recall: dict[str, Any],
    validation_manifest: dict[str, Any],
) -> dict[str, Any]:
    if detector_id == ISOLATION_FOREST_DETECTOR_ID:
        supported = "Broad multivariate response to available controlled abnormal sessions."
        unsupported = [
            "mechanical root-cause diagnosis",
            "fault probability",
            "clean distinction between unusual-but-normal operating behavior and faults without expert review",
        ]
        false_patterns = [
            "startup or engine-off constant-signal windows",
            "normal/high-load riding patterns that are uncommon relative to the baseline",
            "voltage-related regions also captured by contextual detector",
        ]
        blind_spots = ["contextual-only voltage deviations where IF remains negative"]
    else:
        supported = "Context-conditioned battery-voltage deviation in modeled RPM/TPS operating contexts."
        unsupported = [
            "high-RPM/load behavior without voltage deviation",
            "thermal anomalies",
            "unmodeled contexts and sessions missing required TPS raw features",
        ]
        false_patterns = [
            "first-window or short voltage dips requiring physical corroboration",
            "battery-low candidate labels without external voltage measurement remain expert-only",
        ]
        blind_spots = ["high-load/unmodeled contexts", "legacy sessions without required prepared features"]
    return {
        "decision": recommendation,
        "controlled_condition_recall": recall,
        "supported_phenomenon": supported,
        "not_supported_for": unsupported,
        "known_false_positive_patterns_or_ambiguities": false_patterns,
        "known_blind_spots": blind_spots,
        "ground_truth_limitations": [
            "No detector-positive verified-normal event set is available for precision.",
            "No root-cause labels are assigned from anomaly output.",
        ],
        "review_item_count": sum(
            1
            for item in validation_manifest["items"]
            if detector_id in item["detector_outputs"].get("positive_detector_ids", [])
        ),
    }


def write_markdown_report(
    path: Path,
    validation_manifest: dict[str, Any],
    metrics: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    summary = validation_manifest["summary"]
    h2_foundation = validation_manifest.get("upstream_h2_foundation", {})
    h2_dataset = h2_foundation.get("dataset", {})
    h2_parity = h2_foundation.get("parity", {})
    lines = [
        "# H6 Active Detector Validation Report",
        "",
        "## Dataset",
        "",
        f"- H2 parity passed: `{h2_parity.get('passed')}`",
        f"- H2 evaluation-ready sessions: `{h2_dataset.get('evaluation_ready_session_count')}`",
        f"- H2 label metrics computed: `{(h2_dataset.get('label_metrics') or {}).get('computed')}`",
        f"- Review items: `{summary['item_count']}`",
        f"- Region types: `{summary['validation_region_type_counts']}`",
        f"- Independent labels: `{summary['independent_label_counts']}`",
        "- Detector predictions and validation labels are stored separately.",
        "- Unlabeled and unverified events remain inconclusive.",
        "",
        "## Metrics",
        "",
    ]
    for detector_id, detector_metrics in metrics["detectors"].items():
        recall = detector_metrics["controlled_condition_recall"]
        lines.append(
            f"- `{detector_id}` controlled-condition recall: `{recall['value']}` "
            f"({recall['detected_case_count']}/{recall['eligible_case_count']} labeled cases)"
        )
    lines.extend(
        [
            f"- Not computed: `{metrics['not_computed']}`",
            "",
            "## Decisions",
            "",
        ]
    )
    for detector_id, payload in decision["detector_decisions"].items():
        lines.append(f"- `{detector_id}`: `{payload['decision']}`")
        lines.append(f"  Supported: {payload['supported_phenomenon']}")
    lines.extend(
        [
            "",
            "## Compatibility",
            "",
            "- Production inference remains unchanged.",
            "- DriveSafe behavior was not modified.",
            "- Rejected temporal candidates remain excluded.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows
