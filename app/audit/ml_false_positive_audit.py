from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings
from app.domain.telemetry import (
    CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
    CanonicalTelemetrySession,
    SamplingMetadata,
    TelemetrySample,
    V2_CORE_SIGNAL_COLUMNS,
    V2_SIGNAL_DEFINITIONS,
)
from app.ml.features import extract_window_features
from app.ml.inference import load_model_bundle
from app.ml.profile_v02_training import V02_ML_SIGNAL_COLUMNS, load_profile_v02_jsonl_file
from app.ml.windowing import create_windows
from app.services.analysis_service import AnalysisService


CURRENT_V2_MODEL_DIR = Path("data/models/honda_keihin_71_17_v2")
CURRENT_V2_MODEL_VERSION = "iforest-baseline-20260920T091709Z"
V2_DECODER_KEY = "honda_keihin_71_17:1.0.0"
MIN_CONFIRMED_NORMAL_SESSIONS_FOR_CANDIDATE = 4

KNOWN_ABNORMAL_V2_SESSIONS: dict[str, str] = {
    "ride-20260904-1810": "synthetically injected high ECT session from demo seed generation",
    "ride-20260909-0725": "synthetically injected low battery session from demo seed generation",
    "ride-20260914-1935": "synthetically injected high RPM/load session from demo seed generation",
    "ride-20260918-0815": "synthetically injected high ECT session from demo seed generation",
}


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    min_window_count: int = 10
    min_duration_ms: float = 30_000.0
    min_eligible_samples: int = 140


@dataclass(slots=True)
class SessionEvaluation:
    session: CanonicalTelemetrySession
    frame_data: pd.DataFrame
    feature_frame: pd.DataFrame
    windows: pd.DataFrame
    row: dict[str, Any]


def status_from_scores(anomaly_ratio: float, health_score: float | None) -> str:
    if health_score is None:
        return "no_windows"
    if anomaly_ratio >= 0.15 or health_score < 50:
        return "attention"
    if anomaly_ratio >= 0.02 or health_score < 80:
        return "monitor"
    return "ok"


def evidence_policy_status(
    *,
    overall_status: str,
    window_count: int,
    duration_ms: float | None,
    eligible_samples: int,
    policy: EvidencePolicy = EvidencePolicy(),
) -> tuple[str, str]:
    if overall_status in {"no_windows", "model_unavailable", "not_scored"}:
        return overall_status, overall_status

    reasons: list[str] = []
    if window_count < policy.min_window_count:
        reasons.append(f"window_count<{policy.min_window_count}")
    if duration_ms is None or duration_ms < policy.min_duration_ms:
        reasons.append(f"duration_ms<{int(policy.min_duration_ms)}")
    if eligible_samples < policy.min_eligible_samples:
        reasons.append(f"eligible_samples<{policy.min_eligible_samples}")

    if reasons and overall_status in {"attention", "monitor"}:
        return "limited_data", ";".join(reasons)
    return overall_status, "sufficient_evidence"


def can_train_candidate(label_rows: list[dict[str, str]]) -> tuple[bool, str]:
    normal_sessions = [row["session_id"] for row in label_rows if row.get("label") == "normal_ride"]
    if len(normal_sessions) < MIN_CONFIRMED_NORMAL_SESSIONS_FOR_CANDIDATE:
        return (
            False,
            "insufficient confirmed normal_ride sessions for session-level train/validation split",
        )
    return True, "confirmed normal_ride sessions are available"


def load_telemetry_session(session_dir: Path) -> CanonicalTelemetrySession:
    metadata_path = session_dir / "metadata.json"
    samples_path = session_dir / "samples.jsonl"
    metadata = _read_json(metadata_path)
    samples = [TelemetrySample(**row) for row in _read_jsonl(samples_path)]
    sampling = SamplingMetadata(**metadata.get("sampling", {}))
    return CanonicalTelemetrySession(
        session_id=metadata.get("session_id") or session_dir.name,
        vehicle_id=metadata.get("vehicle_id"),
        device_id=metadata.get("device_id"),
        ecu_profile_id=metadata.get("ecu_profile_id"),
        decoder_id=metadata["decoder_id"],
        decoder_version=metadata["decoder_version"],
        telemetry_schema_version=metadata.get("telemetry_schema_version", CANONICAL_TELEMETRY_SCHEMA_VERSION_V2),
        sampling=sampling,
        samples=samples,
        signal_definitions=metadata.get("signal_definitions") or V2_SIGNAL_DEFINITIONS.copy(),
        source_type=metadata.get("source_type", "canonical"),
    )


def discover_v2_session_dirs(data_dir: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    telemetry_root = data_dir / "telemetry"
    selected: list[Path] = []
    excluded: list[dict[str, Any]] = []
    for metadata_path in sorted(telemetry_root.glob("*/metadata.json")):
        metadata = _read_json(metadata_path)
        schema = metadata.get("telemetry_schema_version")
        decoder_key = f"{metadata.get('decoder_id')}:{metadata.get('decoder_version')}"
        if schema == CANONICAL_TELEMETRY_SCHEMA_VERSION_V2 and decoder_key == V2_DECODER_KEY:
            selected.append(metadata_path.parent)
        else:
            excluded.append(
                {
                    "session_id": metadata.get("session_id") or metadata_path.parent.name,
                    "telemetry_schema_version": schema,
                    "decoder_key": decoder_key,
                    "reason": "not current V2 SH Mode model input",
                }
            )
    return selected, excluded


def evaluate_session(
    session: CanonicalTelemetrySession,
    settings: Settings,
    model_dir: Path,
    policy: EvidencePolicy = EvidencePolicy(),
) -> SessionEvaluation:
    service = AnalysisService(settings, model_dir=model_dir)
    output = service.analyze(session, persist=False, allow_empty_analysis=True)
    frame_data = output.frame_data
    windows = output.windows
    duration_ms = _session_duration_ms(frame_data)
    eligible_samples = int(frame_data["ml_eligible"].fillna(False).astype(bool).sum()) if not frame_data.empty else 0
    decision_stats = _decision_score_stats(windows)
    signal_stats = _signal_stats(frame_data)
    policy_status, policy_reason = evidence_policy_status(
        overall_status=str(output.summary["overall_status"]),
        window_count=int(output.summary["window_count"]),
        duration_ms=duration_ms,
        eligible_samples=eligible_samples,
        policy=policy,
    )
    row = {
        "session_id": output.summary["session_id"],
        "sample_count": len(session.samples),
        "eligible_sample_count": eligible_samples,
        "duration_ms": duration_ms,
        "window_count": int(output.summary["window_count"]),
        "window_bucket": window_bucket(int(output.summary["window_count"])),
        "anomaly_window_count": int(output.summary["anomaly_window_count"]),
        "anomaly_ratio": float(output.summary["anomaly_ratio"]),
        "health_score": output.summary["health_score"],
        "overall_status": output.summary["overall_status"],
        "evidence_policy_status": policy_status,
        "evidence_policy_reason": policy_reason,
        "model_version": output.summary["model_version"],
        "most_unusual_features": ";".join(output.summary.get("most_unusual_features") or []),
        **decision_stats,
        **signal_stats,
    }
    return SessionEvaluation(
        session=session,
        frame_data=frame_data,
        feature_frame=output.feature_frame,
        windows=windows,
        row=row,
    )


def window_bucket(window_count: int) -> str:
    if window_count <= 0:
        return "0"
    if window_count <= 4:
        return "1-4"
    if window_count <= 9:
        return "5-9"
    if window_count <= 19:
        return "10-19"
    if window_count <= 49:
        return "20-49"
    return "50+"


def build_session_labels(report_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    labels: list[dict[str, str]] = []
    for row in sorted(report_rows, key=lambda item: str(item["session_id"])):
        session_id = str(row["session_id"])
        if session_id in KNOWN_ABNORMAL_V2_SESSIONS:
            labels.append(
                {
                    "session_id": session_id,
                    "label": "known_abnormal",
                    "label_source": "explicit_demo_seed_generation",
                    "notes": KNOWN_ABNORMAL_V2_SESSIONS[session_id],
                }
            )
        elif int(row.get("window_count") or 0) < EvidencePolicy().min_window_count:
            labels.append(
                {
                    "session_id": session_id,
                    "label": "too_short",
                    "label_source": "derived_window_count_lt_10",
                    "notes": "Too few windows for stable session-level status; not a mechanical truth label.",
                }
            )
        else:
            labels.append(
                {
                    "session_id": session_id,
                    "label": "unknown",
                    "label_source": "no_explicit_operational_label",
                    "notes": "Needs human review before being used as normal or abnormal truth.",
                }
            )
    return labels


def build_label_review_rows(
    report_rows: list[dict[str, Any]],
    labels: list[dict[str, str]],
) -> list[dict[str, Any]]:
    labels_by_session = {row["session_id"]: row["label"] for row in labels}
    rows: list[dict[str, Any]] = []
    for row in report_rows:
        session_id = str(row["session_id"])
        if labels_by_session.get(session_id) != "unknown":
            continue
        rows.append(
            {
                "session_id": session_id,
                "sample_count": row["sample_count"],
                "duration": _seconds(row.get("duration_ms")),
                "rpm_min": row.get("rpm_min"),
                "rpm_max": row.get("rpm_max"),
                "rpm_median": row.get("rpm_median"),
                "tps_range": row.get("tps_raw_range"),
                "battery_min": row.get("battery_voltage_min"),
                "battery_median": row.get("battery_voltage_median"),
                "iat_range": row.get("iat_c_range"),
                "ect_start": row.get("ect_c_start"),
                "ect_end": row.get("ect_c_end"),
                "ect_delta": row.get("ect_c_delta"),
                "current_status": row.get("overall_status"),
                "anomaly_ratio": row.get("anomaly_ratio"),
                "most_unusual_features": row.get("most_unusual_features"),
                "suggested_context": suggested_context(row),
                "human_label": "",
                "human_notes": "",
            }
        )
    return sorted(rows, key=lambda item: str(item["session_id"]))


def suggested_context(row: dict[str, Any]) -> str:
    contexts: list[str] = []
    if int(row.get("window_count") or 0) < EvidencePolicy().min_window_count:
        contexts.append("too_short_candidate")
    if _finite(row.get("ect_c_start")) and _finite(row.get("ect_c_end")):
        ect_start = float(row["ect_c_start"])
        ect_delta = float(row.get("ect_c_delta") or 0.0)
        if ect_start < 75.0 and ect_delta >= 15.0:
            contexts.append("warmup_candidate")
    if _finite(row.get("rpm_median")) and _finite(row.get("tps_range")):
        if float(row["rpm_median"]) < 1800.0 and float(row["tps_range"]) <= 10.0:
            contexts.append("idle_heavy_candidate")
    if _finite(row.get("battery_voltage_min")) and float(row["battery_voltage_min"]) < 11.5:
        contexts.append("battery_low_candidate")
    if _finite(row.get("ect_c_max")) and float(row["ect_c_max"]) > 100.0:
        contexts.append("high_ect_candidate")
    if _finite(row.get("rpm_max")) and float(row["rpm_max"]) > 8000.0:
        contexts.append("high_rpm_candidate")
    return ";".join(contexts) if contexts else "review_required"


def build_model_comparison_rows(
    report_rows: list[dict[str, Any]],
    labels: list[dict[str, str]],
    candidate_trained: bool,
    candidate_version: str | None,
) -> list[dict[str, Any]]:
    labels_by_session = {row["session_id"]: row["label"] for row in labels}
    rows: list[dict[str, Any]] = []
    for row in sorted(report_rows, key=lambda item: str(item["session_id"])):
        if candidate_trained:
            new_status = row["overall_status"]
            new_health = row["health_score"]
            new_anomaly_ratio = row["anomaly_ratio"]
            note = "candidate evaluation not implemented by this branch"
        else:
            new_status = "candidate_not_trained"
            new_health = ""
            new_anomaly_ratio = ""
            note = "insufficient confirmed normal labels; production model unchanged"
        rows.append(
            {
                "session_id": row["session_id"],
                "label": labels_by_session.get(row["session_id"], "unknown"),
                "old_status": row["overall_status"],
                "old_health": row["health_score"],
                "old_anomaly_ratio": row["anomaly_ratio"],
                "new_status": new_status,
                "new_health": new_health,
                "new_anomaly_ratio": new_anomaly_ratio,
                "status_changed": bool(candidate_trained and new_status != row["overall_status"]),
                "candidate_model_version": candidate_version or "",
                "candidate_note": note,
                "evidence_policy_status": row.get("evidence_policy_status"),
                "evidence_policy_reason": row.get("evidence_policy_reason"),
            }
        )
    return rows


def load_training_dataset(model_metadata: dict[str, Any], settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = Path(model_metadata.get("training_source", "real_data/real_run"))
    sessions = [str(item) for item in model_metadata.get("training_sessions", [])]
    frame_parts: list[pd.DataFrame] = []
    feature_parts: list[pd.DataFrame] = []
    for session_name in sessions:
        path = source / session_name
        loaded = load_profile_v02_jsonl_file(path, strict_checksum=True, allow_legacy_29_byte=False)
        frame_data = loaded.frame_data.copy()
        frame_data["source_file"] = session_name
        windows = create_windows(
            frame_data,
            window_size_samples=settings.window_size_samples,
            step_size_samples=settings.window_step_samples,
            max_checksum_error_ratio=settings.max_window_checksum_error_ratio,
            max_invalid_decoded_ratio=settings.max_window_invalid_decoded_ratio,
        )
        features = extract_window_features(frame_data, windows, signal_columns=V02_ML_SIGNAL_COLUMNS)
        features.insert(0, "source_file", session_name)
        frame_parts.append(frame_data)
        feature_parts.append(features)
    frames = pd.concat(frame_parts, ignore_index=True) if frame_parts else pd.DataFrame()
    features = pd.concat(feature_parts, ignore_index=True) if feature_parts else pd.DataFrame()
    return frames, features


def run_false_positive_audit(
    *,
    data_dir: Path = Path("data"),
    model_dir: Path = CURRENT_V2_MODEL_DIR,
    output_dir: Path = Path("data/calibration"),
    docs_dir: Path = Path("docs/ml"),
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or Settings(data_dir=data_dir, model_dir=model_dir)
    selected_dirs, excluded_sessions = discover_v2_session_dirs(data_dir)
    evaluations = [evaluate_session(load_telemetry_session(path), settings, model_dir) for path in selected_dirs]
    report_rows = [item.row for item in evaluations]
    report_rows.sort(key=lambda row: str(row["session_id"]))
    labels = build_session_labels(report_rows)
    can_train, candidate_reason = can_train_candidate(labels)
    candidate_trained = False
    candidate_version = None

    label_review_rows = build_label_review_rows(report_rows, labels)
    comparison_rows = build_model_comparison_rows(report_rows, labels, candidate_trained, candidate_version)

    output_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "current_model_session_report.csv", report_rows)
    _write_csv(output_dir / "session_labels.csv", labels)
    _write_csv(output_dir / "SESSION_LABEL_REVIEW.csv", label_review_rows)
    _write_csv(output_dir / "model_comparison.csv", comparison_rows)

    model_metadata = json.loads((model_dir / "model_metadata.json").read_text(encoding="utf-8"))
    training_frames, training_features = load_training_dataset(model_metadata, settings)
    feature_audit = build_feature_audit(training_features, evaluations, labels, model_metadata)
    short_session_audit = build_short_session_audit(report_rows)
    training_audit = build_training_audit(model_metadata, training_frames, training_features, report_rows)
    aggregate = {
        "sessions_evaluated": len(report_rows),
        "records_evaluated": int(sum(int(row["sample_count"]) for row in report_rows)),
        "excluded_sessions": excluded_sessions,
        "labels": Counter(row["label"] for row in labels),
        "status_distribution": Counter(str(row["overall_status"]) for row in report_rows),
        "policy_status_distribution": Counter(str(row["evidence_policy_status"]) for row in report_rows),
        "candidate_training_possible": can_train,
        "candidate_training_reason": candidate_reason,
        "candidate_trained": candidate_trained,
        "candidate_version": candidate_version,
        "model_version": model_metadata.get("model_version_id") or model_metadata.get("version"),
    }

    (docs_dir / "CURRENT_MODEL_FALSE_POSITIVE_AUDIT.md").write_text(
        current_model_report_markdown(
            aggregate=aggregate,
            report_rows=report_rows,
            labels=labels,
            short_session_audit=short_session_audit,
            feature_audit=feature_audit,
        ),
        encoding="utf-8",
    )
    (docs_dir / "TRAINING_DISTRIBUTION_AUDIT.md").write_text(
        training_distribution_markdown(training_audit),
        encoding="utf-8",
    )
    (docs_dir / "ANOMALY_CALIBRATION_REPORT.md").write_text(
        anomaly_calibration_markdown(
            aggregate=aggregate,
            labels=labels,
            report_rows=report_rows,
            comparison_rows=comparison_rows,
            short_session_audit=short_session_audit,
            feature_audit=feature_audit,
            label_manifest_hash=_sha256(output_dir / "session_labels.csv"),
        ),
        encoding="utf-8",
    )

    return {
        **aggregate,
        "report_paths": {
            "current_model_session_report": str(output_dir / "current_model_session_report.csv"),
            "session_labels": str(output_dir / "session_labels.csv"),
            "session_label_review": str(output_dir / "SESSION_LABEL_REVIEW.csv"),
            "model_comparison": str(output_dir / "model_comparison.csv"),
            "current_model_audit": str(docs_dir / "CURRENT_MODEL_FALSE_POSITIVE_AUDIT.md"),
            "training_distribution_audit": str(docs_dir / "TRAINING_DISTRIBUTION_AUDIT.md"),
            "anomaly_calibration_report": str(docs_dir / "ANOMALY_CALIBRATION_REPORT.md"),
        },
    }


def build_short_session_audit(report_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    df = pd.DataFrame(report_rows)
    if df.empty:
        return rows
    for bucket, group in df.groupby("window_bucket", sort=False):
        health = pd.to_numeric(group["health_score"], errors="coerce").dropna()
        rows.append(
            {
                "window_bucket": bucket,
                "session_count": int(len(group)),
                "median_anomaly_ratio": _round(group["anomaly_ratio"].median()),
                "status_distribution": _counter_text(group["overall_status"].tolist()),
                "health_p10": _round(health.quantile(0.10)) if not health.empty else "",
                "health_median": _round(health.median()) if not health.empty else "",
                "health_p90": _round(health.quantile(0.90)) if not health.empty else "",
            }
        )
    order = {"0": 0, "1-4": 1, "5-9": 2, "10-19": 3, "20-49": 4, "50+": 5}
    return sorted(rows, key=lambda row: order.get(str(row["window_bucket"]), 99))


def build_feature_audit(
    training_features: pd.DataFrame,
    evaluations: list[SessionEvaluation],
    labels: list[dict[str, str]],
    model_metadata: dict[str, Any],
) -> dict[str, Any]:
    label_by_session = {row["session_id"]: row["label"] for row in labels}
    unusual_counter: Counter[str] = Counter()
    eval_feature_parts: list[pd.DataFrame] = []
    for evaluation in evaluations:
        session_id = evaluation.session.session_id or ""
        windows = evaluation.windows
        if windows.empty:
            continue
        for items in windows.get("most_unusual_features", pd.Series(dtype=str)).fillna("").tolist():
            unusual_counter.update(feature for feature in str(items).split(";") if feature)
        feature_rows = windows.copy()
        feature_rows.insert(0, "session_id", session_id)
        feature_rows.insert(1, "label", label_by_session.get(session_id, "unknown"))
        eval_feature_parts.append(feature_rows)
    eval_features = pd.concat(eval_feature_parts, ignore_index=True) if eval_feature_parts else pd.DataFrame()
    top_features = [feature for feature, _count in unusual_counter.most_common(12)]
    feature_rows: list[dict[str, Any]] = []
    for feature in top_features:
        train_series = pd.to_numeric(training_features.get(feature, pd.Series(dtype=float)), errors="coerce").dropna()
        baseline = _series_distribution(train_series)
        feature_rows.append({"feature": feature, "group": "training_baseline", **baseline, "outlier_frequency": ""})
        for label in ["normal_ride", "warmup", "idle_heavy", "too_short", "known_abnormal", "unknown"]:
            if eval_features.empty or feature not in eval_features:
                series = pd.Series(dtype=float)
            else:
                series = pd.to_numeric(eval_features.loc[eval_features["label"] == label, feature], errors="coerce").dropna()
            feature_rows.append(
                {
                    "feature": feature,
                    "group": label,
                    **_series_distribution(series),
                    "outlier_frequency": _outlier_frequency(series, train_series),
                }
            )
    return {
        "top_unusual_features": unusual_counter.most_common(20),
        "feature_rows": feature_rows,
        "training_feature_count": int(len(training_features)),
        "feature_schema_version": model_metadata.get("feature_schema_version"),
    }


def build_training_audit(
    model_metadata: dict[str, Any],
    training_frames: pd.DataFrame,
    training_features: pd.DataFrame,
    report_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    training_sessions: list[dict[str, Any]] = []
    if not training_frames.empty:
        for source_file, group in training_frames.groupby("source_file"):
            windows = training_features[training_features["source_file"] == source_file]
            training_sessions.append(
                {
                    "source_file": source_file,
                    "sample_count": int(len(group)),
                    "window_count": int(len(windows)),
                    **_frame_range_summary(group),
                }
            )
    eval_df = pd.DataFrame(report_rows)
    return {
        "model_metadata": model_metadata,
        "training_sessions": sorted(training_sessions, key=lambda row: str(row["source_file"])),
        "training_distribution": _frame_range_summary(training_frames),
        "evaluation_distribution": _report_range_summary(eval_df),
        "training_window_count_recomputed": int(len(training_features)),
        "distribution_gaps": distribution_gaps(training_frames, eval_df),
    }


def distribution_gaps(training_frames: pd.DataFrame, eval_df: pd.DataFrame) -> list[str]:
    gaps: list[str] = []
    if training_frames.empty or eval_df.empty:
        return ["insufficient data to compare distributions"]
    training_duration = _session_durations_from_frames(training_frames)
    eval_duration = pd.to_numeric(eval_df.get("duration_ms"), errors="coerce").dropna()
    train_ect_min = _column_min(training_frames, "ect_c")
    eval_ect_min = _column_min(eval_df, "ect_c_min")
    train_batt_min = _column_min(training_frames, "battery_voltage")
    eval_batt_min = _column_min(eval_df, "battery_voltage_min")
    train_rpm_max = _column_max(training_frames, "rpm")
    eval_rpm_max = _column_max(eval_df, "rpm_max")
    train_tps_max = _column_max(training_frames, "tps_raw")
    eval_tps_max = _column_max(eval_df, "tps_raw_max")
    train_ect_max = _column_max(training_frames, "ect_c")
    eval_ect_max = _column_max(eval_df, "ect_c_max")

    if _finite(train_ect_min) and _finite(eval_ect_min) and float(eval_ect_min) + 10 < float(train_ect_min):
        gaps.append(f"cold/warm-up temperatures underrepresented: training ECT min {train_ect_min}, eval ECT min {eval_ect_min}")
    if _finite(train_batt_min) and _finite(eval_batt_min) and float(eval_batt_min) + 1.0 < float(train_batt_min):
        gaps.append(f"low battery/key-on range underrepresented: training battery min {train_batt_min}, eval min {eval_batt_min}")
    if _finite(train_rpm_max) and _finite(eval_rpm_max) and float(eval_rpm_max) > float(train_rpm_max) * 1.2:
        gaps.append(f"high RPM/load range extends beyond training: training RPM max {train_rpm_max}, eval max {eval_rpm_max}")
    if _finite(train_tps_max) and _finite(eval_tps_max) and float(eval_tps_max) > float(train_tps_max):
        gaps.append(f"high TPS raw range extends beyond training: training TPS raw max {train_tps_max}, eval max {eval_tps_max}")
    if _finite(train_ect_max) and _finite(eval_ect_max) and float(eval_ect_max) > float(train_ect_max) + 5.0:
        gaps.append(f"high ECT range extends beyond training: training ECT max {train_ect_max}, eval max {eval_ect_max}")
    low_battery_train = _fraction_below(training_frames, "battery_voltage", 12.0)
    low_battery_eval = _fraction_below(eval_df, "battery_voltage_min", 12.0)
    if _finite(low_battery_train) and _finite(low_battery_eval) and float(low_battery_eval) > max(0.02, float(low_battery_train) * 5.0):
        gaps.append(
            f"low-battery sessions are more common in evaluation: training frame fraction below 12V "
            f"{float(low_battery_train):.4f}, eval session fraction with min below 12V {float(low_battery_eval):.4f}"
        )
    if not training_duration.empty and not eval_duration.empty and float(eval_duration.max()) > float(training_duration.max()) * 1.2:
        gaps.append(
            f"long sessions exceed training coverage: training max {float(training_duration.max()):.0f} ms, "
            f"eval max {float(eval_duration.max()):.0f} ms"
        )
    if not gaps:
        gaps.append("no major distribution gap detected by simple range checks")
    return gaps


def current_model_report_markdown(
    *,
    aggregate: dict[str, Any],
    report_rows: list[dict[str, Any]],
    labels: list[dict[str, str]],
    short_session_audit: list[dict[str, Any]],
    feature_audit: dict[str, Any],
) -> str:
    status_table = _counter_markdown(aggregate["status_distribution"])
    policy_table = _counter_markdown(aggregate["policy_status_distribution"])
    label_table = _counter_markdown(aggregate["labels"])
    short_table = _markdown_table(short_session_audit)
    top_features = "\n".join(
        f"- `{feature}`: {count} window mentions" for feature, count in feature_audit["top_unusual_features"][:12]
    )
    limited = [row for row in report_rows if row.get("evidence_policy_status") == "limited_data"]
    limited_lines = "\n".join(
        f"- `{row['session_id']}`: {row['window_count']} windows, {row['anomaly_window_count']} anomalous, "
        f"ratio {float(row['anomaly_ratio']):.3f}, current `{row['overall_status']}`"
        for row in limited
    ) or "- None"
    return f"""# Current Model False-Positive Audit

## Scope

- Evaluated model: `{aggregate['model_version']}`
- Evaluated sessions: {aggregate['sessions_evaluated']}
- Evaluated telemetry records: {aggregate['records_evaluated']}
- Telemetry schema: `{CANONICAL_TELEMETRY_SCHEMA_VERSION_V2}`
- Decoder: `{V2_DECODER_KEY}`
- Excluded non-current-schema sessions: {len(aggregate['excluded_sessions'])}

Isolation Forest is treated as an out-of-distribution detector, not mechanical
ground truth. A high anomaly score means the window differs from the training
baseline, not that the vehicle is faulty.

## Current Status Distribution

{status_table}

## Objectively Known Labels

{label_table}

No `normal_ride` labels were assigned from score appearance alone. Unknown
sessions are intentionally held for human review.

## Short-Session Sensitivity

{short_table}

The current ratio thresholds are unstable for low window counts. For example,
one anomalous window in seven windows is a 14.3% ratio, while fourteen anomalous
windows in seven hundred windows is only 2.0%. Those cases carry very different
evidence weight even when the same ratio thresholds are used.

Sessions downgraded by the audit-only minimum-evidence policy:

{limited_lines}

## Commonly Flagged Features

{top_features}

These features are not automatically bad. Several are plausible indicators of
training-distribution gaps: battery level, cold/warm-up ECT movement, and
constant IAT/TPS behavior can be normal operational states if they are properly
represented in the baseline.

## Finding

The false-positive behavior is primarily consistent with model calibration and
training distribution mismatch, plus low-window evidence quantization. Cloud,
HTTP transport, RAW normalization, canonical V2 semantics, and ECU byte mapping
are outside this audit and were not changed.
"""


def training_distribution_markdown(audit: dict[str, Any]) -> str:
    metadata = audit["model_metadata"]
    training_rows = audit["training_sessions"]
    training_table = _markdown_table(training_rows)
    gaps = "\n".join(f"- {gap}" for gap in audit["distribution_gaps"])
    return f"""# Training Distribution Audit

## Model

- Model version: `{metadata.get('model_version_id') or metadata.get('version')}`
- Training source: `{metadata.get('training_source')}`
- Training session count: {len(metadata.get('training_sessions', []))}
- Metadata training windows: {metadata.get('training_window_count')}
- Recomputed training windows: {audit['training_window_count_recomputed']}
- Feature schema: `{metadata.get('feature_schema_version')}`
- Telemetry schema: `{metadata.get('telemetry_schema_version')}`
- Decoder versions: `{', '.join(metadata.get('training_decoder_versions', []))}`

## Training Sessions

{training_table}

## Distribution Comparison

Training distribution:

{_markdown_table([audit['training_distribution']])}

Current evaluation distribution:

{_markdown_table([audit['evaluation_distribution']])}

## Supported Gaps

{gaps}

The current baseline is built from road-run files only. It should not be treated
as proof that cold start, idle-heavy, low-battery key-on/cranking, or very short
sessions are mechanically abnormal unless those states are represented and
labelled in the training/evaluation set.
"""


def anomaly_calibration_markdown(
    *,
    aggregate: dict[str, Any],
    labels: list[dict[str, str]],
    report_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    short_session_audit: list[dict[str, Any]],
    feature_audit: dict[str, Any],
    label_manifest_hash: str,
) -> str:
    normal_count = sum(1 for row in labels if row["label"] == "normal_ride")
    known_abnormal_count = sum(1 for row in labels if row["label"] == "known_abnormal")
    unknown_count = sum(1 for row in labels if row["label"] == "unknown")
    old_known_abnormal = [row for row in comparison_rows if row["label"] == "known_abnormal"]
    old_abnormal_lines = "\n".join(
        f"- `{row['session_id']}`: old `{row['old_status']}`, health {row['old_health']}, "
        f"anomaly ratio {row['old_anomaly_ratio']}, policy `{row['evidence_policy_status']}`"
        for row in old_known_abnormal
    ) or "- None"
    feature_table = _markdown_table(feature_audit["feature_rows"][:84])
    return f"""# Anomaly Calibration Report

## Label Manifest

- Label manifest SHA-256: `{label_manifest_hash}`
- Objectively labelled `normal_ride`: {normal_count}
- Objectively labelled `known_abnormal`: {known_abnormal_count}
- Unknown sessions needing review: {unknown_count}

## Candidate Baseline

Candidate model was not trained.

Reason: {aggregate['candidate_training_reason']}.

The proposed normal-training set is empty because this audit found no
objectively confirmed `normal_ride` sessions. Unknown sessions were not used as
normal data. The production model remains unchanged.

## Old vs Candidate

- Old model status distribution: {_counter_text([row['overall_status'] for row in report_rows])}
- Candidate status distribution: `candidate_not_trained={len(report_rows)}`
- Old false-positive count on known normal: not computed because there are 0 confirmed normal sessions.
- Candidate false-positive count on known normal: not computed because no candidate was trained.

Known abnormal/injected behavior under the old model:

{old_abnormal_lines}

## Minimum-Evidence Policy Recommendation

Use an interpretation gate before assigning strong session-level `attention` or
`monitor`:

- minimum windows: {EvidencePolicy().min_window_count}
- minimum eligible samples: {EvidencePolicy().min_eligible_samples}
- minimum duration: {int(EvidencePolicy().min_duration_ms)} ms

This does not hide per-window anomalies. It only prevents a very short session
from receiving a strong session-level conclusion because one or two windows
dominate the anomaly ratio.

Short-session audit:

{_markdown_table(short_session_audit)}

## Threshold Recommendation

Keep the current production thresholds for now:

- `attention`: `anomaly_ratio >= 0.15` or `health_score < 50`
- `monitor`: `anomaly_ratio >= 0.02` or `health_score < 80`

There is insufficient labelled evidence for threshold recalibration. Loosening
thresholds against unknown sessions would only make the dashboard look nicer;
it would not prove lower false positives.

## Operational-State Recommendation

Prefer a representative normal training distribution first. Specifically,
collect labelled normal rides that include cold/warm-up, idle-heavy, key-on or
charging-state battery variation, and longer rides. Do not introduce multiple
state-specific models until labelled data proves one baseline cannot cover
those states.

## Feature-Level Calibration Table

{feature_table}
"""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _session_duration_ms(frame_data: pd.DataFrame) -> float | None:
    if frame_data.empty or "relative_time_ms" not in frame_data:
        return None
    series = pd.to_numeric(frame_data["relative_time_ms"], errors="coerce").dropna()
    if len(series) < 2:
        return None
    return _round(float(series.max() - series.min()))


def _decision_score_stats(windows: pd.DataFrame) -> dict[str, Any]:
    if windows.empty or "decision_score" not in windows:
        return {
            "decision_score_min": "",
            "decision_score_p10": "",
            "decision_score_median": "",
            "decision_score_p90": "",
            "decision_score_max": "",
        }
    series = pd.to_numeric(windows["decision_score"], errors="coerce").dropna()
    if series.empty:
        return {
            "decision_score_min": "",
            "decision_score_p10": "",
            "decision_score_median": "",
            "decision_score_p90": "",
            "decision_score_max": "",
        }
    return {
        "decision_score_min": _round(series.min()),
        "decision_score_p10": _round(series.quantile(0.10)),
        "decision_score_median": _round(series.median()),
        "decision_score_p90": _round(series.quantile(0.90)),
        "decision_score_max": _round(series.max()),
    }


def _signal_stats(frame_data: pd.DataFrame) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for column in V2_CORE_SIGNAL_COLUMNS:
        series = pd.to_numeric(frame_data.get(column, pd.Series(dtype=float)), errors="coerce").dropna()
        if series.empty:
            stats[f"{column}_min"] = ""
            stats[f"{column}_max"] = ""
            stats[f"{column}_median"] = ""
            stats[f"{column}_range"] = ""
            stats[f"{column}_start"] = ""
            stats[f"{column}_end"] = ""
            stats[f"{column}_delta"] = ""
            continue
        stats[f"{column}_min"] = _round(series.min())
        stats[f"{column}_max"] = _round(series.max())
        stats[f"{column}_median"] = _round(series.median())
        stats[f"{column}_range"] = _round(series.max() - series.min())
        stats[f"{column}_start"] = _round(series.iloc[0])
        stats[f"{column}_end"] = _round(series.iloc[-1])
        stats[f"{column}_delta"] = _round(series.iloc[-1] - series.iloc[0])
    return stats


def _frame_range_summary(frame_data: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "sample_count": int(len(frame_data)),
        "duration_ms_min": "",
        "duration_ms_median": "",
        "duration_ms_max": "",
    }
    durations = _session_durations_from_frames(frame_data)
    if not durations.empty:
        summary["duration_ms_min"] = _round(durations.min())
        summary["duration_ms_median"] = _round(durations.median())
        summary["duration_ms_max"] = _round(durations.max())
    for column in V2_CORE_SIGNAL_COLUMNS:
        series = pd.to_numeric(frame_data.get(column, pd.Series(dtype=float)), errors="coerce").dropna()
        summary[f"{column}_min"] = _round(series.min()) if not series.empty else ""
        summary[f"{column}_median"] = _round(series.median()) if not series.empty else ""
        summary[f"{column}_max"] = _round(series.max()) if not series.empty else ""
    return summary


def _report_range_summary(eval_df: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {"session_count": int(len(eval_df))}
    if eval_df.empty:
        return summary
    summary["sample_count"] = int(pd.to_numeric(eval_df["sample_count"], errors="coerce").sum())
    duration = pd.to_numeric(eval_df.get("duration_ms"), errors="coerce").dropna()
    summary["duration_ms_min"] = _round(duration.min()) if not duration.empty else ""
    summary["duration_ms_median"] = _round(duration.median()) if not duration.empty else ""
    summary["duration_ms_max"] = _round(duration.max()) if not duration.empty else ""
    for column in V2_CORE_SIGNAL_COLUMNS:
        mins = pd.to_numeric(eval_df.get(f"{column}_min"), errors="coerce").dropna()
        medians = pd.to_numeric(eval_df.get(f"{column}_median"), errors="coerce").dropna()
        maxes = pd.to_numeric(eval_df.get(f"{column}_max"), errors="coerce").dropna()
        summary[f"{column}_min"] = _round(mins.min()) if not mins.empty else ""
        summary[f"{column}_median"] = _round(medians.median()) if not medians.empty else ""
        summary[f"{column}_max"] = _round(maxes.max()) if not maxes.empty else ""
    return summary


def _session_durations_from_frames(frame_data: pd.DataFrame) -> pd.Series:
    if frame_data.empty or "source_file" not in frame_data or "relative_time_ms" not in frame_data:
        return pd.Series(dtype=float)
    durations: list[float] = []
    for _source, group in frame_data.groupby("source_file"):
        values = pd.to_numeric(group["relative_time_ms"], errors="coerce").dropna()
        if len(values) >= 2:
            durations.append(float(values.max() - values.min()))
    return pd.Series(durations, dtype=float)


def _series_distribution(series: pd.Series) -> dict[str, Any]:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if series.empty:
        return {"count": 0, "median": "", "iqr": "", "p05": "", "p95": ""}
    q25 = float(series.quantile(0.25))
    q75 = float(series.quantile(0.75))
    return {
        "count": int(series.count()),
        "median": _round(series.median()),
        "iqr": _round(q75 - q25),
        "p05": _round(series.quantile(0.05)),
        "p95": _round(series.quantile(0.95)),
    }


def _outlier_frequency(series: pd.Series, baseline: pd.Series) -> Any:
    series = pd.to_numeric(series, errors="coerce").dropna()
    baseline = pd.to_numeric(baseline, errors="coerce").dropna()
    if series.empty or baseline.empty:
        return ""
    low = float(baseline.quantile(0.05))
    high = float(baseline.quantile(0.95))
    return _round(((series < low) | (series > high)).mean())


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No rows._"
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                headers.append(key)
                seen.add(key)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_markdown_cell(row.get(header, "")) for header in headers) + " |")
    return "\n".join(lines)


def _counter_markdown(counter: Counter[str]) -> str:
    rows = [{"value": key, "count": value} for key, value in sorted(counter.items())]
    return _markdown_table(rows)


def _counter_text(values: list[Any]) -> str:
    counter = Counter(str(value) for value in values)
    return ", ".join(f"{key}={counter[key]}" for key in sorted(counter)) or "none"


def _markdown_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _seconds(duration_ms: Any) -> Any:
    if not _finite(duration_ms):
        return ""
    return _round(float(duration_ms) / 1000.0)


def _round(value: Any, digits: int = 6) -> Any:
    if not _finite(value):
        return ""
    rounded = round(float(value), digits)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _finite(value: Any) -> bool:
    try:
        return value is not None and value != "" and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _column_min(frame: pd.DataFrame, column: str) -> Any:
    series = pd.to_numeric(frame.get(column, pd.Series(dtype=float)), errors="coerce").dropna()
    return _round(series.min()) if not series.empty else ""


def _column_max(frame: pd.DataFrame, column: str) -> Any:
    series = pd.to_numeric(frame.get(column, pd.Series(dtype=float)), errors="coerce").dropna()
    return _round(series.max()) if not series.empty else ""


def _fraction_below(frame: pd.DataFrame, column: str, threshold: float) -> Any:
    series = pd.to_numeric(frame.get(column, pd.Series(dtype=float)), errors="coerce").dropna()
    if series.empty:
        return ""
    return float((series < threshold).mean())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_current_model_loadable(model_dir: Path = CURRENT_V2_MODEL_DIR) -> dict[str, Any]:
    bundle = load_model_bundle(model_dir)
    metadata = bundle.metadata
    if metadata.get("model_version_id") != CURRENT_V2_MODEL_VERSION:
        raise AssertionError(f"unexpected model version: {metadata.get('model_version_id')}")
    if metadata.get("telemetry_schema_version") != CANONICAL_TELEMETRY_SCHEMA_VERSION_V2:
        raise AssertionError("current model is not canonical-telemetry-v2")
    if metadata.get("signal_columns") != V02_ML_SIGNAL_COLUMNS:
        raise AssertionError("current model signal columns do not match V2 ML columns")
    if V2_DECODER_KEY not in metadata.get("training_decoder_versions", []):
        raise AssertionError("current model decoder provenance does not include V2 decoder")
    return metadata
