from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from app.config import Settings
from app.evaluation.contextual_shadow import (
    default_h3_paths,
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
from app.ml.artifacts import load_model_bundle
from app.ml.contextual_detector import (
    CONTEXTUAL_BATTERY_DETECTOR_ID,
    ContextualBatteryVoltageDetector,
    assign_operating_context,
)
from app.ml.harness import DetectorContext, ModelHarness
from app.ml.iforest_detector import IsolationForestDetector
from app.ml.temporal_detector import (
    TEMPORAL_BATTERY_DETECTOR_ID,
    TemporalBatteryShiftConfig,
    TemporalBatteryShiftDetector,
)


IFOREST_DETECTOR_ID = "isolation_forest"


@dataclass(frozen=True, slots=True)
class CachedSession:
    item: dict[str, Any]
    frame_data: pd.DataFrame
    feature_frame: pd.DataFrame
    signal_columns: list[str]


def default_h4_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h4")


def run_all(paths: EvaluationPaths, h3_dir: Path | None = None) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h3_dir = (h3_dir or paths.repo_root / "data" / "evaluation" / "h3").resolve()
    manifest = load_manifest(paths, resolved_h3_dir)
    contextual_reference = read_json(resolved_h3_dir / "contextual_reference.json")
    settings = Settings(data_dir=paths.data_dir, model_dir=paths.model_dir)
    selected_items, excluded = select_shadow_evaluation_sessions(manifest)
    eval_cache = load_eval_cache(paths, settings, selected_items)
    config = TemporalBatteryShiftConfig()

    feasibility = build_temporal_feasibility_audit(manifest, eval_cache, config, excluded)
    benchmark, window_rows, temporal_results = run_shadow_benchmark(paths, manifest, contextual_reference, eval_cache, config)
    review_manifest = build_review_manifest(manifest, eval_cache, benchmark, window_rows, temporal_results, config)
    sensitivity = run_parameter_sensitivity(eval_cache, config)
    decision = build_decision(feasibility, benchmark, review_manifest, sensitivity)

    outputs = {
        "feasibility_audit": paths.output_dir / "feasibility_audit.json",
        "benchmark": paths.output_dir / "shadow_benchmark_results.json",
        "window_results": paths.output_dir / "shadow_window_results.jsonl",
        "review_manifest": paths.output_dir / "review_manifest.json",
        "parameter_sensitivity": paths.output_dir / "parameter_sensitivity.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["feasibility_audit"], feasibility)
    write_json(outputs["benchmark"], benchmark)
    write_jsonl(outputs["window_results"], window_rows)
    write_json(outputs["review_manifest"], review_manifest)
    write_json(outputs["parameter_sensitivity"], sensitivity)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], feasibility, benchmark, review_manifest, sensitivity, decision)
    return outputs


def load_manifest(paths: EvaluationPaths, h3_dir: Path) -> dict[str, Any]:
    manifest_path = h3_dir / "evaluation_manifest.json"
    if manifest_path.exists():
        return read_json(manifest_path)
    return build_evaluation_manifest(paths)


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


def build_temporal_feasibility_audit(
    manifest: dict[str, Any],
    eval_cache: dict[str, CachedSession],
    config: TemporalBatteryShiftConfig,
    excluded_sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    window_durations: list[float] = []
    window_steps: list[float] = []
    start_frame_steps: list[int] = []
    missing_window_gaps = 0
    stable_runs: list[dict[str, Any]] = []
    session_durations = []

    for sid, cached in eval_cache.items():
        item = cached.item
        session_durations.append(item.get("recording_duration_ms"))
        frame_index = cached.frame_data.set_index("frame_index") if "frame_index" in cached.frame_data else pd.DataFrame()
        start_times: list[float] = []
        for _, row in cached.feature_frame.iterrows():
            start_frame = int(row["start_frame_index"])
            end_frame = int(row["end_frame_index"])
            if start_frame in frame_index.index and end_frame in frame_index.index:
                start_ms = float(frame_index.loc[start_frame, "relative_time_ms"])
                end_ms = float(frame_index.loc[end_frame, "relative_time_ms"])
                start_times.append(start_ms)
                window_durations.append(max(0.0, end_ms - start_ms))
        window_steps.extend([right - left for left, right in zip(start_times, start_times[1:])])
        starts = [int(value) for value in cached.feature_frame["start_frame_index"].tolist()]
        start_frame_steps.extend([right - left for left, right in zip(starts, starts[1:])])
        missing_window_gaps += sum(1 for step in start_frame_steps if step > config.max_start_frame_step)
        stable_runs.extend(stable_context_runs(sid, cached.feature_frame, config))

    needed = config.min_warmup_windows + config.min_persistence_windows
    adequate_runs = [run for run in stable_runs if run["window_count"] >= needed]
    v2_ready = [
        item for item in manifest["sessions"]
        if item.get("telemetry_schema_version") == "canonical-telemetry-v2"
        and {"rpm", "tps_voltage", "tps_raw", "battery_voltage"}.issubset(set(item.get("verified_signals", [])))
    ]
    return {
        "schema_version": "temporal-feasibility-audit-v1",
        "candidate_target": "sustained intra-session battery-voltage shift within stable RPM/TPS operating contexts",
        "verified_signals_used": ["rpm", "tps_voltage", "tps_raw", "battery_voltage"],
        "unsupported_scope": [
            "cross-session degradation or long-term memory",
            "generic mechanical fault diagnosis",
            "temporal modeling through context transitions",
        ],
        "data_availability": {
            "v2_sessions_with_verified_required_signals": len(v2_ready),
            "evaluated_session_count": len(eval_cache),
            "excluded_sessions": excluded_sessions,
        },
        "cadence": {
            "window_duration_ms": numeric_summary(window_durations),
            "window_start_step_ms": numeric_summary(window_steps),
            "window_start_frame_step": numeric_summary(start_frame_steps),
            "missing_or_irregular_window_gap_count": missing_window_gaps,
        },
        "session_duration_ms": numeric_summary([value for value in session_durations if value is not None]),
        "stable_context_runs": {
            "total": len(stable_runs),
            "adequate_for_temporal_detection": len(adequate_runs),
            "required_run_windows": needed,
            "run_length_windows": numeric_summary([run["window_count"] for run in stable_runs]),
            "contexts": dict(sorted(Counter(run["context"] for run in stable_runs).items())),
        },
        "continuity_policy": {
            "state_scope": "within one session only",
            "reset_on_context_change": True,
            "reset_on_start_frame_gap_gt": config.max_start_frame_step,
            "no_cross_session_continuity_assumed": True,
        },
        "feasible": len(adequate_runs) > 0,
        "feasibility_reason": (
            "Stable context runs are long enough to build intra-session temporal baselines."
            if adequate_runs
            else "Stable context runs are too short for warm-up plus persistence requirements."
        ),
        "detector_configuration": config.to_dict(),
    }


def run_shadow_benchmark(
    paths: EvaluationPaths,
    manifest: dict[str, Any],
    contextual_reference: dict[str, Any],
    eval_cache: dict[str, CachedSession],
    config: TemporalBatteryShiftConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    bundle = load_model_bundle(paths.model_dir)
    session_results: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    temporal_results: dict[str, Any] = {}

    for sid, cached in sorted(eval_cache.items()):
        context = DetectorContext(
            telemetry_schema_version=cached.item.get("telemetry_schema_version"),
            signal_columns=tuple(cached.signal_columns),
        )
        started = perf_counter()
        harness_result = ModelHarness(
            [
                IsolationForestDetector(bundle),
                ContextualBatteryVoltageDetector(contextual_reference),
                TemporalBatteryShiftDetector(config),
            ]
        ).run(cached.feature_frame, context)
        harness_latency_ms = round((perf_counter() - started) * 1000.0, 6)
        detectors = []
        for result in harness_result.results:
            latency_ms = float(result.metadata.get("harness_latency_ms", harness_latency_ms))
            detectors.append(
                detector_benchmark_payload(
                    result,
                    cached.feature_frame,
                    cached.frame_data,
                    cached.item.get("recording_duration_ms"),
                    latency_ms,
                )
            )
            if result.identity.detector_id == TEMPORAL_BATTERY_DETECTOR_ID:
                temporal_results[sid] = result

        append_window_rows(window_rows, sid, harness_result.results, cached)
        session_results.append(
            {
                "session_id": sid,
                "dataset_kind": cached.item.get("dataset_kind"),
                "telemetry_schema_version": cached.item.get("telemetry_schema_version"),
                "training_evaluation_split": cached.item.get("training_evaluation_split"),
                "window_count": int(len(cached.feature_frame)),
                "recording_duration_ms": cached.item.get("recording_duration_ms"),
                "detectors": detectors,
                "detector_disagreements": detector_disagreements(harness_result.results),
                "three_way_agreement": three_way_agreement(harness_result.results),
            }
        )

    benchmark = {
        "schema_version": "temporal-shadow-benchmark-results-v1",
        "configuration": {
            "detectors": [IFOREST_DETECTOR_ID, CONTEXTUAL_BATTERY_DETECTOR_ID, TEMPORAL_BATTERY_DETECTOR_ID],
            "temporal_detector": config.to_dict(),
            "score_policy": "raw detector scores preserved; no cross-detector score normalization",
            "threshold_policy": "production thresholds unchanged; temporal threshold is shadow-only",
        },
        "label_metrics": label_metrics_policy(manifest),
        "sessions": session_results,
        "summary": {
            **benchmark_summary(session_results),
            "by_detector": detector_summary_by_id(session_results),
            "temporal_coverage": temporal_coverage_summary(session_results),
            "pairwise_disagreements": pairwise_disagreement_summary(session_results),
            "three_way_agreement": summarize_three_way(session_results),
            "temporal_event_overlap": summarize_temporal_event_overlap(session_results, window_rows),
        },
    }
    return benchmark, window_rows, temporal_results


def build_review_manifest(
    manifest: dict[str, Any],
    eval_cache: dict[str, CachedSession],
    benchmark: dict[str, Any],
    window_rows: list[dict[str, Any]],
    temporal_results: dict[str, Any],
    config: TemporalBatteryShiftConfig,
) -> dict[str, Any]:
    by_id = {item["session_id"]: item for item in manifest["sessions"]}
    rows_by_session = group_window_rows_by_session(window_rows)
    items: list[dict[str, Any]] = []
    category_examples: dict[str, str] = {}

    for sid, result in sorted(temporal_results.items()):
        cached = eval_cache[sid]
        for index, event in enumerate(anomaly_events(result.windows, cached.frame_data), start=1):
            rows = rows_by_session.get(sid, [])
            overlap = event_overlap(rows, event)
            categories = temporal_event_categories(result.windows, event, overlap)
            review_id = f"temporal-event-{len(items) + 1:03d}"
            item = {
                "review_item_id": review_id,
                "item_type": "temporal_event",
                "session_id": sid,
                "event_index_within_session": index,
                "provenance": session_provenance(by_id.get(sid, cached.item)),
                "event": event,
                "categories": categories,
                "applicable_operating_context": event_context_summary(result.windows, event),
                "source_telemetry": event_telemetry_summary(cached, event),
                "temporal_state_before_event": temporal_state_before_event(result.windows, event),
                "temporal_event_statistics": temporal_event_statistics(result.windows, event),
                "overlap": overlap,
                "prediction_source": {
                    "detector_id": TEMPORAL_BATTERY_DETECTOR_ID,
                    "model_version": result.identity.model_version,
                    "configuration": config.to_dict(),
                    "fault_label_assigned": False,
                },
                "review_annotation": empty_review_annotation(),
            }
            items.append(item)
            for category in categories:
                category_examples.setdefault(category, review_id)

    return {
        "schema_version": "temporal-review-manifest-v1",
        "annotation_policy": {
            "detector_predictions_are_not_fault_labels": True,
            "mechanical_causes_are_not_assigned": True,
            "allowed_annotations": [
                "plausible operating variation",
                "suspicious temporal voltage behavior",
                "likely acquisition/data artifact",
                "likely session-start or context-boundary artifact",
                "insufficient evidence",
                "requires physical verification",
            ],
        },
        "temporal_event_count": len(items),
        "representative_categories": dict(sorted(category_examples.items())),
        "items": items,
    }


def run_parameter_sensitivity(
    eval_cache: dict[str, CachedSession],
    baseline_config: TemporalBatteryShiftConfig,
) -> dict[str, Any]:
    variants = [
        ("baseline", baseline_config),
        ("lower_ewma_alpha", TemporalBatteryShiftConfig(ewma_alpha=0.2)),
        ("higher_ewma_alpha", TemporalBatteryShiftConfig(ewma_alpha=0.5)),
        ("lower_shift_threshold", TemporalBatteryShiftConfig(shift_threshold_volts=0.3)),
        ("higher_shift_threshold", TemporalBatteryShiftConfig(shift_threshold_volts=0.5)),
        ("shorter_warmup", TemporalBatteryShiftConfig(min_warmup_windows=6)),
        ("longer_warmup", TemporalBatteryShiftConfig(min_warmup_windows=10)),
        ("shorter_persistence", TemporalBatteryShiftConfig(min_persistence_windows=3)),
        ("longer_persistence", TemporalBatteryShiftConfig(min_persistence_windows=5)),
    ]
    evaluations = []
    baseline_events: list[dict[str, Any]] = []
    for name, config in variants:
        evaluation = evaluate_temporal_config(eval_cache, config)
        if name == "baseline":
            baseline_events = evaluation["events"]
        evaluations.append({"variant_id": name, "configuration": config.to_dict(), **evaluation})

    persistence = []
    for event in baseline_events:
        persisted_count = sum(
            1
            for evaluation in evaluations
            if evaluation["variant_id"] != "baseline"
            and event_persists(event, evaluation["events"])
        )
        persistence.append(
            {
                **event,
                "persisted_variant_count": persisted_count,
                "variant_count": len(evaluations) - 1,
                "persistence_ratio": round(persisted_count / max(1, len(evaluations) - 1), 6),
            }
        )
    event_counts = [item["event_count"] for item in evaluations if item["variant_id"] != "baseline"]
    baseline_count = len(baseline_events)
    relative_range = None
    if event_counts and baseline_count:
        relative_range = round((max(event_counts) - min(event_counts)) / baseline_count, 6)
    low_persistence = [event for event in persistence if event["persistence_ratio"] < 0.75]
    sensitive = bool(
        relative_range is not None
        and relative_range > 0.5
        or (len(low_persistence) / max(1, len(persistence))) > 0.35
    )
    return {
        "schema_version": "temporal-parameter-sensitivity-v1",
        "method": "small predefined parameter neighborhood; no optimization against labels",
        "baseline_variant": "baseline",
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
                "Event counts or individual events change substantially across the predefined parameter neighborhood."
                if sensitive
                else "Most temporal conclusions persist across the predefined parameter neighborhood."
            ),
        },
    }


def evaluate_temporal_config(eval_cache: dict[str, CachedSession], config: TemporalBatteryShiftConfig) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    anomaly_windows = 0
    events: list[dict[str, Any]] = []
    for sid, cached in sorted(eval_cache.items()):
        result = ModelHarness([TemporalBatteryShiftDetector(config)]).run(
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


def build_decision(
    feasibility: dict[str, Any],
    benchmark: dict[str, Any],
    review_manifest: dict[str, Any],
    sensitivity: dict[str, Any],
) -> dict[str, Any]:
    temporal_summary = benchmark["summary"]["by_detector"].get(TEMPORAL_BATTERY_DETECTOR_ID, {})
    overlap = benchmark["summary"]["temporal_event_overlap"]
    event_count = int(review_manifest["temporal_event_count"])
    core3_only = int(overlap.get("core3_only_event_count") or 0)
    sensitive = sensitivity["sensitivity_interpretation"]["sensitive_to_reasonable_parameter_changes"]
    artifact_count = count_artifact_events(review_manifest)
    artifact_dominated = event_count > 0 and artifact_count / event_count > 0.5
    feasible = bool(feasibility.get("feasible"))
    distinct = core3_only > 0
    meaningful = event_count > 0 and int(temporal_summary.get("scored_window_count") or 0) > 0
    if feasible and meaningful and distinct and not sensitive and not artifact_dominated:
        recommendation = "advance"
    elif feasible and meaningful:
        recommendation = "revise"
    else:
        recommendation = "reject"
    return {
        "schema_version": "temporal-core3-decision-v1",
        "recommendation": recommendation,
        "answers": {
            "detects_temporally_meaningful_phenomenon": meaningful,
            "provides_information_not_represented_by_core1_or_core2": distinct,
            "events_stable_under_parameter_variation": not sensitive,
            "results_dominated_by_start_transition_missing_data_or_artifacts": artifact_dominated,
            "should_remain_part_of_research_harness": recommendation in {"advance", "revise"},
        },
        "basis": {
            "feasible": feasible,
            "temporal_event_count": event_count,
            "core3_only_event_count": core3_only,
            "artifact_category_event_count": artifact_count,
            "parameter_sensitive": sensitive,
            "supervised_accuracy_claims_made": False,
        },
    }


def append_window_rows(
    rows: list[dict[str, Any]],
    session_id: str,
    results: list[Any],
    cached: CachedSession,
) -> None:
    for row_number in range(len(cached.feature_frame)):
        feature_row = cached.feature_frame.iloc[row_number]
        payload = {
            "session_id": session_id,
            "window_number": row_number,
            "window_index": _int_or_none(feature_row.get("window_index")),
            "start_frame_index": _int_or_none(feature_row.get("start_frame_index")),
            "end_frame_index": _int_or_none(feature_row.get("end_frame_index")),
            "operating_context": assign_operating_context(feature_row),
            "telemetry": telemetry_snapshot(feature_row),
            "detectors": {
                result.identity.detector_id: result.window_payload(row_number)
                for result in results
            },
        }
        rows.append(payload)


def three_way_agreement(results: list[Any]) -> dict[str, Any]:
    by_id = {result.identity.detector_id: result for result in results}
    required = [IFOREST_DETECTOR_ID, CONTEXTUAL_BATTERY_DETECTOR_ID, TEMPORAL_BATTERY_DETECTOR_ID]
    if not all(detector_id in by_id and by_id[detector_id].status == "ok" for detector_id in required):
        return {"available": False, "reason": "one or more detectors unavailable"}
    counts: Counter[str] = Counter()
    compared = 0
    unavailable = 0
    window_count = min(len(by_id[detector_id].windows) for detector_id in required)
    for index in range(window_count):
        values = []
        for detector_id in required:
            result = by_id[detector_id]
            value = _bool_or_none(result.windows.iloc[index][result.is_anomaly_column])
            values.append(value)
        if any(value is None for value in values):
            unavailable += 1
            continue
        compared += 1
        key = f"iforest={int(values[0])}|contextual={int(values[1])}|temporal={int(values[2])}"
        counts[key] += 1
    return {"available": True, "compared_windows": compared, "unavailable_windows": unavailable, "states": dict(sorted(counts.items()))}


def summarize_three_way(session_results: list[dict[str, Any]]) -> dict[str, Any]:
    compared = 0
    unavailable = 0
    states: Counter[str] = Counter()
    for session in session_results:
        agreement = session.get("three_way_agreement", {})
        if not agreement.get("available"):
            continue
        compared += int(agreement.get("compared_windows") or 0)
        unavailable += int(agreement.get("unavailable_windows") or 0)
        states.update(agreement.get("states", {}))
    return {"compared_windows": compared, "unavailable_windows": unavailable, "states": dict(sorted(states.items()))}


def temporal_coverage_summary(session_results: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = {
        "applicable_window_count": 0,
        "warmup_window_count": 0,
        "skipped_window_count": 0,
        "anomaly_window_count": 0,
    }
    for session in session_results:
        for detector in session.get("detectors", []):
            if detector.get("detector_id") != TEMPORAL_BATTERY_DETECTOR_ID:
                continue
            metadata = detector.get("metadata") or {}
            coverage["applicable_window_count"] += int(metadata.get("applicable_window_count") or detector.get("scored_window_count") or 0)
            coverage["warmup_window_count"] += int(metadata.get("warmup_window_count") or 0)
            coverage["skipped_window_count"] += int(metadata.get("skipped_window_count") or 0)
            coverage["anomaly_window_count"] += int(detector.get("anomaly_window_count") or 0)
    total = coverage["applicable_window_count"] + coverage["warmup_window_count"] + coverage["skipped_window_count"]
    coverage["total_window_count"] = total
    coverage["applicable_ratio"] = None if total == 0 else round(coverage["applicable_window_count"] / total, 6)
    coverage["warmup_ratio"] = None if total == 0 else round(coverage["warmup_window_count"] / total, 6)
    coverage["skipped_ratio"] = None if total == 0 else round(coverage["skipped_window_count"] / total, 6)
    return coverage


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
                None
                if payload["compared_windows"] == 0
                else round(payload["window_disagreement_count"] / payload["compared_windows"], 6)
            ),
        }
        for (left, right), payload in sorted(pairs.items())
    }


def summarize_temporal_event_overlap(session_results: list[dict[str, Any]], window_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows_by_session = group_window_rows_by_session(window_rows)
    counts: Counter[str] = Counter()
    total = 0
    for session in session_results:
        sid = session["session_id"]
        temporal = next((det for det in session.get("detectors", []) if det.get("detector_id") == TEMPORAL_BATTERY_DETECTOR_ID), None)
        if not temporal:
            continue
        for event in (temporal.get("events") or {}).get("items", []):
            total += 1
            overlap = event_overlap(rows_by_session.get(sid, []), event)
            if overlap["iforest_anomaly_window_count"] and overlap["contextual_anomaly_window_count"]:
                counts["three_detector_agreement_event_count"] += 1
            elif overlap["iforest_anomaly_window_count"]:
                counts["core1_core3_event_count"] += 1
            elif overlap["contextual_anomaly_window_count"]:
                counts["core2_core3_event_count"] += 1
            else:
                counts["core3_only_event_count"] += 1
    for key in [
        "core3_only_event_count",
        "core1_core3_event_count",
        "core2_core3_event_count",
        "three_detector_agreement_event_count",
    ]:
        counts.setdefault(key, 0)
    return {"temporal_event_count": total, **dict(sorted(counts.items()))}


def event_overlap(window_rows: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
    start = int(event["start_window_index"])
    end = int(event["end_window_index"])
    event_rows = [
        row for row in window_rows
        if start <= int(row.get("window_number", row.get("window_index", -1))) <= end
    ]
    iforest_count = 0
    contextual_count = 0
    temporal_count = 0
    for row in event_rows:
        detectors = row.get("detectors", {})
        if _payload_anomaly(detectors.get(IFOREST_DETECTOR_ID)):
            iforest_count += 1
        if _payload_anomaly(detectors.get(CONTEXTUAL_BATTERY_DETECTOR_ID)):
            contextual_count += 1
        if _payload_anomaly(detectors.get(TEMPORAL_BATTERY_DETECTOR_ID)):
            temporal_count += 1
    return {
        "event_window_count": len(event_rows),
        "iforest_anomaly_window_count": iforest_count,
        "contextual_anomaly_window_count": contextual_count,
        "temporal_anomaly_window_count": temporal_count,
    }


def temporal_event_categories(windows: pd.DataFrame, event: dict[str, Any], overlap: dict[str, Any]) -> list[str]:
    categories: list[str] = []
    if overlap["iforest_anomaly_window_count"] and overlap["contextual_anomaly_window_count"]:
        categories.append("three_detector_agreement_event")
    elif overlap["iforest_anomaly_window_count"]:
        categories.append("core1_core3_event")
    elif overlap["contextual_anomaly_window_count"]:
        categories.append("core2_core3_event")
    else:
        categories.append("core3_only_event")
    start = int(event["start_window_index"])
    end = int(event["end_window_index"])
    if start <= 12:
        categories.append("session_start_sensitive_event")
    contexts = windows["temporal_context"].tolist()
    if any(is_context_transition_window(contexts, index) for index in range(start, end + 1)):
        categories.append("likely_transient_or_context_boundary_artifact")
    return categories


def stable_context_runs(session_id: str, feature_frame: pd.DataFrame, config: TemporalBatteryShiftConfig) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    contexts = [assign_operating_context(row) for _, row in feature_frame.iterrows()]
    current = None
    start = 0
    for index, context in enumerate([*contexts, "__END__"]):
        if current is None:
            current = context
            start = index
            continue
        if context == current:
            continue
        if current in config.modeled_contexts:
            chunk = feature_frame.iloc[start:index]
            runs.append(
                {
                    "session_id": session_id,
                    "context": current,
                    "start_window_index": start,
                    "end_window_index": index - 1,
                    "window_count": index - start,
                    "battery_voltage_median_range": _float_or_none(chunk["battery_voltage_median"].max() - chunk["battery_voltage_median"].min()),
                }
            )
        current = context
        start = index
    return runs


def evaluate_event_ids(events: list[dict[str, Any]]) -> set[str]:
    return {event["event_id"] for event in events}


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


def intervals_overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return max(left_start, right_start) <= min(left_end, right_end)


def is_context_transition_window(contexts: list[str], index: int) -> bool:
    if index < 0 or index >= len(contexts):
        return False
    current = contexts[index]
    start = max(0, index - 1)
    end = min(len(contexts) - 1, index + 1)
    return any(contexts[position] != current for position in range(start, end + 1))


def event_context_summary(windows: pd.DataFrame, event: dict[str, Any]) -> dict[str, int]:
    chunk = windows.iloc[int(event["start_window_index"]) : int(event["end_window_index"]) + 1]
    return dict(sorted(Counter(chunk["temporal_context"].dropna().astype(str).tolist()).items()))


def event_telemetry_summary(cached: CachedSession, event: dict[str, Any]) -> dict[str, Any]:
    chunk = cached.feature_frame.iloc[int(event["start_window_index"]) : int(event["end_window_index"]) + 1]
    return {
        "rpm_median": numeric_summary(chunk["rpm_median"].astype(float).tolist()),
        "tps_raw_median": numeric_summary(chunk["tps_raw_median"].astype(float).tolist()),
        "tps_voltage_median": numeric_summary(chunk["tps_voltage_median"].astype(float).tolist()),
        "battery_voltage_median": numeric_summary(chunk["battery_voltage_median"].astype(float).tolist()),
        "battery_voltage_min": numeric_summary(chunk["battery_voltage_min"].astype(float).tolist()) if "battery_voltage_min" in chunk else None,
    }


def temporal_state_before_event(windows: pd.DataFrame, event: dict[str, Any]) -> dict[str, Any] | None:
    start = int(event["start_window_index"])
    if start <= 0:
        return None
    row = windows.iloc[start - 1]
    return {
        "window_index": _int_or_none(row.get("window_index")),
        "temporal_status": row.get("temporal_status"),
        "context": row.get("temporal_context"),
        "observed": _float_or_none(row.get("temporal_observed_value")),
        "baseline": _float_or_none(row.get("temporal_baseline_value")),
        "ewma": _float_or_none(row.get("temporal_ewma_value")),
        "deviation": _float_or_none(row.get("temporal_deviation")),
        "change_statistic": _float_or_none(row.get("temporal_change_statistic")),
        "persistence_count": _int_or_none(row.get("temporal_persistence_count")),
    }


def temporal_event_statistics(windows: pd.DataFrame, event: dict[str, Any]) -> dict[str, Any]:
    chunk = windows.iloc[int(event["start_window_index"]) : int(event["end_window_index"]) + 1]
    return {
        "max_change_statistic": _float_or_none(pd.to_numeric(chunk["temporal_change_statistic"], errors="coerce").max()),
        "max_abs_deviation": _float_or_none(pd.to_numeric(chunk["temporal_deviation"], errors="coerce").abs().max()),
        "max_persistence_count": _int_or_none(pd.to_numeric(chunk["temporal_persistence_count"], errors="coerce").max()),
        "threshold": _float_or_none(pd.to_numeric(chunk["temporal_threshold"], errors="coerce").dropna().iloc[0])
        if pd.to_numeric(chunk["temporal_threshold"], errors="coerce").dropna().size
        else None,
        "event_ids": sorted(set(value for value in chunk["temporal_event_id"].dropna().astype(str).tolist())),
    }


def count_artifact_events(review_manifest: dict[str, Any]) -> int:
    return sum(
        1
        for item in review_manifest.get("items", [])
        if "likely_transient_or_context_boundary_artifact" in item.get("categories", [])
        or "session_start_sensitive_event" in item.get("categories", [])
    )


def group_window_rows_by_session(window_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in window_rows:
        grouped[row["session_id"]].append(row)
    return grouped


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


def telemetry_snapshot(row: pd.Series) -> dict[str, Any]:
    return {
        "rpm_median": _float_or_none(row.get("rpm_median")),
        "tps_raw_median": _float_or_none(row.get("tps_raw_median")),
        "tps_voltage_median": _float_or_none(row.get("tps_voltage_median")),
        "battery_voltage_median": _float_or_none(row.get("battery_voltage_median")),
        "battery_voltage_min": _float_or_none(row.get("battery_voltage_min")),
        "checksum_failure_ratio": _float_or_none(row.get("checksum_failure_ratio")),
        "invalid_decoded_ratio": _float_or_none(row.get("invalid_decoded_ratio")),
    }


def empty_review_annotation() -> dict[str, Any]:
    return {
        "selected": None,
        "reviewer": None,
        "reviewed_at": None,
        "notes": None,
        "fault_label_assigned": False,
    }


def numeric_summary(values: list[Any]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(clean),
        "min": round(min(clean), 6),
        "median": round(statistics.median(clean), 6),
        "max": round(max(clean), 6),
    }


def _payload_anomaly(payload: dict[str, Any] | None) -> bool:
    if not payload or payload.get("status") != "ok":
        return False
    return _bool_or_none(payload.get("is_anomaly")) is True


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


def write_markdown_report(
    path: Path,
    feasibility: dict[str, Any],
    benchmark: dict[str, Any],
    review_manifest: dict[str, Any],
    sensitivity: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    temporal = benchmark["summary"]["by_detector"].get(TEMPORAL_BATTERY_DETECTOR_ID, {})
    temporal_coverage = benchmark["summary"]["temporal_coverage"]
    overlap = benchmark["summary"]["temporal_event_overlap"]
    lines = [
        "# H4 Temporal Change Detector Report",
        "",
        "## Feasibility",
        "",
        f"- Feasible: `{feasibility['feasible']}`",
        f"- Target: {feasibility['candidate_target']}",
        f"- Median window step ms: `{feasibility['cadence']['window_start_step_ms']['median']}`",
        f"- Adequate stable context runs: `{feasibility['stable_context_runs']['adequate_for_temporal_detection']}`",
        "",
        "## Benchmark",
        "",
        f"- Temporal statuses: `{temporal.get('status_counts')}`",
        f"- Temporal coverage: `{temporal_coverage}`",
        f"- Pairwise disagreements: `{benchmark['summary']['pairwise_disagreements']}`",
        f"- Temporal events: `{temporal.get('event_count')}`",
        f"- Event overlap: `{overlap}`",
        f"- Three-way states: `{benchmark['summary']['three_way_agreement']['states']}`",
        f"- Label metrics computed: `{benchmark['label_metrics']['computed']}`",
        "",
        "## Review",
        "",
        f"- Review events: `{review_manifest['temporal_event_count']}`",
        f"- Representative categories: `{review_manifest['representative_categories']}`",
        "",
        "## Stability",
        "",
        f"- Variant event count range: {sensitivity['event_count_stability']['variant_min']}..{sensitivity['event_count_stability']['variant_max']}",
        f"- Low-persistence events: `{sensitivity['low_persistence_event_count']}`",
        f"- Parameter sensitive: `{sensitivity['sensitivity_interpretation']['sensitive_to_reasonable_parameter_changes']}`",
        "",
        "## Decision",
        "",
        f"- Recommendation: `{decision['recommendation']}`",
        f"- Detects temporal phenomenon: `{decision['answers']['detects_temporally_meaningful_phenomenon']}`",
        f"- Distinct from Core 1/Core 2: `{decision['answers']['provides_information_not_represented_by_core1_or_core2']}`",
        f"- Stable under parameter variation: `{decision['answers']['events_stable_under_parameter_variation']}`",
        f"- Artifact dominated: `{decision['answers']['results_dominated_by_start_transition_missing_data_or_artifacts']}`",
        f"- Keep in research harness: `{decision['answers']['should_remain_part_of_research_harness']}`",
        "",
        "## Outputs",
        "",
        "- `feasibility_audit.json`",
        "- `shadow_benchmark_results.json` and `shadow_window_results.jsonl`",
        "- `review_manifest.json`",
        "- `parameter_sensitivity.json`",
        "- `decision_report.json`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
