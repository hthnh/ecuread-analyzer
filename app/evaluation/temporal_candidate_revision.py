from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from app.config import Settings
from app.evaluation.contextual_shadow import (
    detector_summary_by_id,
    load_session_frames,
    select_shadow_evaluation_sessions,
    write_jsonl,
)
from app.evaluation.model_harness_evaluation import (
    EvaluationPaths,
    anomaly_events,
    benchmark_summary,
    build_evaluation_manifest,
    default_paths,
    detector_benchmark_payload,
    detector_disagreements,
    label_metrics_policy,
    write_json,
)
from app.evaluation.temporal_shadow import (
    IFOREST_DETECTOR_ID,
    CachedSession,
    event_overlap,
    group_window_rows_by_session,
    intervals_overlap,
    load_eval_cache,
    numeric_summary,
    session_provenance,
)
from app.ml.artifacts import load_model_bundle
from app.ml.contextual_detector import CONTEXTUAL_BATTERY_DETECTOR_ID, ContextualBatteryVoltageDetector
from app.ml.harness import DetectorContext, ModelHarness
from app.ml.iforest_detector import IsolationForestDetector
from app.ml.rpm_stability_detector import (
    RPM_STABILITY_DETECTOR_ID,
    RPM_STABILITY_REQUIRED_FEATURES,
    RpmStabilityConfig,
    RpmStabilityDetector,
)
from app.ml.temporal_detector import TEMPORAL_BATTERY_DETECTOR_ID, TemporalBatteryShiftDetector


def default_h41_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h4_1")


def run_all(
    paths: EvaluationPaths,
    *,
    h3_dir: Path | None = None,
    h4_dir: Path | None = None,
) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h3_dir = (h3_dir or paths.repo_root / "data" / "evaluation" / "h3").resolve()
    resolved_h4_dir = (h4_dir or paths.repo_root / "data" / "evaluation" / "h4").resolve()
    manifest = load_manifest(paths, resolved_h3_dir)
    contextual_reference = read_json(resolved_h3_dir / "contextual_reference.json")
    candidate_a = load_candidate_a_summary(resolved_h4_dir)

    settings = Settings(data_dir=paths.data_dir, model_dir=paths.model_dir)
    selected_items, excluded = select_shadow_evaluation_sessions(manifest)
    eval_cache = load_eval_cache(paths, settings, selected_items)
    config = RpmStabilityConfig()

    target_audit = build_target_audit(manifest, eval_cache, config, excluded)
    benchmark, window_rows, rpm_results = run_candidate_benchmark(paths, manifest, contextual_reference, eval_cache, config)
    review = build_rpm_review_manifest(manifest, eval_cache, window_rows, rpm_results, config)
    sensitivity = run_parameter_sensitivity(eval_cache, config)
    selection = select_temporal_candidate(candidate_a, benchmark, review, sensitivity, target_audit)

    outputs = {
        "target_audit": paths.output_dir / "target_audit.json",
        "benchmark": paths.output_dir / "candidate_benchmark_results.json",
        "window_results": paths.output_dir / "shadow_window_results.jsonl",
        "review_manifest": paths.output_dir / "rpm_review_manifest.json",
        "parameter_sensitivity": paths.output_dir / "rpm_parameter_sensitivity.json",
        "candidate_selection": paths.output_dir / "candidate_selection.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["target_audit"], target_audit)
    write_json(outputs["benchmark"], benchmark)
    write_jsonl(outputs["window_results"], window_rows)
    write_json(outputs["review_manifest"], review)
    write_json(outputs["parameter_sensitivity"], sensitivity)
    write_json(outputs["candidate_selection"], selection)
    write_markdown_report(outputs["report"], target_audit, benchmark, review, sensitivity, selection)
    return outputs


def load_manifest(paths: EvaluationPaths, h3_dir: Path) -> dict[str, Any]:
    manifest_path = h3_dir / "evaluation_manifest.json"
    if manifest_path.exists():
        return read_json(manifest_path)
    return build_evaluation_manifest(paths)


def load_candidate_a_summary(h4_dir: Path) -> dict[str, Any]:
    benchmark_path = h4_dir / "shadow_benchmark_results.json"
    sensitivity_path = h4_dir / "parameter_sensitivity.json"
    decision_path = h4_dir / "decision_report.json"
    if not benchmark_path.exists():
        return {"available": False, "reason": "H4 benchmark not found"}
    benchmark = read_json(benchmark_path)
    sensitivity = read_json(sensitivity_path) if sensitivity_path.exists() else {}
    decision = read_json(decision_path) if decision_path.exists() else {}
    overlap = benchmark.get("summary", {}).get("temporal_event_overlap", {})
    detector = benchmark.get("summary", {}).get("by_detector", {}).get(TEMPORAL_BATTERY_DETECTOR_ID, {})
    return {
        "available": True,
        "candidate_id": TEMPORAL_BATTERY_DETECTOR_ID,
        "event_count": overlap.get("temporal_event_count"),
        "core3_only_event_count": overlap.get("core3_only_event_count"),
        "core1_core3_event_count": overlap.get("core1_core3_event_count"),
        "scored_window_count": detector.get("scored_window_count"),
        "anomaly_window_count": detector.get("anomaly_window_count"),
        "parameter_sensitive": sensitivity.get("sensitivity_interpretation", {}).get("sensitive_to_reasonable_parameter_changes"),
        "recommendation": decision.get("recommendation"),
        "basis": decision.get("basis"),
    }


def build_target_audit(
    manifest: dict[str, Any],
    eval_cache: dict[str, CachedSession],
    config: RpmStabilityConfig,
    excluded: list[dict[str, Any]],
) -> dict[str, Any]:
    runs = []
    window_steps = []
    missing_required_features: dict[str, list[str]] = {}
    for sid, cached in eval_cache.items():
        starts = [int(value) for value in cached.feature_frame["start_frame_index"].tolist()]
        window_steps.extend([right - left for left, right in zip(starts, starts[1:])])
        missing = [feature for feature in RPM_STABILITY_REQUIRED_FEATURES if feature not in cached.feature_frame.columns]
        if missing:
            missing_required_features[sid] = missing
            continue
        runs.extend(closed_throttle_runs(sid, cached.feature_frame, config))
    adequate = [run for run in runs if run["window_count"] >= config.min_warmup_windows + config.min_persistence_windows]
    v2_ready = [
        item for item in manifest["sessions"]
        if item.get("telemetry_schema_version") == "canonical-telemetry-v2"
        and {"rpm", "tps_voltage", "tps_raw", "battery_voltage"}.issubset(set(item.get("verified_signals", [])))
    ]
    return {
        "schema_version": "temporal-rpm-target-audit-v1",
        "candidate_id": RPM_STABILITY_DETECTOR_ID,
        "target": "sustained RPM variability increase within closed-throttle running segments",
        "verified_signals_used": ["rpm", "tps_voltage", "tps_raw"],
        "signals_not_used": {
            "battery_voltage": "excluded from Candidate B target to remain distinct from Core 2 and Candidate A",
            "iat_c": "not strictly verified",
            "ect_c": "not strictly verified",
        },
        "applicability_conditions": {
            "engine_running": f"rpm_median >= {config.min_engine_running_rpm}",
            "closed_or_near_closed_tps_raw": f"tps_raw_median <= {config.closed_tps_raw_max}",
            "closed_or_near_closed_tps_voltage": f"tps_voltage_median <= {config.closed_tps_voltage_max}",
            "minimum_run_windows": config.min_warmup_windows + config.min_persistence_windows,
            "max_start_frame_step": config.max_start_frame_step,
        },
        "data_availability": {
            "v2_sessions_with_verified_required_signals": len(v2_ready),
            "evaluated_session_count": len(eval_cache),
            "feature_complete_session_count": len(eval_cache) - len(missing_required_features),
            "sessions_missing_required_features": missing_required_features,
            "excluded_sessions": excluded,
        },
        "cadence": {
            "window_start_frame_step": numeric_summary(window_steps),
        },
        "closed_throttle_runs": {
            "total": len(runs),
            "adequate_for_candidate": len(adequate),
            "run_length_windows": numeric_summary([run["window_count"] for run in runs]),
            "contexts_by_session": dict(sorted(Counter(run["session_id"] for run in adequate).items())),
        },
        "feasible": len(adequate) > 0,
        "feasibility_reason": (
            "Closed-throttle running segments are long enough for warm-up plus persistence."
            if adequate
            else "Closed-throttle running segments are too short for temporal RPM stability detection."
        ),
        "configuration": config.to_dict(),
    }


def run_candidate_benchmark(
    paths: EvaluationPaths,
    manifest: dict[str, Any],
    contextual_reference: dict[str, Any],
    eval_cache: dict[str, CachedSession],
    config: RpmStabilityConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    bundle = load_model_bundle(paths.model_dir)
    session_results = []
    window_rows: list[dict[str, Any]] = []
    rpm_results: dict[str, Any] = {}
    for sid, cached in sorted(eval_cache.items()):
        detectors = [
            IsolationForestDetector(bundle),
            ContextualBatteryVoltageDetector(contextual_reference),
            TemporalBatteryShiftDetector(),
            RpmStabilityDetector(config),
        ]
        context = DetectorContext(
            telemetry_schema_version=cached.item.get("telemetry_schema_version"),
            signal_columns=tuple(cached.signal_columns),
        )
        started = perf_counter()
        harness_result = ModelHarness(detectors).run(cached.feature_frame, context)
        harness_latency_ms = round((perf_counter() - started) * 1000.0, 6)
        detector_payloads = []
        for result in harness_result.results:
            latency_ms = float(result.metadata.get("harness_latency_ms", harness_latency_ms))
            detector_payloads.append(
                detector_benchmark_payload(
                    result,
                    cached.feature_frame,
                    cached.frame_data,
                    cached.item.get("recording_duration_ms"),
                    latency_ms,
                )
            )
            if result.identity.detector_id == RPM_STABILITY_DETECTOR_ID:
                rpm_results[sid] = result
        append_window_rows(window_rows, sid, cached, harness_result.results)
        session_results.append(
            {
                "session_id": sid,
                "dataset_kind": cached.item.get("dataset_kind"),
                "telemetry_schema_version": cached.item.get("telemetry_schema_version"),
                "training_evaluation_split": cached.item.get("training_evaluation_split"),
                "window_count": int(len(cached.feature_frame)),
                "recording_duration_ms": cached.item.get("recording_duration_ms"),
                "detectors": detector_payloads,
                "detector_disagreements": detector_disagreements(harness_result.results),
                "four_way_states": four_way_states(harness_result.results),
            }
        )
    benchmark = {
        "schema_version": "temporal-candidate-revision-benchmark-v1",
        "configuration": {
            "purpose": "candidate selection, not four-model production orchestration",
            "detectors": [
                IFOREST_DETECTOR_ID,
                CONTEXTUAL_BATTERY_DETECTOR_ID,
                TEMPORAL_BATTERY_DETECTOR_ID,
                RPM_STABILITY_DETECTOR_ID,
            ],
            "rpm_candidate": config.to_dict(),
            "score_policy": "raw detector scores are not compared across families",
        },
        "label_metrics": label_metrics_policy(manifest),
        "sessions": session_results,
        "summary": {
            **benchmark_summary(session_results),
            "by_detector": detector_summary_by_id(session_results),
            "rpm_candidate_coverage": detector_coverage_summary(session_results, RPM_STABILITY_DETECTOR_ID, "rpm_stability"),
            "battery_candidate_coverage": detector_coverage_summary(session_results, TEMPORAL_BATTERY_DETECTOR_ID, "temporal"),
            "pairwise_disagreements": pairwise_disagreement_summary(session_results),
            "four_way_states": summarize_four_way(session_results),
            "rpm_event_overlap": summarize_rpm_event_overlap(session_results, window_rows),
        },
    }
    return benchmark, window_rows, rpm_results


def build_rpm_review_manifest(
    manifest: dict[str, Any],
    eval_cache: dict[str, CachedSession],
    window_rows: list[dict[str, Any]],
    rpm_results: dict[str, Any],
    config: RpmStabilityConfig,
) -> dict[str, Any]:
    by_id = {item["session_id"]: item for item in manifest["sessions"]}
    rows_by_session = group_window_rows_by_session(window_rows)
    items = []
    categories: dict[str, str] = {}
    for sid, result in sorted(rpm_results.items()):
        cached = eval_cache[sid]
        for index, event in enumerate(anomaly_events(result.windows, cached.frame_data), start=1):
            rows = rows_by_session.get(sid, [])
            overlap = event_overlap(rows, event)
            item_categories = rpm_event_categories(result.windows, event, overlap)
            review_id = f"rpm-temporal-event-{len(items) + 1:03d}"
            item = {
                "review_item_id": review_id,
                "item_type": "rpm_stability_event",
                "session_id": sid,
                "event_index_within_session": index,
                "provenance": session_provenance(by_id.get(sid, cached.item)),
                "event": event,
                "categories": item_categories,
                "rpm_sequence_summary": rpm_sequence_summary(cached.feature_frame, event),
                "tps_context_state": tps_context_summary(cached.feature_frame, event),
                "rpm_baseline_statistics": rpm_state_before_event(result.windows, event),
                "current_variability_statistics": rpm_event_statistics(result.windows, event),
                "overlap": {
                    "isolation_forest": overlap.get("iforest_anomaly_window_count"),
                    "contextual_battery": overlap.get("contextual_anomaly_window_count"),
                    "temporal_battery_shift": detector_overlap(rows, event, TEMPORAL_BATTERY_DETECTOR_ID),
                    "rpm_stability": detector_overlap(rows, event, RPM_STABILITY_DETECTOR_ID),
                },
                "prediction_source": {
                    "detector_id": RPM_STABILITY_DETECTOR_ID,
                    "model_version": result.identity.model_version,
                    "configuration": config.to_dict(),
                    "fault_label_assigned": False,
                },
                "review_annotation": empty_review_annotation(),
            }
            items.append(item)
            for category in item_categories:
                categories.setdefault(category, review_id)
    return {
        "schema_version": "rpm-temporal-review-manifest-v1",
        "annotation_policy": {
            "allowed_observable_categories": [
                "stable RPM behavior",
                "sustained RPM variability increase",
                "transient",
                "context-transition artifact",
                "acquisition artifact",
                "insufficient evidence",
            ],
            "mechanical_root_causes_assigned": False,
        },
        "rpm_event_count": len(items),
        "representative_categories": dict(sorted(categories.items())),
        "items": items,
    }


def run_parameter_sensitivity(eval_cache: dict[str, CachedSession], baseline_config: RpmStabilityConfig) -> dict[str, Any]:
    variants = [
        ("baseline", baseline_config),
        ("lower_ewma_alpha", RpmStabilityConfig(ewma_alpha=0.2)),
        ("higher_ewma_alpha", RpmStabilityConfig(ewma_alpha=0.5)),
        ("lower_threshold", RpmStabilityConfig(robust_z_threshold=2.5)),
        ("higher_threshold", RpmStabilityConfig(robust_z_threshold=3.5)),
        ("shorter_warmup", RpmStabilityConfig(min_warmup_windows=6)),
        ("longer_warmup", RpmStabilityConfig(min_warmup_windows=10)),
        ("shorter_persistence", RpmStabilityConfig(min_persistence_windows=3)),
        ("longer_persistence", RpmStabilityConfig(min_persistence_windows=5)),
        ("lower_min_increase", RpmStabilityConfig(min_dispersion_increase_rpm=200.0)),
        ("higher_min_increase", RpmStabilityConfig(min_dispersion_increase_rpm=300.0)),
    ]
    evaluations = []
    baseline_events: list[dict[str, Any]] = []
    for variant_id, config in variants:
        evaluation = evaluate_rpm_config(eval_cache, config)
        if variant_id == "baseline":
            baseline_events = evaluation["events"]
        evaluations.append({"variant_id": variant_id, "configuration": config.to_dict(), **evaluation})
    event_counts = [item["event_count"] for item in evaluations if item["variant_id"] != "baseline"]
    persistence = []
    for event in baseline_events:
        persisted = sum(
            1
            for evaluation in evaluations
            if evaluation["variant_id"] != "baseline" and event_persists(event, evaluation["events"])
        )
        persistence.append(
            {
                **event,
                "persisted_variant_count": persisted,
                "variant_count": len(evaluations) - 1,
                "persistence_ratio": round(persisted / max(1, len(evaluations) - 1), 6),
            }
        )
    baseline_count = len(baseline_events)
    relative_range = None
    if event_counts and baseline_count:
        relative_range = round((max(event_counts) - min(event_counts)) / baseline_count, 6)
    low_persistence = [event for event in persistence if event["persistence_ratio"] < 0.75]
    sensitive = bool(
        relative_range is not None
        and relative_range > 0.75
        or (len(low_persistence) / max(1, len(persistence))) > 0.35
    )
    return {
        "schema_version": "rpm-temporal-parameter-sensitivity-v1",
        "method": "small predefined parameter neighborhood; no tuning against labels",
        "evaluations": evaluations,
        "event_count_stability": {
            "baseline_event_count": baseline_count,
            "variant_min": min(event_counts) if event_counts else None,
            "variant_median": statistics.median(event_counts) if event_counts else None,
            "variant_max": max(event_counts) if event_counts else None,
            "relative_range_vs_baseline": relative_range,
        },
        "baseline_event_persistence": persistence,
        "low_persistence_event_count": len(low_persistence),
        "sensitivity_interpretation": {
            "sensitive_to_reasonable_parameter_changes": sensitive,
            "reason": (
                "RPM event conclusions changed substantially across the predefined parameter neighborhood."
                if sensitive
                else "RPM event conclusions were stable across the predefined parameter neighborhood."
            ),
        },
    }


def select_temporal_candidate(
    candidate_a: dict[str, Any],
    benchmark: dict[str, Any],
    review: dict[str, Any],
    sensitivity: dict[str, Any],
    target_audit: dict[str, Any],
) -> dict[str, Any]:
    rpm_overlap = benchmark["summary"]["rpm_event_overlap"]
    rpm_coverage = benchmark["summary"]["rpm_candidate_coverage"]
    rpm_events = int(review["rpm_event_count"])
    rpm_core3_only = int(rpm_overlap.get("core3_only_event_count") or 0)
    rpm_sensitive = bool(sensitivity["sensitivity_interpretation"]["sensitive_to_reasonable_parameter_changes"])
    artifact_count = sum(
        1
        for item in review.get("items", [])
        if "context-transition artifact" in item.get("categories", [])
        or "transient" in item.get("categories", [])
        or "acquisition artifact" in item.get("categories", [])
    )
    rpm_artifact_dominated = rpm_events > 0 and artifact_count / rpm_events > 0.5
    rpm_good = (
        target_audit.get("feasible")
        and rpm_events > 0
        and rpm_core3_only > 0
        and not rpm_sensitive
        and not rpm_artifact_dominated
    )
    battery_good = (
        candidate_a.get("available")
        and int(candidate_a.get("event_count") or 0) > 0
        and int(candidate_a.get("core3_only_event_count") or 0) > 0
        and candidate_a.get("parameter_sensitive") is False
    )
    if not target_audit.get("feasible"):
        selection = "insufficient_data"
    elif rpm_good:
        selection = "rpm_candidate_advance"
    elif battery_good:
        selection = "battery_candidate_retain"
    elif rpm_events == 0 and int(candidate_a.get("event_count") or 0) == 0:
        selection = "insufficient_data"
    else:
        selection = "both_reject"
    return {
        "schema_version": "temporal-candidate-selection-v1",
        "selection": selection,
        "candidate_a_temporal_battery_shift": candidate_a,
        "candidate_b_temporal_rpm_stability": {
            "event_count": rpm_events,
            "core3_only_event_count": rpm_core3_only,
            "coverage": rpm_coverage,
            "parameter_sensitive": rpm_sensitive,
            "artifact_category_event_count": artifact_count,
            "artifact_dominated": rpm_artifact_dominated,
            "representative_categories": review.get("representative_categories"),
        },
        "rationale": selection_rationale(selection),
        "production_registration_changed": False,
        "supervised_accuracy_claims_made": False,
    }


def append_window_rows(
    rows: list[dict[str, Any]],
    session_id: str,
    cached: CachedSession,
    results: list[Any],
) -> None:
    for row_number in range(len(cached.feature_frame)):
        feature_row = cached.feature_frame.iloc[row_number]
        rows.append(
            {
                "session_id": session_id,
                "window_number": row_number,
                "window_index": _int_or_none(feature_row.get("window_index")),
                "start_frame_index": _int_or_none(feature_row.get("start_frame_index")),
                "end_frame_index": _int_or_none(feature_row.get("end_frame_index")),
                "telemetry": {
                    "rpm_median": _float_or_none(feature_row.get("rpm_median")),
                    "rpm_std": _float_or_none(feature_row.get("rpm_std")),
                    "rpm_range": _float_or_none(feature_row.get("rpm_range")),
                    "tps_raw_median": _float_or_none(feature_row.get("tps_raw_median")),
                    "tps_voltage_median": _float_or_none(feature_row.get("tps_voltage_median")),
                    "battery_voltage_median": _float_or_none(feature_row.get("battery_voltage_median")),
                },
                "detectors": {
                    result.identity.detector_id: result.window_payload(row_number)
                    for result in results
                },
            }
        )


def detector_coverage_summary(session_results: list[dict[str, Any]], detector_id: str, metadata_prefix: str) -> dict[str, Any]:
    coverage = Counter()
    for session in session_results:
        for detector in session.get("detectors", []):
            if detector.get("detector_id") != detector_id:
                continue
            metadata = detector.get("metadata") or {}
            coverage["applicable_window_count"] += int(metadata.get("applicable_window_count") or detector.get("scored_window_count") or 0)
            coverage["warmup_window_count"] += int(metadata.get("warmup_window_count") or 0)
            coverage["skipped_window_count"] += int(metadata.get("skipped_window_count") or 0)
            coverage["anomaly_window_count"] += int(detector.get("anomaly_window_count") or 0)
            coverage["event_count"] += int((detector.get("events") or {}).get("count") or 0)
    total = coverage["applicable_window_count"] + coverage["warmup_window_count"] + coverage["skipped_window_count"]
    return {
        **dict(coverage),
        "total_window_count": total,
        "applicable_ratio": None if total == 0 else round(coverage["applicable_window_count"] / total, 6),
        "warmup_ratio": None if total == 0 else round(coverage["warmup_window_count"] / total, 6),
        "skipped_ratio": None if total == 0 else round(coverage["skipped_window_count"] / total, 6),
    }


def pairwise_disagreement_summary(session_results: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[tuple[str, str], dict[str, int]] = {}
    for session in session_results:
        for pair in session.get("detector_disagreements", {}).get("pairs", []):
            key = (pair["left_detector_id"], pair["right_detector_id"])
            row = pairs.setdefault(
                key,
                {
                    "compared_windows": 0,
                    "unavailable_windows": 0,
                    "window_disagreement_count": 0,
                    "event_count_delta": 0,
                },
            )
            row["compared_windows"] += int(pair.get("compared_windows") or 0)
            row["unavailable_windows"] += int(pair.get("unavailable_windows") or 0)
            row["window_disagreement_count"] += int(pair.get("window_disagreement_count") or 0)
            row["event_count_delta"] += int(pair.get("event_count_delta") or 0)
    return {
        f"{left}__{right}": {
            **payload,
            "window_disagreement_ratio": (
                None if payload["compared_windows"] == 0 else round(payload["window_disagreement_count"] / payload["compared_windows"], 6)
            ),
        }
        for (left, right), payload in sorted(pairs.items())
    }


def four_way_states(results: list[Any]) -> dict[str, Any]:
    detector_ids = [
        IFOREST_DETECTOR_ID,
        CONTEXTUAL_BATTERY_DETECTOR_ID,
        TEMPORAL_BATTERY_DETECTOR_ID,
        RPM_STABILITY_DETECTOR_ID,
    ]
    by_id = {result.identity.detector_id: result for result in results}
    if not all(detector_id in by_id and by_id[detector_id].status == "ok" for detector_id in detector_ids):
        return {"available": False, "reason": "one or more detectors unavailable"}
    window_count = min(len(by_id[detector_id].windows) for detector_id in detector_ids)
    counts: Counter[str] = Counter()
    unavailable = 0
    compared = 0
    for index in range(window_count):
        values = []
        for detector_id in detector_ids:
            result = by_id[detector_id]
            values.append(_bool_or_none(result.windows.iloc[index][result.is_anomaly_column]))
        if any(value is None for value in values):
            unavailable += 1
            continue
        compared += 1
        counts[
            "iforest={}|contextual={}|battery_temporal={}|rpm_temporal={}".format(
                *(int(value) for value in values)
            )
        ] += 1
    return {"available": True, "compared_windows": compared, "unavailable_windows": unavailable, "states": dict(sorted(counts.items()))}


def summarize_four_way(session_results: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    compared = 0
    unavailable = 0
    for session in session_results:
        states = session.get("four_way_states", {})
        if not states.get("available"):
            continue
        compared += int(states.get("compared_windows") or 0)
        unavailable += int(states.get("unavailable_windows") or 0)
        counts.update(states.get("states", {}))
    return {"compared_windows": compared, "unavailable_windows": unavailable, "states": dict(sorted(counts.items()))}


def summarize_rpm_event_overlap(session_results: list[dict[str, Any]], window_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows_by_session = group_window_rows_by_session(window_rows)
    counts: Counter[str] = Counter()
    total = 0
    for session in session_results:
        sid = session["session_id"]
        rpm = next((det for det in session.get("detectors", []) if det.get("detector_id") == RPM_STABILITY_DETECTOR_ID), None)
        if not rpm:
            continue
        for event in (rpm.get("events") or {}).get("items", []):
            total += 1
            overlap = event_overlap(rows_by_session.get(sid, []), event)
            battery_temporal_count = detector_overlap(rows_by_session.get(sid, []), event, TEMPORAL_BATTERY_DETECTOR_ID)
            if overlap["iforest_anomaly_window_count"] or overlap["contextual_anomaly_window_count"] or battery_temporal_count:
                if overlap["iforest_anomaly_window_count"]:
                    counts["core1_overlap_event_count"] += 1
                if overlap["contextual_anomaly_window_count"]:
                    counts["core2_overlap_event_count"] += 1
                if battery_temporal_count:
                    counts["battery_temporal_overlap_event_count"] += 1
            else:
                counts["core3_only_event_count"] += 1
    for key in [
        "core1_overlap_event_count",
        "core2_overlap_event_count",
        "battery_temporal_overlap_event_count",
        "core3_only_event_count",
    ]:
        counts.setdefault(key, 0)
    return {"rpm_event_count": total, **dict(sorted(counts.items()))}


def evaluate_rpm_config(eval_cache: dict[str, CachedSession], config: RpmStabilityConfig) -> dict[str, Any]:
    events = []
    status_counts: Counter[str] = Counter()
    anomaly_windows = 0
    for sid, cached in sorted(eval_cache.items()):
        result = ModelHarness([RpmStabilityDetector(config)]).run(
            cached.feature_frame,
            DetectorContext(
                telemetry_schema_version=cached.item.get("telemetry_schema_version"),
                signal_columns=tuple(cached.signal_columns),
            ),
        ).results[0]
        status_counts[result.status] += 1
        anomaly_windows += int(result.anomaly_window_count or 0)
        if result.status != "ok":
            continue
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
        "anomaly_window_count": anomaly_windows,
        "status_counts": dict(sorted(status_counts.items())),
        "events": events,
    }


def event_persists(event: dict[str, Any], candidates: list[dict[str, Any]]) -> bool:
    for candidate in candidates:
        if candidate["session_id"] != event["session_id"]:
            continue
        if intervals_overlap(
            int(event["start_window_index"]),
            int(event["end_window_index"]),
            int(candidate["start_window_index"]),
            int(candidate["end_window_index"]),
        ):
            return True
    return False


def closed_throttle_runs(session_id: str, feature_frame: pd.DataFrame, config: RpmStabilityConfig) -> list[dict[str, Any]]:
    missing = [feature for feature in RPM_STABILITY_REQUIRED_FEATURES if feature not in feature_frame.columns]
    if missing:
        return []
    flags = [
        bool(
            _float_or_nan(row["rpm_median"]) >= config.min_engine_running_rpm
            and _float_or_nan(row["tps_raw_median"]) <= config.closed_tps_raw_max
            and _float_or_nan(row["tps_voltage_median"]) <= config.closed_tps_voltage_max
        )
        for _, row in feature_frame.iterrows()
    ]
    runs = []
    active = False
    start = 0
    for index, flag in enumerate([*flags, False]):
        if flag and not active:
            active = True
            start = index
        elif active and not flag:
            chunk = feature_frame.iloc[start:index]
            runs.append(
                {
                    "session_id": session_id,
                    "start_window_index": start,
                    "end_window_index": index - 1,
                    "window_count": index - start,
                    "rpm_std_median": _float_or_none(chunk["rpm_std"].median()),
                    "rpm_std_max": _float_or_none(chunk["rpm_std"].max()),
                    "rpm_median_range": _float_or_none(chunk["rpm_median"].max() - chunk["rpm_median"].min()),
                }
            )
            active = False
    return runs


def rpm_event_categories(windows: pd.DataFrame, event: dict[str, Any], overlap: dict[str, Any]) -> list[str]:
    categories = ["sustained RPM variability increase"]
    start = int(event["start_window_index"])
    end = int(event["end_window_index"])
    if start <= 12:
        categories.append("transient")
    statuses = windows["rpm_stability_status"].tolist()
    if any(status != "applicable" for status in statuses[start : end + 1]):
        categories.append("context-transition artifact")
    chunk = windows.iloc[start : end + 1]
    checksum = (
        pd.to_numeric(chunk["checksum_failure_ratio"], errors="coerce")
        if "checksum_failure_ratio" in chunk.columns
        else pd.Series([0.0] * len(chunk), index=chunk.index)
    )
    if (checksum.fillna(0) > 0).any():
        categories.append("acquisition artifact")
    if overlap["iforest_anomaly_window_count"] == 0 and overlap["contextual_anomaly_window_count"] == 0:
        categories.append("Core3-only RPM temporal event")
    return categories


def rpm_sequence_summary(feature_frame: pd.DataFrame, event: dict[str, Any]) -> dict[str, Any]:
    chunk = feature_frame.iloc[int(event["start_window_index"]) : int(event["end_window_index"]) + 1]
    return {
        "rpm_median": numeric_summary(chunk["rpm_median"].astype(float).tolist()),
        "rpm_std": numeric_summary(chunk["rpm_std"].astype(float).tolist()),
        "rpm_range": numeric_summary(chunk["rpm_range"].astype(float).tolist()),
        "rpm_mean_absolute_diff": numeric_summary(chunk["rpm_mean_absolute_diff"].astype(float).tolist()),
    }


def tps_context_summary(feature_frame: pd.DataFrame, event: dict[str, Any]) -> dict[str, Any]:
    chunk = feature_frame.iloc[int(event["start_window_index"]) : int(event["end_window_index"]) + 1]
    return {
        "tps_raw_median": numeric_summary(chunk["tps_raw_median"].astype(float).tolist()),
        "tps_voltage_median": numeric_summary(chunk["tps_voltage_median"].astype(float).tolist()),
    }


def rpm_state_before_event(windows: pd.DataFrame, event: dict[str, Any]) -> dict[str, Any] | None:
    start = int(event["start_window_index"])
    if start <= 0:
        return None
    row = windows.iloc[start - 1]
    return {
        "window_index": _int_or_none(row.get("window_index")),
        "status": row.get("rpm_stability_status"),
        "observed_center": _float_or_none(row.get("rpm_observed_center")),
        "observed_dispersion": _float_or_none(row.get("rpm_observed_dispersion")),
        "baseline_center": _float_or_none(row.get("rpm_baseline_center")),
        "baseline_dispersion": _float_or_none(row.get("rpm_baseline_dispersion")),
        "baseline_dispersion_scale": _float_or_none(row.get("rpm_baseline_dispersion_scale")),
        "ewma_dispersion": _float_or_none(row.get("rpm_ewma_dispersion")),
        "change_statistic": _float_or_none(row.get("rpm_stability_change_statistic")),
        "persistence_count": _int_or_none(row.get("rpm_stability_persistence_count")),
    }


def rpm_event_statistics(windows: pd.DataFrame, event: dict[str, Any]) -> dict[str, Any]:
    chunk = windows.iloc[int(event["start_window_index"]) : int(event["end_window_index"]) + 1]
    return {
        "max_change_statistic": _float_or_none(pd.to_numeric(chunk["rpm_stability_change_statistic"], errors="coerce").max()),
        "max_dispersion_increase": _float_or_none(pd.to_numeric(chunk["rpm_dispersion_increase"], errors="coerce").max()),
        "max_persistence_count": _int_or_none(pd.to_numeric(chunk["rpm_stability_persistence_count"], errors="coerce").max()),
        "event_ids": sorted(set(chunk["rpm_stability_event_id"].dropna().astype(str).tolist())),
    }


def detector_overlap(window_rows: list[dict[str, Any]], event: dict[str, Any], detector_id: str) -> int:
    start = int(event["start_window_index"])
    end = int(event["end_window_index"])
    count = 0
    for row in window_rows:
        window_number = int(row.get("window_number", row.get("window_index", -1)))
        if not start <= window_number <= end:
            continue
        payload = row.get("detectors", {}).get(detector_id, {})
        if payload.get("status") == "ok" and _bool_or_none(payload.get("is_anomaly")) is True:
            count += 1
    return count


def empty_review_annotation() -> dict[str, Any]:
    return {
        "selected": None,
        "reviewer": None,
        "reviewed_at": None,
        "notes": None,
        "fault_label_assigned": False,
    }


def selection_rationale(selection: str) -> str:
    return {
        "rpm_candidate_advance": "Candidate B produced stable, traceable, materially distinct RPM temporal events.",
        "battery_candidate_retain": "Candidate A remains the only stable candidate with distinct temporal value.",
        "both_reject": "Neither temporal candidate currently demonstrates sufficient distinct event-level value.",
        "insufficient_data": "Available telemetry is insufficient for a defensible temporal candidate selection.",
    }[selection]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _float_or_nan(value: Any) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return math.nan
    return resolved if math.isfinite(resolved) else math.nan


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def write_markdown_report(
    path: Path,
    target_audit: dict[str, Any],
    benchmark: dict[str, Any],
    review: dict[str, Any],
    sensitivity: dict[str, Any],
    selection: dict[str, Any],
) -> None:
    rpm = benchmark["summary"]["by_detector"].get(RPM_STABILITY_DETECTOR_ID, {})
    battery = benchmark["summary"]["by_detector"].get(TEMPORAL_BATTERY_DETECTOR_ID, {})
    lines = [
        "# H4.1 Temporal Candidate Revision Report",
        "",
        "## Target Audit",
        "",
        f"- Feasible: `{target_audit['feasible']}`",
        f"- Target: {target_audit['target']}",
        f"- Adequate closed-throttle runs: `{target_audit['closed_throttle_runs']['adequate_for_candidate']}`",
        "- Candidate B uses verified RPM/TPS only; battery voltage is excluded from its target.",
        "",
        "## Comparative Benchmark",
        "",
        f"- Candidate A statuses: `{battery.get('status_counts')}`",
        f"- Candidate B statuses: `{rpm.get('status_counts')}`",
        f"- Candidate B coverage: `{benchmark['summary']['rpm_candidate_coverage']}`",
        f"- Candidate B event overlap: `{benchmark['summary']['rpm_event_overlap']}`",
        f"- Pairwise disagreements: `{benchmark['summary']['pairwise_disagreements']}`",
        f"- Four-way states: `{benchmark['summary']['four_way_states']['states']}`",
        "",
        "## Review",
        "",
        f"- Candidate B review events: `{review['rpm_event_count']}`",
        f"- Review categories: `{review['representative_categories']}`",
        "",
        "## Stability",
        "",
        f"- Candidate B variant event count range: {sensitivity['event_count_stability']['variant_min']}..{sensitivity['event_count_stability']['variant_max']}",
        f"- Low-persistence Candidate B events: `{sensitivity['low_persistence_event_count']}`",
        f"- Parameter sensitive: `{sensitivity['sensitivity_interpretation']['sensitive_to_reasonable_parameter_changes']}`",
        "",
        "## Selection",
        "",
        f"- Selection: `{selection['selection']}`",
        f"- Rationale: {selection['rationale']}",
        "- Production inference remains Isolation Forest only.",
        "",
        "## Outputs",
        "",
        "- `target_audit.json`",
        "- `candidate_benchmark_results.json` and `shadow_window_results.jsonl`",
        "- `rpm_review_manifest.json`",
        "- `rpm_parameter_sensitivity.json`",
        "- `candidate_selection.json`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
