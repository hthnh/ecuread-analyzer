from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import Settings
from app.evaluation.contextual_shadow import (
    DEFAULT_MIN_CONTEXT_WINDOWS,
    DEFAULT_ROBUST_Z_THRESHOLD,
    detector_summary_by_id,
    load_reference_feature_frames,
    load_session_frames,
    reference_provenance,
    select_shadow_evaluation_sessions,
)
from app.evaluation.model_harness_evaluation import (
    EvaluationPaths,
    benchmark_summary,
    build_evaluation_manifest,
    default_paths,
    detector_benchmark_payload,
    label_metrics_policy,
    write_json,
)
from app.ml.artifacts import load_model_bundle
from app.ml.contextual_detector import ContextualBatteryVoltageDetector, build_contextual_battery_reference
from app.ml.evidence_aggregation import (
    ACTIVE_H5_DETECTOR_IDS,
    REJECTED_TEMPORAL_CANDIDATE_IDS,
    AggregationPolicy,
    aggregate_window_results,
    aggregation_contract,
    build_aggregate_events,
    representative_events,
    summarize_aggregate_windows,
)
from app.ml.harness import DetectorContext, ModelHarness
from app.ml.iforest_detector import IsolationForestDetector


def default_h5_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h5")


def run_all(paths: EvaluationPaths, *, h3_dir: Path | None = None) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    resolved_h3_dir = (h3_dir or paths.repo_root / "data" / "evaluation" / "h3").resolve()
    manifest = load_manifest(paths, resolved_h3_dir)
    reference = load_contextual_reference(paths, resolved_h3_dir, manifest)
    policy = AggregationPolicy()
    contract = aggregation_contract(policy)
    benchmark, window_rows, events = run_aggregation_benchmark(paths, manifest, reference, policy)
    review = build_event_review_manifest(manifest, events)
    decision = build_decision_report(contract, benchmark, review)

    outputs = {
        "contract": paths.output_dir / "aggregation_contract.json",
        "benchmark": paths.output_dir / "aggregate_benchmark_results.json",
        "window_results": paths.output_dir / "aggregate_window_results.jsonl",
        "event_review": paths.output_dir / "aggregate_event_review.json",
        "decision": paths.output_dir / "decision_report.json",
        "report": paths.output_dir / "report.md",
    }
    write_json(outputs["contract"], contract)
    write_json(outputs["benchmark"], benchmark)
    write_jsonl(outputs["window_results"], window_rows)
    write_json(outputs["event_review"], review)
    write_json(outputs["decision"], decision)
    write_markdown_report(outputs["report"], contract, benchmark, review, decision)
    return outputs


def load_manifest(paths: EvaluationPaths, h3_dir: Path) -> dict[str, Any]:
    manifest_path = h3_dir / "evaluation_manifest.json"
    if manifest_path.exists():
        return read_json(manifest_path)
    return build_evaluation_manifest(paths)


def load_contextual_reference(paths: EvaluationPaths, h3_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    reference_path = h3_dir / "contextual_reference.json"
    if reference_path.exists():
        return read_json(reference_path)
    reference_frames, reference_items = load_reference_feature_frames(paths, manifest)
    return build_contextual_battery_reference(
        reference_frames,
        provenance=reference_provenance(paths, manifest, reference_items),
        min_context_windows=DEFAULT_MIN_CONTEXT_WINDOWS,
        robust_z_threshold=DEFAULT_ROBUST_Z_THRESHOLD,
    )


def run_aggregation_benchmark(
    paths: EvaluationPaths,
    manifest: dict[str, Any],
    reference: dict[str, Any],
    policy: AggregationPolicy,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    settings = Settings(data_dir=paths.data_dir, model_dir=paths.model_dir)
    selected, excluded = select_shadow_evaluation_sessions(manifest)
    bundle = load_model_bundle(paths.model_dir)
    session_results: list[dict[str, Any]] = []
    aggregate_window_rows: list[dict[str, Any]] = []

    for item in selected:
        session_result, session_windows = run_aggregation_session(paths, settings, bundle, reference, item, policy)
        session_results.append(session_result)
        aggregate_window_rows.extend(session_windows)

    events = build_aggregate_events(aggregate_window_rows)
    aggregate_summary = summarize_aggregate_windows(aggregate_window_rows)
    benchmark = {
        "schema_version": "multi-detector-evidence-aggregation-benchmark-v1",
        "configuration": {
            "purpose": "offline evidence representation; not production score fusion",
            "active_detector_ids": list(ACTIVE_H5_DETECTOR_IDS),
            "excluded_detector_ids": list(REJECTED_TEMPORAL_CANDIDATE_IDS),
            "score_policy": "detector-local scores and thresholds are preserved independently; no universal anomaly score",
            "production_behavior_changed": False,
            "drive_safe_behavior_changed": False,
        },
        "selection": {
            "evaluated_session_count": len(selected),
            "excluded_session_count": len(excluded),
            "excluded_sessions": excluded,
        },
        "label_metrics": label_metrics_policy(manifest),
        "sessions": session_results,
        "summary": {
            **benchmark_summary(session_results),
            "by_detector": detector_summary_by_id(session_results),
            "aggregation": aggregate_summary,
            "aggregate_events": events["summary"],
            "representative_aggregate_events": representative_events(events),
        },
    }
    return benchmark, aggregate_window_rows, events


def run_aggregation_session(
    paths: EvaluationPaths,
    settings: Settings,
    bundle: Any,
    reference: dict[str, Any],
    item: dict[str, Any],
    policy: AggregationPolicy,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        session, frame_data, feature_frame, signal_columns = load_session_frames(paths, settings, item)
        context = DetectorContext(
            telemetry_schema_version=session.telemetry_schema_version,
            signal_columns=tuple(signal_columns),
            minimum_windows_for_status=settings.min_session_windows_for_status,
        )
        started = perf_counter()
        harness_result = ModelHarness(
            [IsolationForestDetector(bundle), ContextualBatteryVoltageDetector(reference)]
        ).run(feature_frame, context)
        harness_latency_ms = round((perf_counter() - started) * 1000.0, 6)
        detectors = [
            detector_benchmark_payload(
                result,
                feature_frame,
                frame_data,
                item.get("recording_duration_ms"),
                float(result.metadata.get("harness_latency_ms", harness_latency_ms)),
            )
            for result in harness_result.results
        ]
        aggregate_rows = aggregate_window_results(
            session_id=item["session_id"],
            feature_frame=feature_frame,
            results=harness_result.results,
            frame_data=frame_data,
            policy=policy,
        )
        return (
            {
                "session_id": item["session_id"],
                "dataset_kind": item["dataset_kind"],
                "telemetry_schema_version": session.telemetry_schema_version,
                "training_evaluation_split": item["training_evaluation_split"],
                "window_count": int(len(feature_frame)),
                "recording_duration_ms": item.get("recording_duration_ms"),
                "detectors": detectors,
                "aggregate_summary": summarize_aggregate_windows(aggregate_rows),
            },
            aggregate_rows,
        )
    except Exception as exc:  # noqa: BLE001 - evaluation records failed sessions.
        failed_rows = failed_session_rows(item, str(type(exc).__name__), str(exc), policy)
        return (
            {
                "session_id": item["session_id"],
                "dataset_kind": item.get("dataset_kind"),
                "training_evaluation_split": item.get("training_evaluation_split", {}),
                "window_count": item.get("window_count"),
                "recording_duration_ms": item.get("recording_duration_ms"),
                "detectors": [
                    {
                        "detector_id": "h5_aggregation_session",
                        "status": "failed",
                        "reason": "aggregation_session_exception",
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                ],
                "aggregate_summary": summarize_aggregate_windows(failed_rows),
            },
            failed_rows,
        )


def failed_session_rows(
    item: dict[str, Any],
    error_type: str,
    error_message: str,
    policy: AggregationPolicy,
) -> list[dict[str, Any]]:
    window_count = int(item.get("window_count") or 0)
    rows = []
    for row_number in range(window_count):
        rows.append(
            {
                "schema_version": "multi-detector-window-aggregation-v1",
                "session_id": item["session_id"],
                "window_number": row_number,
                "window_index": row_number,
                "start_frame_index": None,
                "end_frame_index": None,
                "start_time_ms": None,
                "end_time_ms": None,
                "duration_ms": None,
                "telemetry": {},
                "active_detector_ids": list(policy.detector_ids),
                "detector_findings": [
                    {
                        "detector_id": detector_id,
                        "model_version": None,
                        "execution_status": "failed",
                        "available": False,
                        "applicable": False,
                        "applicability_reason": "aggregation_session_exception",
                        "finding": "unavailable",
                        "prediction": None,
                        "is_anomaly": None,
                        "detector_local_score": None,
                        "detector_local_threshold": None,
                        "evidence": {},
                        "missing_features": [],
                        "error": {"type": error_type, "message": error_message},
                    }
                    for detector_id in policy.detector_ids
                ],
                "counts": {
                    "available_detector_count": 0,
                    "applicable_detector_count": 0,
                    "positive_finding_count": 0,
                    "negative_finding_count": 0,
                    "not_applicable_detector_count": 0,
                    "unavailable_detector_count": len(policy.detector_ids),
                },
                "detector_ids": {
                    "positive": [],
                    "negative": [],
                    "not_applicable": [],
                    "unavailable": list(policy.detector_ids),
                },
                "coverage": {
                    "status": "none",
                    "full_detector_count": len(policy.detector_ids),
                    "applicable_ratio": 0.0,
                },
                "aggregate_evidence_status": {
                    "state": "insufficient_coverage",
                    "description": "No active detector was applicable for this window.",
                    "no_evidence_is_verified_healthy": False,
                    "score_fusion_used": False,
                },
                "detector_disagreements": [],
            }
        )
    return rows


def build_event_review_manifest(manifest: dict[str, Any], events: dict[str, Any]) -> dict[str, Any]:
    by_id = {item["session_id"]: item for item in manifest["sessions"]}
    all_events = [*events.get("finding_events", []), *events.get("coverage_gap_regions", [])]
    return {
        "schema_version": "multi-detector-aggregate-event-review-v1",
        "annotation_policy": {
            "mechanical_root_causes_assigned": False,
            "allowed_review_annotations": [
                "interpretable detector agreement",
                "interpretable detector disagreement",
                "detector-specific evidence",
                "insufficient coverage",
                "likely acquisition/data artifact",
                "requires physical verification",
            ],
        },
        "event_count": len(all_events),
        "event_type_counts": dict(sorted(Counter(event["event_type"] for event in all_events).items())),
        "items": [
            {
                "review_item_id": f"h5-aggregate-event-{index:04d}",
                "provenance": event_provenance(by_id.get(event["session_id"], {})),
                "event": event,
                "review_annotation": {
                    "selected": None,
                    "reviewer": None,
                    "reviewed_at": None,
                    "notes": None,
                    "fault_label_assigned": False,
                },
            }
            for index, event in enumerate(all_events, start=1)
        ],
    }


def event_provenance(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": item.get("session_id"),
        "path": item.get("path"),
        "capture_provenance": item.get("capture_provenance"),
        "decoder": item.get("decoder"),
        "telemetry_schema_version": item.get("telemetry_schema_version"),
        "training_evaluation_split": item.get("training_evaluation_split"),
        "artifacts": item.get("artifacts"),
    }


def build_decision_report(
    contract: dict[str, Any],
    benchmark: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    aggregation = benchmark["summary"]["aggregation"]
    event_types = review.get("event_type_counts", {})
    failures = benchmark["summary"].get("detector_status_counts", {}).get("failed", 0)
    active_set_ok = tuple(contract["active_detector_ids"]) == ACTIVE_H5_DETECTOR_IDS
    clean_semantics = (
        contract["policy"]["score_fusion_used"] is False
        and contract["policy"]["no_evidence_is_verified_healthy"] is False
    )
    traceable = review.get("event_count", 0) == 0 or all(
        item.get("provenance", {}).get("session_id")
        and item.get("event", {}).get("source_window_range")
        for item in review.get("items", [])
    )
    recommendation = "advance" if active_set_ok and clean_semantics and traceable and failures == 0 else "revise"
    return {
        "schema_version": "multi-detector-evidence-aggregation-decision-v1",
        "recommendation": recommendation,
        "answers": {
            "heterogeneous_outputs_represented_without_score_fusion": clean_semantics,
            "only_one_detector_applicable_windows": aggregation["only_one_detector_applicable_windows"],
            "both_applicable_agreement_windows": aggregation["both_applicable_agreement_windows"],
            "detector_disagreement_windows": aggregation["disagreement_windows"],
            "disagreement_states_interpretable": event_types.get("disagreement_region", 0) >= 0,
            "provenance_sufficient_for_later_rca": traceable,
            "suitable_for_future_drivesafe_shadow_metadata": recommendation == "advance",
        },
        "basis": {
            "active_detector_ids": contract["active_detector_ids"],
            "excluded_detector_ids": contract["excluded_detector_ids"],
            "evidence_state_counts": aggregation["evidence_state_counts"],
            "coverage_status_counts": aggregation["coverage_status_counts"],
            "detector_finding_counts": aggregation["detector_finding_counts"],
            "event_type_counts": event_types,
            "failed_detector_result_count": failures,
            "production_behavior_changed": benchmark["configuration"]["production_behavior_changed"],
            "drive_safe_behavior_changed": benchmark["configuration"]["drive_safe_behavior_changed"],
            "supervised_accuracy_claims_made": False,
        },
        "limitations": [
            "`no_evidence` is an evidence state, not proof the vehicle is healthy.",
            "Partial coverage means unavailable or inapplicable detectors are not counted as negative votes.",
            "The representation is ready for a future additive DriveSafe contract, but this task intentionally does not change DriveSafe behavior.",
            "No precision, recall, F1, confidence, RCA or score fusion is computed.",
        ],
    }


def write_markdown_report(
    path: Path,
    contract: dict[str, Any],
    benchmark: dict[str, Any],
    review: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    aggregation = benchmark["summary"]["aggregation"]
    lines = [
        "# H5 Multi-Detector Evidence Aggregation Report",
        "",
        "## Contract",
        "",
        f"- Active detectors: `{contract['active_detector_ids']}`",
        f"- Excluded temporal candidates: `{contract['excluded_detector_ids']}`",
        "- Detector-local scores and thresholds are preserved independently; no universal anomaly score is produced.",
        "- `no_evidence` does not mean verified healthy.",
        "",
        "## Coverage",
        "",
        f"- Evidence states: `{aggregation['evidence_state_counts']}`",
        f"- Coverage states: `{aggregation['coverage_status_counts']}`",
        f"- Applicable detector count distribution: `{aggregation['applicable_detector_count_distribution']}`",
        f"- Detector finding counts: `{aggregation['detector_finding_counts']}`",
        "",
        "## Findings",
        "",
        f"- IF-only finding windows: `{aggregation['if_only_finding_windows']}`",
        f"- Contextual-only finding windows: `{aggregation['contextual_only_finding_windows']}`",
        f"- Overlapping finding windows: `{aggregation['overlapping_finding_windows']}`",
        f"- Disagreement windows: `{aggregation['disagreement_windows']}`",
        f"- Insufficient-coverage windows: `{aggregation['insufficient_coverage_windows']}`",
        f"- Aggregate event types: `{review['event_type_counts']}`",
        "",
        "## Decision",
        "",
        f"- Recommendation: `{decision['recommendation']}`",
        f"- Future DriveSafe suitability: `{decision['answers']['suitable_for_future_drivesafe_shadow_metadata']}`",
        "- Production inference remains Isolation Forest only.",
        "- DriveSafe behavior was not modified.",
        "",
        "## Outputs",
        "",
        "- `aggregation_contract.json`",
        "- `aggregate_benchmark_results.json`",
        "- `aggregate_window_results.jsonl`",
        "- `aggregate_event_review.json`",
        "- `decision_report.json`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
