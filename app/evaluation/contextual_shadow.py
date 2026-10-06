from __future__ import annotations

import csv
import json
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from app.config import Settings
from app.domain.telemetry import get_required_signal_columns
from app.evaluation.model_harness_evaluation import (
    EvaluationPaths,
    benchmark_summary,
    build_evaluation_manifest,
    default_paths,
    detector_benchmark_payload,
    detector_disagreements,
    file_sha256,
    label_metrics_policy,
    load_canonical_session_from_telemetry,
    write_json,
)
from app.ml.artifacts import load_model_bundle
from app.ml.contextual_detector import (
    CONTEXTUAL_BATTERY_DETECTOR_ID,
    CONTEXTUAL_REQUIRED_FEATURES,
    DEFAULT_MIN_CONTEXT_WINDOWS,
    DEFAULT_ROBUST_Z_THRESHOLD,
    ContextualBatteryVoltageDetector,
    build_contextual_battery_reference,
)
from app.ml.harness import DetectorContext, ModelHarness
from app.ml.iforest_detector import IsolationForestDetector
from app.services.analysis_service import build_feature_frame, canonical_session_to_frame_data


STRICT_VERIFIED_SIGNALS = ("rpm", "tps_voltage", "tps_raw", "battery_voltage")


def default_h3_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return default_paths(repo_root=root, output_dir=output_dir or root / "data" / "evaluation" / "h3")


def run_all(paths: EvaluationPaths) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_evaluation_manifest(paths)
    reference_frames, reference_items = load_reference_feature_frames(paths, manifest)
    reference = build_contextual_battery_reference(
        reference_frames,
        provenance=reference_provenance(paths, manifest, reference_items),
        min_context_windows=DEFAULT_MIN_CONTEXT_WINDOWS,
        robust_z_threshold=DEFAULT_ROBUST_Z_THRESHOLD,
    )
    audit = build_feasibility_audit(paths, manifest, reference, reference_items)
    benchmark, window_rows = run_shadow_benchmark(paths, manifest, reference)
    decision = build_decision_report(audit, reference, benchmark)

    manifest_path = paths.output_dir / "evaluation_manifest.json"
    audit_path = paths.output_dir / "feasibility_audit.json"
    reference_path = paths.output_dir / "contextual_reference.json"
    benchmark_path = paths.output_dir / "shadow_benchmark_results.json"
    windows_path = paths.output_dir / "shadow_window_results.jsonl"
    decision_path = paths.output_dir / "decision_report.json"
    report_path = paths.output_dir / "report.md"

    write_json(manifest_path, manifest)
    write_json(audit_path, audit)
    write_json(reference_path, reference)
    write_json(benchmark_path, benchmark)
    write_jsonl(windows_path, window_rows)
    write_json(decision_path, decision)
    write_markdown_report(report_path, audit, reference, benchmark, decision)
    return {
        "manifest": manifest_path,
        "feasibility_audit": audit_path,
        "reference": reference_path,
        "benchmark": benchmark_path,
        "window_results": windows_path,
        "decision": decision_path,
        "report": report_path,
    }


def load_reference_feature_frames(
    paths: EvaluationPaths,
    manifest: dict[str, Any],
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    settings = Settings(data_dir=paths.data_dir, model_dir=paths.model_dir)
    frames: dict[str, pd.DataFrame] = {}
    items: list[dict[str, Any]] = []
    for item in manifest["sessions"]:
        split = item.get("training_evaluation_split", {}).get("split")
        if split != "normal_train" or item.get("telemetry_schema_version") != "canonical-telemetry-v2":
            continue
        if not set(STRICT_VERIFIED_SIGNALS).issubset(set(item.get("verified_signals", []))):
            continue
        session, frame_data, feature_frame, signal_columns = load_session_frames(paths, settings, item)
        missing_features = [feature for feature in CONTEXTUAL_REQUIRED_FEATURES if feature not in feature_frame]
        if missing_features:
            continue
        frames[session.session_id or item["session_id"]] = feature_frame
        resolved = dict(item)
        resolved["signal_columns"] = signal_columns
        resolved["frame_count"] = int(len(frame_data))
        items.append(resolved)
    return frames, items


def reference_provenance(
    paths: EvaluationPaths,
    manifest: dict[str, Any],
    reference_items: list[dict[str, Any]],
) -> dict[str, Any]:
    calibration_decisions_path = paths.data_dir / "decoder-audit" / "reports" / "calibration_signal_decisions.csv"
    return {
        "feature_schema_version": manifest["provenance"].get("feature_schema_version"),
        "telemetry_schema_version": "canonical-telemetry-v2",
        "decoder_versions": sorted(
            {
                item.get("decoder", {}).get("decoder_version_key")
                for item in reference_items
                if item.get("decoder", {}).get("decoder_version_key")
            }
        ),
        "reference_split": "normal_train",
        "reference_session_count": len(reference_items),
        "reference_session_ids": sorted(item["session_id"] for item in reference_items),
        "calibration_signal_decisions": str(calibration_decisions_path.relative_to(paths.repo_root)),
        "calibration_signal_decisions_sha256": file_sha256(calibration_decisions_path),
        "verified_signal_policy": "strictly verified or verified_as_raw calibration decisions only",
        "training_outside_harness": True,
    }


def build_feasibility_audit(
    paths: EvaluationPaths,
    manifest: dict[str, Any],
    reference: dict[str, Any],
    reference_items: list[dict[str, Any]],
) -> dict[str, Any]:
    signal_decisions = load_signal_decisions(paths.data_dir / "decoder-audit" / "reports" / "calibration_signal_decisions.csv")
    v2_sessions = [
        item
        for item in manifest["sessions"]
        if item.get("dataset_kind") == "canonical_telemetry"
        and item.get("telemetry_schema_version") == "canonical-telemetry-v2"
    ]
    v2_with_required = [
        item
        for item in v2_sessions
        if set(STRICT_VERIFIED_SIGNALS).issubset(set(item.get("verified_signals", [])))
    ]
    duplicate_groups = duplicate_sample_groups(manifest)
    contexts = reference.get("contexts", {})
    enough_reference = bool(contexts)
    return {
        "schema_version": "contextual-shadow-feasibility-audit-v1",
        "supported_detector_scope": (
            "narrow contextual statistical detection for verified battery-voltage deviations within "
            "RPM/TPS-defined operating contexts"
        ),
        "unsupported_detector_scope": [
            "general mechanical fault diagnosis",
            "coolant or intake temperature contextual detection using only verified signals",
            "RPM-from-TPS behavioral modeling",
        ],
        "verified_signal_decisions": {
            "strictly_verified_signals": list(STRICT_VERIFIED_SIGNALS),
            "source": "data/decoder-audit/reports/calibration_signal_decisions.csv",
            "rows": signal_decisions,
            "excluded_high_confidence_not_verified": ["iat_c", "ect_c"],
        },
        "canonical_availability": {
            "v2_session_count": len(v2_sessions),
            "v2_sessions_with_required_verified_signals": len(v2_with_required),
            "reference_session_count": len(reference_items),
            "reference_session_ids": sorted(item["session_id"] for item in reference_items),
        },
        "rpm_tps_time_alignment": {
            "supported": True,
            "basis": (
                "Canonical samples store RPM, TPS voltage and TPS raw in the same decoded sample row; "
                "window features are computed over identical row boundaries."
            ),
        },
        "window_representation": {
            "sufficient_for": "coarse context bins from per-window RPM/TPS medians and battery-voltage robust scoring",
            "insufficient_for": "within-window transient waveform diagnosis or closed-loop causal modeling",
            "required_features": list(CONTEXTUAL_REQUIRED_FEATURES),
        },
        "reference_context_counts": reference.get("observed_context_counts", {}),
        "modeled_contexts": sorted(contexts),
        "unmodeled_context_policy": "skipped, not converted into normal predictions",
        "capture_overlap": {
            "duplicate_sample_groups": duplicate_groups,
            "evaluation_overlap_policy": "exclude reference session IDs and duplicate sample hashes from shadow evaluation",
        },
        "feasible": enough_reference and len(reference_items) > 0,
        "feasibility_reason": (
            "Reference split has enough windows for idle, low-load and mid-load running contexts."
            if enough_reference
            else "Reference split lacks any context with enough verified windows for robust statistics."
        ),
    }


def run_shadow_benchmark(
    paths: EvaluationPaths,
    manifest: dict[str, Any],
    reference: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    settings = Settings(data_dir=paths.data_dir, model_dir=paths.model_dir)
    bundle = load_model_bundle(paths.model_dir)
    selected, excluded = select_shadow_evaluation_sessions(manifest)
    session_results: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []

    for item in selected:
        session_results.append(
            run_shadow_session(paths, settings, bundle, reference, item, window_rows)
        )

    benchmark = {
        "schema_version": "contextual-shadow-benchmark-results-v1",
        "configuration": {
            "model_dir": str(paths.model_dir.relative_to(paths.repo_root)),
            "production_detector": "isolation_forest",
            "shadow_detector": CONTEXTUAL_BATTERY_DETECTOR_ID,
            "detectors": ["isolation_forest", CONTEXTUAL_BATTERY_DETECTOR_ID],
            "score_policy": "raw detector scores preserved; no cross-detector score normalization",
            "threshold_policy": "production inference thresholds unchanged; contextual threshold is shadow-only",
            "reference_model_version": reference.get("model_version"),
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
            "disagreements": disagreement_summary(session_results),
        },
    }
    return benchmark, window_rows


def run_shadow_session(
    paths: EvaluationPaths,
    settings: Settings,
    bundle: Any,
    reference: dict[str, Any],
    item: dict[str, Any],
    window_rows: list[dict[str, Any]],
) -> dict[str, Any]:
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
        detector_results = []
        raw_results = harness_result.results
        for result in raw_results:
            latency_ms = float(result.metadata.get("harness_latency_ms", harness_latency_ms))
            detector_results.append(
                detector_benchmark_payload(
                    result,
                    feature_frame,
                    frame_data,
                    item.get("recording_duration_ms"),
                    latency_ms,
                )
            )

        append_window_rows(window_rows, item["session_id"], raw_results, len(feature_frame))
        return {
            "session_id": item["session_id"],
            "dataset_kind": item["dataset_kind"],
            "telemetry_schema_version": session.telemetry_schema_version,
            "training_evaluation_split": item["training_evaluation_split"],
            "window_count": int(len(feature_frame)),
            "recording_duration_ms": item.get("recording_duration_ms"),
            "detectors": detector_results,
            "detector_disagreements": detector_disagreements(raw_results),
        }
    except Exception as exc:  # noqa: BLE001 - benchmark records failed sessions.
        return {
            "session_id": item["session_id"],
            "dataset_kind": item["dataset_kind"],
            "training_evaluation_split": item.get("training_evaluation_split", {}),
            "window_count": item.get("window_count"),
            "recording_duration_ms": item.get("recording_duration_ms"),
            "detectors": [
                {
                    "detector_id": "shadow_benchmark",
                    "status": "failed",
                    "reason": "benchmark_exception",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            ],
            "detector_disagreements": {"available": False, "reason": "session failed"},
        }


def select_shadow_evaluation_sessions(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reference_ids = {
        item["session_id"]
        for item in manifest["sessions"]
        if item.get("training_evaluation_split", {}).get("split") == "normal_train"
    }
    reference_hashes = {
        item.get("artifacts", {}).get("samples_sha256")
        for item in manifest["sessions"]
        if item["session_id"] in reference_ids
    }
    excluded: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in manifest["sessions"]:
        if item.get("dataset_kind") != "canonical_telemetry" or not item.get("evaluation_ready"):
            continue
        sample_hash = item.get("artifacts", {}).get("samples_sha256")
        if item["session_id"] in reference_ids:
            excluded.append({"session_id": item["session_id"], "reason": "reference_session"})
            continue
        if sample_hash in reference_hashes:
            excluded.append({"session_id": item["session_id"], "reason": "duplicate_of_reference_capture"})
            continue
        grouped.setdefault(sample_hash or item["session_id"], []).append(item)

    selected: list[dict[str, Any]] = []
    for _, group in sorted(grouped.items()):
        preferred = sorted(group, key=evaluation_representation_sort_key)[0]
        selected.append(preferred)
        for item in group:
            if item is not preferred:
                excluded.append({"session_id": item["session_id"], "reason": "duplicate_evaluation_capture"})
    selected.sort(key=lambda item: item["session_id"])
    excluded.sort(key=lambda item: item["session_id"])
    return selected, excluded


def evaluation_representation_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    label = item.get("training_evaluation_split", {}).get("label")
    split = item.get("training_evaluation_split", {}).get("split")
    has_review_context = bool(label) and split != "unassigned"
    is_ride_session = str(item.get("session_id", "")).startswith("ride-")
    return (
        0 if has_review_context else 1,
        0 if is_ride_session else 1,
        str(item.get("session_id", "")),
    )


def load_session_frames(
    paths: EvaluationPaths,
    settings: Settings,
    item: dict[str, Any],
) -> tuple[Any, pd.DataFrame, pd.DataFrame, list[str]]:
    session = load_canonical_session_from_telemetry(paths.repo_root / item["path"])
    signal_columns = get_required_signal_columns(session.telemetry_schema_version)
    frame_data = canonical_session_to_frame_data(session, signal_columns)
    feature_frame = build_feature_frame(frame_data, settings, signal_columns)
    return session, frame_data, feature_frame, signal_columns


def append_window_rows(
    window_rows: list[dict[str, Any]],
    session_id: str,
    results: list[Any],
    window_count: int,
) -> None:
    for row_number in range(window_count):
        detectors = {
            result.identity.detector_id: result.window_payload(row_number)
            for result in results
        }
        base: dict[str, Any] = {"session_id": session_id, "window_number": row_number, "detectors": detectors}
        if results:
            first = results[0].windows.iloc[row_number]
            for column in ("window_index", "start_frame_index", "end_frame_index", "duration_ms"):
                if column in first:
                    base[column] = _json_safe(first[column])
        window_rows.append(base)


def detector_summary_by_id(session_results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for session in session_results:
        for detector in session.get("detectors", []):
            detector_id = detector.get("detector_id", "unknown")
            row = summary.setdefault(
                detector_id,
                {
                    "status_counts": {},
                    "session_count": 0,
                    "scored_window_count": 0,
                    "anomaly_window_count": 0,
                    "skipped_window_count": 0,
                    "event_count": 0,
                },
            )
            row["session_count"] += 1
            status = str(detector.get("status"))
            row["status_counts"][status] = row["status_counts"].get(status, 0) + 1
            row["scored_window_count"] += int(detector.get("scored_window_count") or 0)
            row["anomaly_window_count"] += int(detector.get("anomaly_window_count") or 0)
            metadata = detector.get("metadata") or {}
            row["skipped_window_count"] += int(metadata.get("skipped_window_count") or 0)
            row["event_count"] += int((detector.get("events") or {}).get("count") or 0)
    for row in summary.values():
        row["status_counts"] = dict(sorted(row["status_counts"].items()))
    return dict(sorted(summary.items()))


def disagreement_summary(session_results: list[dict[str, Any]]) -> dict[str, Any]:
    compared = 0
    unavailable = 0
    disagreements = 0
    sessions_with_disagreement = 0
    for session in session_results:
        pairs = session.get("detector_disagreements", {}).get("pairs", [])
        session_disagreed = False
        for pair in pairs:
            compared += int(pair.get("compared_windows") or 0)
            unavailable += int(pair.get("unavailable_windows") or 0)
            count = int(pair.get("window_disagreement_count") or 0)
            disagreements += count
            session_disagreed = session_disagreed or count > 0
        if session_disagreed:
            sessions_with_disagreement += 1
    return {
        "compared_windows": compared,
        "unavailable_windows": unavailable,
        "window_disagreement_count": disagreements,
        "window_disagreement_ratio": None if compared == 0 else round(disagreements / compared, 6),
        "sessions_with_disagreement": sessions_with_disagreement,
    }


def build_decision_report(
    audit: dict[str, Any],
    reference: dict[str, Any],
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    contextual = benchmark["summary"]["by_detector"].get(CONTEXTUAL_BATTERY_DETECTOR_ID, {})
    disagreements = benchmark["summary"].get("disagreements", {})
    feasible = bool(audit.get("feasible"))
    contextual_ok = contextual.get("status_counts", {}).get("ok", 0) > 0
    distinct = int(disagreements.get("window_disagreement_count") or 0) > 0
    return {
        "telemetry_supports_contextual_detection": feasible,
        "distinct_information_observed": distinct,
        "manual_review_candidates": representative_events(benchmark),
        "remaining_limitations": [
            "Only verified RPM, TPS raw, TPS voltage and battery voltage are used.",
            "IAT/ECT contextual thermal behavior remains excluded until those signals are verified.",
            "No supervised accuracy metrics are computed because verified binary normal/fault labels are unavailable.",
            "Contextual anomalies indicate statistical deviation only.",
        ],
        "recommendation": (
            "advance_for_offline_shadow_revision_not_production"
            if feasible and contextual_ok
            else "revise_or_reject_before_shadow_use"
        ),
        "recommendation_reason": (
            "Core 2 is feasible as a narrow battery-voltage contextual shadow detector; keep it offline until manual review validates events."
            if feasible and contextual_ok
            else "Available verified telemetry/reference coverage is insufficient for a useful contextual detector."
        ),
        "reference_model_version": reference.get("model_version"),
    }


def representative_events(benchmark: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for session in benchmark.get("sessions", []):
        split = session.get("training_evaluation_split", {})
        for detector in session.get("detectors", []):
            if detector.get("detector_id") != CONTEXTUAL_BATTERY_DETECTOR_ID:
                continue
            for event in (detector.get("events") or {}).get("items", []):
                candidates.append(
                    {
                        "session_id": session.get("session_id"),
                        "label": split.get("label"),
                        "label_is_verified_truth": bool(split.get("suitable_for_precision_recall_f1")),
                        "detector_id": CONTEXTUAL_BATTERY_DETECTOR_ID,
                        "event": event,
                        "review_reason": "contextual battery-voltage statistical deviation",
                    }
                )
    return sorted(candidates, key=manual_review_sort_key)[:limit]


def manual_review_sort_key(candidate: dict[str, Any]) -> tuple[int, int, str]:
    label = candidate.get("label")
    event = candidate.get("event", {})
    if label in {"battery_low_candidate", "known_abnormal"}:
        label_priority = 0
    elif label:
        label_priority = 1
    else:
        label_priority = 2
    return (label_priority, -int(event.get("window_count") or 0), str(candidate.get("session_id", "")))


def load_signal_decisions(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            signal = row.get("signal", "")
            if signal in {"RPM", "TPS voltage", "TPS raw", "Battery", "IAT", "ECT"}:
                rows.append(row)
    return rows


def duplicate_sample_groups(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for item in manifest["sessions"]:
        sample_hash = item.get("artifacts", {}).get("samples_sha256")
        if not sample_hash:
            continue
        grouped.setdefault(sample_hash, []).append(item["session_id"])
    return [
        {"samples_sha256": sample_hash, "session_ids": sorted(session_ids)}
        for sample_hash, session_ids in sorted(grouped.items())
        if len(session_ids) > 1
    ]


def write_markdown_report(
    path: Path,
    audit: dict[str, Any],
    reference: dict[str, Any],
    benchmark: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    contextual = benchmark["summary"]["by_detector"].get(CONTEXTUAL_BATTERY_DETECTOR_ID, {})
    iforest = benchmark["summary"]["by_detector"].get("isolation_forest", {})
    lines = [
        "# H3 Contextual Shadow Detector Report",
        "",
        "## Feasibility",
        "",
        f"- Feasible detector scope: `{audit['feasible']}`",
        f"- Scope: {audit['supported_detector_scope']}",
        "- Verified signals used: RPM, TPS voltage, TPS raw, battery voltage.",
        "- IAT and ECT were excluded because they are high-confidence/provisional, not strictly verified.",
        f"- Modeled contexts: {', '.join(reference.get('contexts', {}).keys()) or 'none'}",
        f"- Reference sessions: {len(reference.get('reference_session_ids', []))}",
        "",
        "## Shadow Benchmark",
        "",
        f"- Sessions evaluated: {benchmark['selection']['evaluated_session_count']}",
        f"- Isolation Forest statuses: `{iforest.get('status_counts')}`",
        f"- Core 2 statuses: `{contextual.get('status_counts')}`",
        f"- Core 2 scored windows: `{contextual.get('scored_window_count')}`",
        f"- Core 2 skipped windows: `{contextual.get('skipped_window_count')}`",
        f"- Core 2 events: `{contextual.get('event_count')}`",
        f"- Window disagreements: `{benchmark['summary']['disagreements'].get('window_disagreement_count')}`",
        f"- Label metrics computed: `{benchmark['label_metrics']['computed']}`",
        f"- Label metrics reason: {benchmark['label_metrics']['reason']}",
        "",
        "## Decision",
        "",
        f"- Does telemetry support meaningful contextual detection? `{decision['telemetry_supports_contextual_detection']}` for the narrow battery-voltage scope only.",
        f"- Does Core 2 provide distinct information beyond Isolation Forest? `{decision['distinct_information_observed']}`.",
        f"- Remaining limitations: {' '.join(decision['remaining_limitations'])}",
        f"- Recommendation: `{decision['recommendation']}`",
        f"- Rationale: {decision['recommendation_reason']}",
        "- Core 2 remains offline/shadow only and does not alter production anomaly decisions.",
        "",
        "## Manual Review",
        "",
        *manual_review_lines(decision.get("manual_review_candidates", [])),
        "",
        "## Outputs",
        "",
        "- `feasibility_audit.json`",
        "- `contextual_reference.json`",
        "- `shadow_benchmark_results.json`",
        "- `shadow_window_results.jsonl`",
        "- `decision_report.json`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def manual_review_lines(candidates: list[dict[str, Any]]) -> list[str]:
    if not candidates:
        return ["- No contextual events were selected for manual review."]
    lines: list[str] = []
    for candidate in candidates[:8]:
        event = candidate.get("event", {})
        lines.append(
            "- "
            f"{candidate.get('session_id')} "
            f"label={candidate.get('label')} "
            f"windows={event.get('start_window_index')}..{event.get('end_window_index')} "
            f"reason={candidate.get('review_reason')}"
        )
    return lines


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=_json_safe) + "\n")


def _json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")
