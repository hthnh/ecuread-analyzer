from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings
from app.evaluation.contextual_shadow import (
    default_h3_paths,
    duplicate_sample_groups,
    load_reference_feature_frames,
    load_session_frames,
    select_shadow_evaluation_sessions,
)
from app.evaluation.model_harness_evaluation import (
    EvaluationPaths,
    anomaly_events,
    default_paths,
    write_json,
)
from app.ml.contextual_detector import (
    CONTEXTUAL_BATTERY_DETECTOR_ID,
    DEFAULT_MIN_CONTEXT_WINDOWS,
    DEFAULT_ROBUST_Z_THRESHOLD,
    ContextualBatteryVoltageDetector,
    assign_operating_context,
    build_contextual_battery_reference,
)
from app.ml.harness import DetectorContext, ModelHarness


IFOREST_DETECTOR_ID = "isolation_forest"
NEAR_CONTEXTUAL_THRESHOLD_DELTA = 0.5
NEAR_IF_DECISION_THRESHOLD_DELTA = 0.025
TRANSITION_RADIUS_WINDOWS = 1
ALLOWED_REVIEW_ANNOTATIONS = [
    "plausible operating variation",
    "suspicious voltage behavior",
    "likely acquisition/data artifact",
    "insufficient evidence",
    "requires physical verification",
]


@dataclass(frozen=True, slots=True)
class CachedSession:
    item: dict[str, Any]
    frame_data: pd.DataFrame
    feature_frame: pd.DataFrame
    signal_columns: list[str]


def default_h31_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h3_1")


def run_all(paths: EvaluationPaths, h3_dir: Path | None = None) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h3_dir = (h3_dir or paths.repo_root / "data" / "evaluation" / "h3").resolve()
    h3_manifest = read_json(resolved_h3_dir / "evaluation_manifest.json")
    h3_reference = read_json(resolved_h3_dir / "contextual_reference.json")
    h3_benchmark = read_json(resolved_h3_dir / "shadow_benchmark_results.json")
    h3_window_rows = read_jsonl(resolved_h3_dir / "shadow_window_results.jsonl")

    settings = Settings(data_dir=paths.data_dir, model_dir=paths.model_dir)
    selected_items, _ = select_shadow_evaluation_sessions(h3_manifest)
    eval_cache = load_eval_cache(paths, settings, selected_items)
    reference_frames, reference_items = load_reference_feature_frames(paths, h3_manifest)

    reference_windows = build_reference_window_rows(reference_frames, reference_items, h3_manifest)
    reference_audit = build_reference_audit(paths, h3_manifest, h3_reference, h3_benchmark, reference_items)
    disagreements = classify_disagreements(h3_window_rows, eval_cache, h3_manifest, h3_reference)
    disagreement_summary = summarize_disagreements(disagreements)
    manual_review = build_manual_review_manifest(
        h3_benchmark,
        h3_window_rows,
        disagreements,
        eval_cache,
        h3_manifest,
        h3_reference,
    )
    stability = evaluate_reference_stability(h3_reference, reference_frames, reference_items, eval_cache)
    boundary = evaluate_context_boundaries(h3_window_rows, eval_cache)
    decision = build_decision(disagreement_summary, manual_review, stability, boundary)

    outputs = {
        "reference_audit": paths.output_dir / "reference_audit.json",
        "reference_windows": paths.output_dir / "reference_windows.jsonl",
        "disagreements": paths.output_dir / "disagreements.jsonl",
        "disagreement_summary": paths.output_dir / "disagreement_summary.json",
        "manual_review_manifest": paths.output_dir / "manual_review_manifest.json",
        "reference_stability": paths.output_dir / "reference_stability.json",
        "context_boundary_review": paths.output_dir / "context_boundary_review.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["reference_audit"], reference_audit)
    write_jsonl(outputs["reference_windows"], reference_windows)
    write_jsonl(outputs["disagreements"], disagreements)
    write_json(outputs["disagreement_summary"], disagreement_summary)
    write_json(outputs["manual_review_manifest"], manual_review)
    write_json(outputs["reference_stability"], stability)
    write_json(outputs["context_boundary_review"], boundary)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], reference_audit, disagreement_summary, manual_review, stability, boundary, decision)
    return outputs


def build_reference_audit(
    paths: EvaluationPaths,
    manifest: dict[str, Any],
    reference: dict[str, Any],
    benchmark: dict[str, Any],
    reference_items: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {item["session_id"]: item for item in manifest["sessions"]}
    reference_ids = set(reference.get("reference_session_ids", []))
    reference_hashes = {
        by_id[session_id]["artifacts"]["samples_sha256"]
        for session_id in reference_ids
        if session_id in by_id
    }
    evaluated_ids = {session["session_id"] for session in benchmark.get("sessions", [])}
    evaluated_hashes = {
        by_id[session_id]["artifacts"]["samples_sha256"]
        for session_id in evaluated_ids
        if session_id in by_id and by_id[session_id].get("artifacts")
    }
    duplicates = duplicate_sample_groups(manifest)
    reference_duplicate_groups = [
        group
        for group in duplicates
        if any(session_id in reference_ids for session_id in group.get("session_ids", []))
    ]
    return {
        "schema_version": "contextual-detector-reference-audit-v1",
        "terminology": {
            "baseline_term": "nominal/reference baseline",
            "source_split_name": reference.get("provenance", {}).get("reference_split"),
            "normal_train_is_verified_healthy_ground_truth": False,
            "reason": (
                "`normal_train` sessions are human-review nominal/reference sessions used to fit a baseline; "
                "they are not independent verified healthy ground truth or proof of absence of faults."
            ),
            "historical_labels_preserved": True,
        },
        "reference_model_version": reference.get("model_version"),
        "reference_session_count": len(reference_ids),
        "reference_sessions": [
            reference_session_provenance(paths, by_id[session_id])
            for session_id in sorted(reference_ids)
            if session_id in by_id
        ],
        "reference_window_trace_output": "reference_windows.jsonl",
        "overlap_audit": {
            "reference_and_evaluation_sample_hash_overlap": bool(reference_hashes & evaluated_hashes),
            "overlapping_sample_hashes": sorted(reference_hashes & evaluated_hashes),
            "alternate_reference_capture_representations": reference_duplicate_groups,
            "h3_excluded_sessions": benchmark.get("selection", {}).get("excluded_sessions", []),
            "policy": (
                "Reference session IDs and alternate representations sharing a reference sample hash are excluded "
                "from H3/H3.1 evaluation."
            ),
        },
        "reference_configuration": reference.get("configuration", {}),
        "reference_context_counts": reference.get("observed_context_counts", {}),
        "provenance": reference.get("provenance", {}),
    }


def reference_session_provenance(paths: EvaluationPaths, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": item["session_id"],
        "path": item.get("path"),
        "absolute_path": str((paths.repo_root / item["path"]).resolve()) if item.get("path") else None,
        "capture_provenance": item.get("capture_provenance"),
        "decoder": item.get("decoder"),
        "telemetry_schema_version": item.get("telemetry_schema_version"),
        "signal_columns": item.get("signal_columns"),
        "verified_signals": item.get("verified_signals"),
        "window_count": item.get("window_count"),
        "recording_duration_ms": item.get("recording_duration_ms"),
        "training_evaluation_split": item.get("training_evaluation_split"),
        "artifacts": item.get("artifacts"),
    }


def build_reference_window_rows(
    reference_frames: dict[str, pd.DataFrame],
    reference_items: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {item["session_id"]: item for item in manifest["sessions"]}
    rows: list[dict[str, Any]] = []
    for session_id, feature_frame in sorted(reference_frames.items()):
        item = by_id.get(session_id, {})
        for _, row in feature_frame.iterrows():
            rows.append(
                {
                    "session_id": session_id,
                    "source_path": item.get("path"),
                    "samples_sha256": item.get("artifacts", {}).get("samples_sha256"),
                    "metadata_sha256": item.get("artifacts", {}).get("metadata_sha256"),
                    "window_index": int(row.get("window_index", len(rows))),
                    "start_frame_index": _int_or_none(row.get("start_frame_index")),
                    "end_frame_index": _int_or_none(row.get("end_frame_index")),
                    "operating_context": assign_operating_context(row),
                    "rpm_median": _float_or_none(row.get("rpm_median")),
                    "tps_raw_median": _float_or_none(row.get("tps_raw_median")),
                    "tps_voltage_median": _float_or_none(row.get("tps_voltage_median")),
                    "battery_voltage_median": _float_or_none(row.get("battery_voltage_median")),
                    "battery_voltage_min": _float_or_none(row.get("battery_voltage_min")),
                }
            )
    return rows


def classify_disagreements(
    window_rows: list[dict[str, Any]],
    eval_cache: dict[str, CachedSession],
    manifest: dict[str, Any],
    reference: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {item["session_id"]: item for item in manifest["sessions"]}
    context_sequences = {
        session_id: [assign_operating_context(row) for _, row in cached.feature_frame.iterrows()]
        for session_id, cached in eval_cache.items()
    }
    records: list[dict[str, Any]] = []
    for row in window_rows:
        sid = row["session_id"]
        cached = eval_cache.get(sid)
        if cached is None:
            continue
        detectors = row.get("detectors", {})
        if_payload = detectors.get(IFOREST_DETECTOR_ID, {})
        ctx_payload = detectors.get(CONTEXTUAL_BATTERY_DETECTOR_ID, {})
        if if_payload.get("status") != "ok" or ctx_payload.get("status") != "ok":
            continue
        if_anomaly = _bool_or_none(if_payload.get("is_anomaly"))
        ctx_anomaly = _bool_or_none(ctx_payload.get("is_anomaly"))
        if if_anomaly is None or ctx_anomaly is None or if_anomaly == ctx_anomaly:
            continue

        window_number = int(row.get("window_number", row.get("window_index", 0)))
        feature_row = cached.feature_frame.iloc[window_number]
        context = str(ctx_payload.get("evidence", {}).get("operating_context") or assign_operating_context(feature_row))
        category = (
            "isolation_forest_anomaly_contextual_normal"
            if if_anomaly and not ctx_anomaly
            else "isolation_forest_normal_contextual_anomaly"
        )
        tags = classify_disagreement_tags(row, feature_row, context_sequences.get(sid, []), context)
        record = {
            "session_id": sid,
            "window_number": window_number,
            "window_index": _int_or_none(row.get("window_index")),
            "start_frame_index": _int_or_none(row.get("start_frame_index")),
            "end_frame_index": _int_or_none(row.get("end_frame_index")),
            "time": window_time(cached.frame_data, row),
            "primary_category": category,
            "categories": [category, *tags],
            "operating_context": context,
            "telemetry": telemetry_snapshot(feature_row),
            "isolation_forest": {
                "score_sample": _float_or_none(if_payload.get("anomaly_score")),
                "decision_score": _float_or_none(if_payload.get("evidence", {}).get("decision_score")),
                "prediction": if_payload.get("prediction"),
                "is_anomaly": if_anomaly,
                "threshold_reference": "decision_score < 0 maps to Isolation Forest anomaly",
                "most_unusual_features": if_payload.get("evidence", {}).get("most_unusual_features"),
            },
            "contextual_detector": {
                "robust_z": _float_or_none(ctx_payload.get("anomaly_score")),
                "threshold": _float_or_none(ctx_payload.get("evidence", {}).get("contextual_threshold")),
                "prediction": ctx_payload.get("prediction"),
                "is_anomaly": ctx_anomaly,
                "top_feature": ctx_payload.get("evidence", {}).get("contextual_top_feature"),
                "evidence": ctx_payload.get("evidence", {}).get("contextual_evidence"),
                "reference_stats": reference_stats_for_window(ctx_payload, reference),
            },
            "provenance": session_provenance(by_id.get(sid, cached.item)),
        }
        records.append(record)

    mark_repeated_patterns(records)
    for record in records:
        if len(record["categories"]) == 1:
            record["categories"].append("unresolved")
    return records


def classify_disagreement_tags(
    window_row: dict[str, Any],
    feature_row: pd.Series,
    context_sequence: list[str],
    context: str,
) -> list[str]:
    tags: list[str] = []
    window_number = int(window_row.get("window_number", window_row.get("window_index", 0)))
    if is_context_transition_window(context_sequence, window_number):
        tags.append("context_transition")

    detectors = window_row.get("detectors", {})
    if_payload = detectors.get(IFOREST_DETECTOR_ID, {})
    ctx_payload = detectors.get(CONTEXTUAL_BATTERY_DETECTOR_ID, {})
    ctx_score = _float_or_none(ctx_payload.get("anomaly_score"))
    ctx_threshold = _float_or_none(ctx_payload.get("evidence", {}).get("contextual_threshold"))
    if_decision = _float_or_none(if_payload.get("evidence", {}).get("decision_score"))
    if (
        ctx_score is not None
        and ctx_threshold is not None
        and abs(ctx_score - ctx_threshold) <= NEAR_CONTEXTUAL_THRESHOLD_DELTA
    ) or (if_decision is not None and abs(if_decision) <= NEAR_IF_DECISION_THRESHOLD_DELTA):
        tags.append("near_threshold_behavior")

    if (
        float(feature_row.get("checksum_failure_ratio", 0.0) or 0.0) > 0.0
        or float(feature_row.get("invalid_decoded_ratio", 0.0) or 0.0) > 0.0
        or int(feature_row.get("sample_count", 0) or 0) < 50
    ):
        tags.append("possible_data_quality_artifact")
    return tags


def mark_repeated_patterns(records: list[dict[str, Any]]) -> None:
    by_session_primary: Counter[tuple[str, str]] = Counter(
        (record["session_id"], record["primary_category"]) for record in records
    )
    by_context_primary_hashes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in records:
        sample_hash = record.get("provenance", {}).get("artifacts", {}).get("samples_sha256")
        if sample_hash:
            by_context_primary_hashes[(record["primary_category"], record["operating_context"])].add(sample_hash)

    for record in records:
        if by_session_primary[(record["session_id"], record["primary_category"])] >= 3:
            _append_unique(record["categories"], "repeated_pattern_within_one_session")
        if len(by_context_primary_hashes[(record["primary_category"], record["operating_context"])]) >= 3:
            _append_unique(record["categories"], "repeated_pattern_across_independent_sessions")

    for region in group_disagreement_regions(records):
        if region["window_count"] >= 3:
            for record in region["records"]:
                _append_unique(record["categories"], "repeated_pattern_within_one_session")


def summarize_disagreements(disagreements: list[dict[str, Any]]) -> dict[str, Any]:
    category_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    context_counts: Counter[str] = Counter()
    session_counts: Counter[str] = Counter()
    for record in disagreements:
        primary_counts[record["primary_category"]] += 1
        context_counts[record["operating_context"]] += 1
        session_counts[record["session_id"]] += 1
        category_counts.update(record["categories"])
    for expected in [
        "context_transition",
        "near_threshold_behavior",
        "possible_data_quality_artifact",
        "repeated_pattern_within_one_session",
        "repeated_pattern_across_independent_sessions",
        "unresolved",
    ]:
        category_counts.setdefault(expected, 0)

    examples = representative_disagreement_examples(disagreements)
    return {
        "schema_version": "contextual-disagreement-summary-v1",
        "total_disagreement_windows": len(disagreements),
        "primary_category_counts": dict(sorted(primary_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "operating_context_counts": dict(sorted(context_counts.items())),
        "session_count": len(session_counts),
        "sessions_with_disagreements": dict(session_counts.most_common()),
        "classification_policy": {
            "near_contextual_threshold_delta": NEAR_CONTEXTUAL_THRESHOLD_DELTA,
            "near_if_decision_threshold_delta": NEAR_IF_DECISION_THRESHOLD_DELTA,
            "transition_radius_windows": TRANSITION_RADIUS_WINDOWS,
            "categories_are_observable_patterns_not_fault_causes": True,
        },
        "representative_examples": examples,
    }


def representative_disagreement_examples(disagreements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = [
        "isolation_forest_anomaly_contextual_normal",
        "isolation_forest_normal_contextual_anomaly",
        "context_transition",
        "near_threshold_behavior",
        "possible_data_quality_artifact",
        "repeated_pattern_within_one_session",
        "repeated_pattern_across_independent_sessions",
        "unresolved",
    ]
    examples: list[dict[str, Any]] = []
    used: set[tuple[str, int]] = set()
    for category in wanted:
        candidates = [
            record for record in disagreements
            if category in record["categories"] and (record["session_id"], record["window_number"]) not in used
        ]
        if not candidates:
            continue
        chosen = sorted(candidates, key=disagreement_severity_key)[0]
        used.add((chosen["session_id"], chosen["window_number"]))
        examples.append(
            {
                "example_for": category,
                **compact_disagreement_example(chosen),
            }
        )
    return examples


def build_manual_review_manifest(
    benchmark: dict[str, Any],
    window_rows: list[dict[str, Any]],
    disagreements: list[dict[str, Any]],
    eval_cache: dict[str, CachedSession],
    manifest: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    by_id = {item["session_id"]: item for item in manifest["sessions"]}
    rows_by_session = group_window_rows_by_session(window_rows)
    contextual_events = []
    for session in benchmark.get("sessions", []):
        sid = session["session_id"]
        cached = eval_cache.get(sid)
        for detector in session.get("detectors", []):
            if detector.get("detector_id") != CONTEXTUAL_BATTERY_DETECTOR_ID:
                continue
            for index, event in enumerate((detector.get("events") or {}).get("items", []), start=1):
                contextual_events.append(
                    {
                        "review_item_id": f"contextual-event-{len(contextual_events) + 1:03d}",
                        "item_type": "contextual_event",
                        "session_id": sid,
                        "event_index_within_session": index,
                        "provenance": session_provenance(by_id.get(sid, cached.item if cached else {})),
                        "event": event,
                        "event_summary": event_summary(cached, event) if cached else {},
                        "overlap_with_isolation_forest": iforest_overlap_summary(rows_by_session.get(sid, []), event),
                        "prediction_source": {
                            "detector_id": CONTEXTUAL_BATTERY_DETECTOR_ID,
                            "model_version": reference.get("model_version"),
                            "label_is_verified_truth": bool(session.get("training_evaluation_split", {}).get("suitable_for_precision_recall_f1")),
                        },
                        "review_annotation": empty_review_annotation(),
                    }
                )

    disagreement_regions = [
        disagreement_region_manifest_item(region, eval_cache, by_id, reference)
        for region in select_representative_regions(group_disagreement_regions(disagreements))
    ]
    return {
        "schema_version": "contextual-manual-review-manifest-v1",
        "annotation_policy": {
            "detector_predictions_are_not_fault_labels": True,
            "allowed_annotations": ALLOWED_REVIEW_ANNOTATIONS,
            "annotations_are_separate_from_predictions": True,
        },
        "contextual_event_count": len(contextual_events),
        "representative_disagreement_region_count": len(disagreement_regions),
        "items": contextual_events + disagreement_regions,
    }


def evaluate_reference_stability(
    baseline_reference: dict[str, Any],
    reference_frames: dict[str, pd.DataFrame],
    reference_items: list[dict[str, Any]],
    eval_cache: dict[str, CachedSession],
) -> dict[str, Any]:
    baseline = evaluate_contextual_reference(baseline_reference, eval_cache)
    variants: list[dict[str, Any]] = []
    baseline_events = baseline["events"]
    persistence_counts: dict[str, int] = {event["event_id"]: 0 for event in baseline_events}

    for left_out in sorted(reference_frames):
        frames = {session_id: frame for session_id, frame in reference_frames.items() if session_id != left_out}
        reference = build_contextual_battery_reference(
            frames,
            provenance={
                "reference_split": "normal_train_leave_one_out",
                "left_out_reference_session_id": left_out,
                "reference_session_ids": sorted(frames),
            },
            min_context_windows=DEFAULT_MIN_CONTEXT_WINDOWS,
            robust_z_threshold=DEFAULT_ROBUST_Z_THRESHOLD,
        )
        evaluation = evaluate_contextual_reference(reference, eval_cache)
        persisted = persisted_baseline_events(baseline_events, evaluation["events"])
        for event_id in persisted:
            persistence_counts[event_id] += 1
        variants.append(
            {
                "variant_id": f"leave_out::{left_out}",
                "left_out_reference_session_id": left_out,
                "reference_session_count": len(frames),
                "model_version": reference.get("model_version"),
                "modeled_contexts": sorted(reference.get("contexts", {})),
                "context_stats": extract_context_stats(reference),
                "event_count": evaluation["event_count"],
                "anomaly_window_count": evaluation["anomaly_window_count"],
                "persisted_baseline_event_count": len(persisted),
            }
        )

    event_counts = [variant["event_count"] for variant in variants]
    anomaly_counts = [variant["anomaly_window_count"] for variant in variants]
    baseline_event_count = int(baseline["event_count"])
    persistence = []
    for event in baseline_events:
        count = persistence_counts[event["event_id"]]
        persistence.append(
            {
                **event,
                "persisted_variant_count": count,
                "variant_count": len(variants),
                "persistence_ratio": round(count / max(1, len(variants)), 6),
            }
        )

    low_persistence = [event for event in persistence if event["persistence_ratio"] < 0.75]
    event_count_range = (max(event_counts) - min(event_counts)) if event_counts else 0
    event_count_relative_range = None if baseline_event_count == 0 else round(event_count_range / baseline_event_count, 6)
    unstable = bool(
        event_count_relative_range is not None
        and event_count_relative_range > 0.25
        or (len(low_persistence) / max(1, len(persistence))) > 0.25
    )
    return {
        "schema_version": "contextual-reference-stability-v1",
        "method": "leave-one-reference-session-out",
        "window_split_policy": "whole reference sessions are left out; windows from a capture are never split across reference/evaluation subsets",
        "baseline": {
            "model_version": baseline_reference.get("model_version"),
            "reference_session_count": len(reference_frames),
            "context_stats": extract_context_stats(baseline_reference),
            "event_count": baseline_event_count,
            "anomaly_window_count": baseline["anomaly_window_count"],
        },
        "variants": variants,
        "context_baseline_stability": summarize_context_stat_stability(baseline_reference, variants),
        "threshold_stability": {
            "threshold_policy": "fixed configuration, not refit from anomaly counts",
            "baseline_threshold": baseline_reference.get("configuration", {}).get("robust_z_threshold"),
            "variant_thresholds": sorted({DEFAULT_ROBUST_Z_THRESHOLD for _ in variants}),
            "stable": True,
        },
        "event_count_stability": {
            "baseline_event_count": baseline_event_count,
            "variant_min": min(event_counts) if event_counts else None,
            "variant_median": statistics.median(event_counts) if event_counts else None,
            "variant_max": max(event_counts) if event_counts else None,
            "relative_range_vs_baseline": event_count_relative_range,
            "anomaly_window_min": min(anomaly_counts) if anomaly_counts else None,
            "anomaly_window_median": statistics.median(anomaly_counts) if anomaly_counts else None,
            "anomaly_window_max": max(anomaly_counts) if anomaly_counts else None,
        },
        "baseline_event_persistence": persistence,
        "low_persistence_event_count": len(low_persistence),
        "stability_interpretation": {
            "unstable_under_leave_one_out": unstable,
            "reason": (
                "Event count or individual event persistence changes substantially under leave-one-reference-session-out."
                if unstable
                else "Leave-one-reference-session-out variants preserve event counts and most baseline events."
            ),
        },
    }


def evaluate_contextual_reference(reference: dict[str, Any], eval_cache: dict[str, CachedSession]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    anomaly_window_count = 0
    for sid, cached in sorted(eval_cache.items()):
        result = ModelHarness([ContextualBatteryVoltageDetector(reference)]).run(
            cached.feature_frame,
            DetectorContext(
                telemetry_schema_version=cached.item.get("telemetry_schema_version"),
                signal_columns=tuple(cached.signal_columns),
            ),
        ).results[0]
        if result.status != "ok":
            continue
        anomaly_window_count += int(result.anomaly_window_count or 0)
        for index, event in enumerate(anomaly_events(result.windows, cached.frame_data), start=1):
            events.append(
                {
                    "event_id": f"{sid}:{event['start_window_index']}:{event['end_window_index']}",
                    "session_id": sid,
                    "event_index_within_session": index,
                    **event,
                }
            )
    return {
        "event_count": len(events),
        "anomaly_window_count": anomaly_window_count,
        "events": events,
    }


def evaluate_context_boundaries(
    window_rows: list[dict[str, Any]],
    eval_cache: dict[str, CachedSession],
) -> dict[str, Any]:
    context_sequences = {
        session_id: [assign_operating_context(row) for _, row in cached.feature_frame.iterrows()]
        for session_id, cached in eval_cache.items()
    }
    scored_total = 0
    scored_near_boundary = 0
    anomalies_total = 0
    anomalies_near_boundary = 0
    disagreements_total = 0
    disagreements_near_boundary = 0
    anomaly_context_counts: Counter[str] = Counter()
    boundary_context_counts: Counter[str] = Counter()

    for row in window_rows:
        sid = row["session_id"]
        sequence = context_sequences.get(sid)
        if not sequence:
            continue
        window_number = int(row.get("window_number", row.get("window_index", 0)))
        if window_number >= len(sequence):
            continue
        near_boundary = is_context_transition_window(sequence, window_number)
        context = sequence[window_number]
        detectors = row.get("detectors", {})
        ctx_payload = detectors.get(CONTEXTUAL_BATTERY_DETECTOR_ID, {})
        if ctx_payload.get("status") == "ok" and _bool_or_none(ctx_payload.get("is_anomaly")) is not None:
            scored_total += 1
            if near_boundary:
                scored_near_boundary += 1
            if _bool_or_none(ctx_payload.get("is_anomaly")) is True:
                anomalies_total += 1
                anomaly_context_counts[context] += 1
                if near_boundary:
                    anomalies_near_boundary += 1
                    boundary_context_counts[context] += 1

        if_payload = detectors.get(IFOREST_DETECTOR_ID, {})
        if_anomaly = _bool_or_none(if_payload.get("is_anomaly")) if if_payload.get("status") == "ok" else None
        ctx_anomaly = _bool_or_none(ctx_payload.get("is_anomaly")) if ctx_payload.get("status") == "ok" else None
        if if_anomaly is not None and ctx_anomaly is not None and if_anomaly != ctx_anomaly:
            disagreements_total += 1
            if near_boundary:
                disagreements_near_boundary += 1

    away_total = scored_total - scored_near_boundary
    away_anomalies = anomalies_total - anomalies_near_boundary
    near_rate = anomalies_near_boundary / scored_near_boundary if scored_near_boundary else None
    away_rate = away_anomalies / away_total if away_total else None
    enrichment = None
    if near_rate is not None and away_rate is not None and away_rate > 0:
        enrichment = near_rate / away_rate
    transition_dominates = bool(
        enrichment is not None
        and enrichment >= 2.0
        and anomalies_near_boundary / max(1, anomalies_total) >= 0.25
    )
    return {
        "schema_version": "context-boundary-review-v1",
        "transition_radius_windows": TRANSITION_RADIUS_WINDOWS,
        "scored_windows": scored_total,
        "scored_windows_near_boundary": scored_near_boundary,
        "contextual_anomaly_windows": anomalies_total,
        "contextual_anomaly_windows_near_boundary": anomalies_near_boundary,
        "contextual_anomaly_rate_near_boundary": None if near_rate is None else round(near_rate, 6),
        "contextual_anomaly_rate_away_from_boundary": None if away_rate is None else round(away_rate, 6),
        "boundary_anomaly_enrichment": None if enrichment is None else round(enrichment, 6),
        "disagreement_windows": disagreements_total,
        "disagreement_windows_near_boundary": disagreements_near_boundary,
        "anomaly_context_counts": dict(sorted(anomaly_context_counts.items())),
        "boundary_anomaly_context_counts": dict(sorted(boundary_context_counts.items())),
        "transition_artifact_assessment": {
            "context_boundaries_dominate_contextual_anomalies": transition_dominates,
            "simple_transition_rule_justified": transition_dominates,
            "reason": (
                "Contextual anomalies are enriched near context boundaries."
                if transition_dominates
                else "Contextual anomalies are not dominated by context-boundary windows under the measured transition radius."
            ),
        },
    }


def build_decision(
    disagreement_summary: dict[str, Any],
    manual_review: dict[str, Any],
    stability: dict[str, Any],
    boundary: dict[str, Any],
) -> dict[str, Any]:
    distinct = disagreement_summary["total_disagreement_windows"] > 0
    stable = not stability["stability_interpretation"]["unstable_under_leave_one_out"]
    boundary_dominated = boundary["transition_artifact_assessment"]["context_boundaries_dominate_contextual_anomalies"]
    contextual_events = manual_review["contextual_event_count"]
    interpretable = contextual_events > 0 and stable and not boundary_dominated
    if stable and distinct and not boundary_dominated:
        recommendation = "advance"
    elif distinct and contextual_events > 0:
        recommendation = "revise"
    else:
        recommendation = "reject"
    return {
        "schema_version": "contextual-core2-review-decision-v1",
        "recommendation": recommendation,
        "answers": {
            "stable_enough_to_remain_in_harness_research_path": stable,
            "materially_distinct_from_isolation_forest": distinct,
            "events_interpretable_from_existing_telemetry": interpretable,
            "reference_or_boundary_artifacts_dominate_results": (not stable) or boundary_dominated,
            "valuable_physical_measurements": [
                "direct battery/charging voltage trace synchronized to ECU samples",
                "starter/engine-running state or alternator charging state",
                "known accessory electrical load state",
                "repeat captures for low-voltage candidate sessions",
                "bench-confirmed TPS/RPM/battery calibration spot checks during context transitions",
            ],
        },
        "basis": {
            "disagreement_windows": disagreement_summary["total_disagreement_windows"],
            "contextual_event_count": contextual_events,
            "reference_unstable": stability["stability_interpretation"]["unstable_under_leave_one_out"],
            "boundary_dominated": boundary_dominated,
            "supervised_accuracy_claims_made": False,
        },
    }


def load_eval_cache(paths: EvaluationPaths, settings: Settings, items: list[dict[str, Any]]) -> dict[str, CachedSession]:
    cache: dict[str, CachedSession] = {}
    for item in items:
        _, frame_data, feature_frame, signal_columns = load_session_frames(paths, settings, item)
        cache[item["session_id"]] = CachedSession(
            item=item,
            frame_data=frame_data,
            feature_frame=feature_frame,
            signal_columns=signal_columns,
        )
    return cache


def group_disagreement_regions(disagreements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for sid, records in sorted(_group_by_session(disagreements).items()):
        sorted_records = sorted(records, key=lambda item: item["window_number"])
        current: list[dict[str, Any]] = []
        previous_window: int | None = None
        for record in sorted_records:
            window_number = int(record["window_number"])
            if previous_window is None or window_number == previous_window + 1:
                current.append(record)
            else:
                regions.append(make_region(sid, current))
                current = [record]
            previous_window = window_number
        if current:
            regions.append(make_region(sid, current))
    return regions


def make_region(session_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    categories = Counter(category for record in records for category in record["categories"])
    contexts = Counter(record["operating_context"] for record in records)
    return {
        "session_id": session_id,
        "start_window_index": min(record["window_number"] for record in records),
        "end_window_index": max(record["window_number"] for record in records),
        "window_count": len(records),
        "categories": dict(sorted(categories.items())),
        "contexts": dict(sorted(contexts.items())),
        "records": records,
    }


def select_representative_regions(regions: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used: set[tuple[str, int, int]] = set()
    for category in [
        "isolation_forest_anomaly_contextual_normal",
        "isolation_forest_normal_contextual_anomaly",
        "context_transition",
        "near_threshold_behavior",
        "repeated_pattern_within_one_session",
        "repeated_pattern_across_independent_sessions",
    ]:
        candidates = [region for region in regions if category in region["categories"]]
        if candidates:
            region = sorted(candidates, key=lambda item: (-item["window_count"], item["session_id"], item["start_window_index"]))[0]
            key = (region["session_id"], region["start_window_index"], region["end_window_index"])
            if key not in used:
                selected.append(region)
                used.add(key)
    for region in sorted(regions, key=lambda item: (-item["window_count"], item["session_id"], item["start_window_index"])):
        key = (region["session_id"], region["start_window_index"], region["end_window_index"])
        if key in used:
            continue
        selected.append(region)
        used.add(key)
        if len(selected) >= limit:
            break
    return selected[:limit]


def disagreement_region_manifest_item(
    region: dict[str, Any],
    eval_cache: dict[str, CachedSession],
    by_id: dict[str, dict[str, Any]],
    reference: dict[str, Any],
) -> dict[str, Any]:
    sid = region["session_id"]
    cached = eval_cache.get(sid)
    event = {
        "start_window_index": region["start_window_index"],
        "end_window_index": region["end_window_index"],
        "window_count": region["window_count"],
    }
    return {
        "review_item_id": f"disagreement-region-{sid}-{region['start_window_index']}-{region['end_window_index']}",
        "item_type": "representative_disagreement_region",
        "session_id": sid,
        "provenance": session_provenance(by_id.get(sid, cached.item if cached else {})),
        "region": event,
        "region_summary": event_summary(cached, event) if cached else {},
        "categories": region["categories"],
        "contexts": region["contexts"],
        "representative_window": compact_disagreement_example(region["records"][0]),
        "prediction_source": {
            "detectors": [IFOREST_DETECTOR_ID, CONTEXTUAL_BATTERY_DETECTOR_ID],
            "contextual_model_version": reference.get("model_version"),
        },
        "review_annotation": empty_review_annotation(),
    }


def event_summary(cached: CachedSession, event: dict[str, Any]) -> dict[str, Any]:
    start = int(event["start_window_index"])
    end = int(event["end_window_index"])
    chunk = cached.feature_frame.iloc[start : end + 1]
    contexts = [assign_operating_context(row) for _, row in chunk.iterrows()]
    return {
        "time": window_time(
            cached.frame_data,
            {
                "start_frame_index": int(chunk.iloc[0].get("start_frame_index", 0)),
                "end_frame_index": int(chunk.iloc[-1].get("end_frame_index", 0)),
            },
        ),
        "context_counts": dict(sorted(Counter(contexts).items())),
        "rpm_median_range": numeric_range(chunk["rpm_median"]) if "rpm_median" in chunk else None,
        "tps_raw_median_range": numeric_range(chunk["tps_raw_median"]) if "tps_raw_median" in chunk else None,
        "tps_voltage_median_range": numeric_range(chunk["tps_voltage_median"]) if "tps_voltage_median" in chunk else None,
        "battery_voltage_median_range": numeric_range(chunk["battery_voltage_median"]) if "battery_voltage_median" in chunk else None,
        "battery_voltage_min_range": numeric_range(chunk["battery_voltage_min"]) if "battery_voltage_min" in chunk else None,
        "max_checksum_failure_ratio": _float_or_none(chunk["checksum_failure_ratio"].max()) if "checksum_failure_ratio" in chunk else None,
        "max_invalid_decoded_ratio": _float_or_none(chunk["invalid_decoded_ratio"].max()) if "invalid_decoded_ratio" in chunk else None,
    }


def iforest_overlap_summary(window_rows: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
    start = int(event["start_window_index"])
    end = int(event["end_window_index"])
    event_rows = [
        row for row in window_rows
        if start <= int(row.get("window_number", row.get("window_index", -1))) <= end
    ]
    if_anomaly_count = 0
    comparable_disagreement_count = 0
    comparable_count = 0
    for row in event_rows:
        detectors = row.get("detectors", {})
        if_payload = detectors.get(IFOREST_DETECTOR_ID, {})
        ctx_payload = detectors.get(CONTEXTUAL_BATTERY_DETECTOR_ID, {})
        if_anomaly = _bool_or_none(if_payload.get("is_anomaly")) if if_payload.get("status") == "ok" else None
        ctx_anomaly = _bool_or_none(ctx_payload.get("is_anomaly")) if ctx_payload.get("status") == "ok" else None
        if if_anomaly is True:
            if_anomaly_count += 1
        if if_anomaly is not None and ctx_anomaly is not None:
            comparable_count += 1
            if if_anomaly != ctx_anomaly:
                comparable_disagreement_count += 1
    return {
        "event_window_count": len(event_rows),
        "iforest_anomaly_window_count": if_anomaly_count,
        "comparable_window_count": comparable_count,
        "comparable_disagreement_window_count": comparable_disagreement_count,
        "source": "H3 shadow_window_results.jsonl",
    }


def extract_context_stats(reference: dict[str, Any]) -> dict[str, Any]:
    contexts: dict[str, Any] = {}
    for context, payload in reference.get("contexts", {}).items():
        contexts[context] = {
            "window_count": payload.get("window_count"),
            "reference_session_count": payload.get("reference_session_count"),
            "features": {
                feature: {
                    "median": stats.get("median"),
                    "robust_scale": stats.get("robust_scale"),
                }
                for feature, stats in payload.get("features", {}).items()
            },
        }
    return contexts


def summarize_context_stat_stability(baseline_reference: dict[str, Any], variants: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    baseline_contexts = extract_context_stats(baseline_reference)
    for context, context_stats in baseline_contexts.items():
        summary[context] = {}
        for feature, baseline_stats in context_stats.get("features", {}).items():
            medians = []
            scales = []
            for variant in variants:
                stats = (
                    variant.get("context_stats", {})
                    .get(context, {})
                    .get("features", {})
                    .get(feature)
                )
                if stats:
                    medians.append(float(stats["median"]))
                    scales.append(float(stats["robust_scale"]))
            summary[context][feature] = {
                "baseline_median": baseline_stats.get("median"),
                "variant_median_min": min(medians) if medians else None,
                "variant_median_max": max(medians) if medians else None,
                "max_abs_median_delta": (
                    max(abs(value - float(baseline_stats["median"])) for value in medians)
                    if medians else None
                ),
                "baseline_robust_scale": baseline_stats.get("robust_scale"),
                "variant_robust_scale_min": min(scales) if scales else None,
                "variant_robust_scale_max": max(scales) if scales else None,
                "max_abs_robust_scale_delta": (
                    max(abs(value - float(baseline_stats["robust_scale"])) for value in scales)
                    if scales else None
                ),
            }
    return summary


def persisted_baseline_events(baseline_events: list[dict[str, Any]], variant_events: list[dict[str, Any]]) -> set[str]:
    persisted: set[str] = set()
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in variant_events:
        by_session[event["session_id"]].append(event)
    for event in baseline_events:
        for candidate in by_session.get(event["session_id"], []):
            if intervals_overlap(
                int(event["start_window_index"]),
                int(event["end_window_index"]),
                int(candidate["start_window_index"]),
                int(candidate["end_window_index"]),
            ):
                persisted.add(event["event_id"])
                break
    return persisted


def intervals_overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return max(left_start, right_start) <= min(left_end, right_end)


def window_time(frame_data: pd.DataFrame, window_row: dict[str, Any]) -> dict[str, Any]:
    start_frame = _int_or_none(window_row.get("start_frame_index"))
    end_frame = _int_or_none(window_row.get("end_frame_index"))
    if start_frame is None or end_frame is None or frame_data.empty or "frame_index" not in frame_data:
        return {"start_time_ms": None, "end_time_ms": None, "duration_ms": None}
    indexed = frame_data.set_index("frame_index")
    start_ms = indexed.loc[start_frame, "relative_time_ms"] if start_frame in indexed.index else None
    end_ms = indexed.loc[end_frame, "relative_time_ms"] if end_frame in indexed.index else None
    start_value = _float_or_none(start_ms)
    end_value = _float_or_none(end_ms)
    return {
        "start_time_ms": start_value,
        "end_time_ms": end_value,
        "duration_ms": None if start_value is None or end_value is None else round(max(0.0, end_value - start_value), 6),
    }


def telemetry_snapshot(row: pd.Series) -> dict[str, Any]:
    return {
        "rpm_median": _float_or_none(row.get("rpm_median")),
        "rpm_mean": _float_or_none(row.get("rpm_mean")),
        "tps_raw_median": _float_or_none(row.get("tps_raw_median")),
        "tps_voltage_median": _float_or_none(row.get("tps_voltage_median")),
        "battery_voltage_median": _float_or_none(row.get("battery_voltage_median")),
        "battery_voltage_mean": _float_or_none(row.get("battery_voltage_mean")),
        "battery_voltage_min": _float_or_none(row.get("battery_voltage_min")),
        "battery_voltage_range": _float_or_none(row.get("battery_voltage_range")),
        "sample_count": _int_or_none(row.get("sample_count")),
        "checksum_failure_ratio": _float_or_none(row.get("checksum_failure_ratio")),
        "invalid_decoded_ratio": _float_or_none(row.get("invalid_decoded_ratio")),
    }


def reference_stats_for_window(ctx_payload: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any] | None:
    evidence = ctx_payload.get("evidence", {})
    context = evidence.get("operating_context")
    top_feature = evidence.get("contextual_top_feature")
    if not context or not top_feature:
        return None
    return (
        reference.get("contexts", {})
        .get(context, {})
        .get("features", {})
        .get(top_feature)
    )


def session_provenance(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": item.get("session_id"),
        "path": item.get("path"),
        "capture_provenance": item.get("capture_provenance"),
        "decoder": item.get("decoder"),
        "telemetry_schema_version": item.get("telemetry_schema_version"),
        "training_evaluation_split": item.get("training_evaluation_split"),
        "artifacts": item.get("artifacts"),
    }


def compact_disagreement_example(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": record["session_id"],
        "window_number": record["window_number"],
        "time": record["time"],
        "operating_context": record["operating_context"],
        "telemetry": record["telemetry"],
        "isolation_forest": record["isolation_forest"],
        "contextual_detector": record["contextual_detector"],
        "categories": record["categories"],
        "provenance": record["provenance"],
    }


def disagreement_severity_key(record: dict[str, Any]) -> tuple[float, str, int]:
    ctx_score = record.get("contextual_detector", {}).get("robust_z")
    if_score = record.get("isolation_forest", {}).get("decision_score")
    severity = 0.0
    if ctx_score is not None:
        severity += abs(float(ctx_score) - DEFAULT_ROBUST_Z_THRESHOLD)
    if if_score is not None:
        severity += abs(float(if_score))
    return (-severity, record["session_id"], int(record["window_number"]))


def is_context_transition_window(context_sequence: list[str], window_number: int) -> bool:
    if not context_sequence or window_number >= len(context_sequence):
        return False
    current = context_sequence[window_number]
    start = max(0, window_number - TRANSITION_RADIUS_WINDOWS)
    end = min(len(context_sequence) - 1, window_number + TRANSITION_RADIUS_WINDOWS)
    return any(context_sequence[index] != current for index in range(start, end + 1))


def empty_review_annotation() -> dict[str, Any]:
    return {
        "allowed_values": ALLOWED_REVIEW_ANNOTATIONS,
        "selected": None,
        "reviewer": None,
        "reviewed_at": None,
        "notes": None,
        "fault_label_assigned": False,
    }


def numeric_range(series: pd.Series) -> dict[str, float | None]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return {"min": None, "median": None, "max": None}
    return {
        "min": _float_or_none(clean.min()),
        "median": _float_or_none(clean.median()),
        "max": _float_or_none(clean.max()),
    }


def _group_by_session(disagreements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in disagreements:
        grouped[record["session_id"]].append(record)
    return grouped


def group_window_rows_by_session(window_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in window_rows:
        grouped[row["session_id"]].append(row)
    return grouped


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=json_safe) + "\n")


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def write_markdown_report(
    path: Path,
    reference_audit: dict[str, Any],
    disagreement_summary: dict[str, Any],
    manual_review: dict[str, Any],
    stability: dict[str, Any],
    boundary: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    lines = [
        "# H3.1 Contextual Detector Review",
        "",
        "## Reference Audit",
        "",
        f"- Baseline terminology: {reference_audit['terminology']['baseline_term']}",
        f"- `normal_train` verified healthy ground truth: `{reference_audit['terminology']['normal_train_is_verified_healthy_ground_truth']}`",
        f"- Reference/evaluation sample hash overlap: `{reference_audit['overlap_audit']['reference_and_evaluation_sample_hash_overlap']}`",
        f"- Reference sessions: {reference_audit['reference_session_count']}",
        "",
        "## Disagreements",
        "",
        f"- Total comparable disagreement windows: {disagreement_summary['total_disagreement_windows']}",
        f"- Primary categories: `{disagreement_summary['primary_category_counts']}`",
        f"- Observable category tags: `{disagreement_summary['category_counts']}`",
        f"- Sessions with disagreements: {disagreement_summary['session_count']}",
        "",
        "## Manual Review Dataset",
        "",
        f"- Contextual events included: {manual_review['contextual_event_count']}",
        f"- Representative disagreement regions included: {manual_review['representative_disagreement_region_count']}",
        "- Review annotations are intentionally separate from detector predictions.",
        "",
        "## Stability",
        "",
        f"- Method: {stability['method']}",
        f"- Baseline event count: {stability['event_count_stability']['baseline_event_count']}",
        f"- Leave-one-out event count range: {stability['event_count_stability']['variant_min']}..{stability['event_count_stability']['variant_max']}",
        f"- Low-persistence baseline events: {stability['low_persistence_event_count']}",
        f"- Unstable under leave-one-out: `{stability['stability_interpretation']['unstable_under_leave_one_out']}`",
        "",
        "## Context Boundaries",
        "",
        f"- Contextual anomaly windows near boundaries: {boundary['contextual_anomaly_windows_near_boundary']} / {boundary['contextual_anomaly_windows']}",
        f"- Boundary anomaly enrichment: `{boundary['boundary_anomaly_enrichment']}`",
        f"- Transition rule justified: `{boundary['transition_artifact_assessment']['simple_transition_rule_justified']}`",
        "",
        "## Decision",
        "",
        f"- Recommendation: `{decision['recommendation']}`",
        f"- Stable enough for research path: `{decision['answers']['stable_enough_to_remain_in_harness_research_path']}`",
        f"- Materially distinct from Isolation Forest: `{decision['answers']['materially_distinct_from_isolation_forest']}`",
        f"- Existing telemetry interpretable enough: `{decision['answers']['events_interpretable_from_existing_telemetry']}`",
        f"- Reference/boundary artifacts dominate: `{decision['answers']['reference_or_boundary_artifacts_dominate_results']}`",
        "- Most valuable physical measurements: "
        + "; ".join(decision["answers"]["valuable_physical_measurements"]),
        "",
        "## Outputs",
        "",
        "- `reference_audit.json` and `reference_windows.jsonl`",
        "- `disagreements.jsonl` and `disagreement_summary.json`",
        "- `manual_review_manifest.json`",
        "- `reference_stability.json`",
        "- `context_boundary_review.json`",
        "- `decision_report.json`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
