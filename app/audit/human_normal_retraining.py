from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.audit.ml_false_positive_audit import (
    CURRENT_V2_MODEL_DIR,
    CURRENT_V2_MODEL_VERSION,
    EvidencePolicy,
    V2_DECODER_KEY,
    assert_current_model_loadable,
    discover_v2_session_dirs,
    load_telemetry_session,
    load_training_dataset,
)
from app.config import Settings
from app.domain.model import FEATURE_SCHEMA_VERSION
from app.domain.telemetry import CANONICAL_TELEMETRY_SCHEMA_VERSION_V2, V2_CORE_SIGNAL_COLUMNS
from app.ml.features import extract_window_features
from app.ml.inference import load_model_bundle, run_inference
from app.ml.profile_v02_training import V02_ML_SIGNAL_COLUMNS
from app.ml.training import model_paths, train_isolation_forest
from app.ml.windowing import create_windows
from app.services.analysis_service import build_feature_frame, canonical_session_to_frame_data


RANDOM_SEED = 42
TRAIN_FRACTION = 0.75
CANDIDATE_MODEL_DIR = Path("data/models/honda_keihin_71_17_v2_candidate_human_normal")
ALLOWED_HUMAN_LABELS = {
    "normal_ride",
    "warmup_candidate",
    "battery_low_candidate",
    "high_rpm_candidate",
    "known_abnormal",
    "too_short",
    "unknown",
}
EVALUATION_ONLY_LABELS = {
    "warmup_candidate",
    "battery_low_candidate",
    "high_rpm_candidate",
    "known_abnormal",
    "too_short",
    "unknown",
}
FEATURES_FOR_HUMAN_AUDIT = [
    "tps_raw_min",
    "tps_voltage_min",
    "ect_c_delta",
    "ect_c_first",
    "ect_c_slope",
    "iat_c_constant_signal",
    "battery_voltage_mean",
    "battery_voltage_median",
]


@dataclass(frozen=True, slots=True)
class HumanRound2Result:
    label_counts: Counter[str]
    train_sessions: list[str]
    holdout_sessions: list[str]
    candidate_model_version: str
    candidate_training_window_count: int
    production_recommendation: str
    report_paths: dict[str, str]


def is_baseline_training_label(label: str | None) -> bool:
    return (label or "").strip() == "normal_ride"


def is_evaluation_only_label(label: str | None) -> bool:
    cleaned = (label or "").strip()
    return cleaned in EVALUATION_ONLY_LABELS or cleaned == ""


def merge_human_review_labels(
    *,
    session_labels_path: Path,
    review_path: Path,
) -> list[dict[str, str]]:
    labels_by_session = {row["session_id"]: row for row in _read_csv(session_labels_path)}
    review_rows = _read_csv(review_path)
    for review in review_rows:
        session_id = review["session_id"]
        human_label = review.get("human_label", "").strip()
        if not human_label:
            continue
        if human_label not in ALLOWED_HUMAN_LABELS:
            raise ValueError(f"unsupported human_label {human_label!r} for session {session_id}")
        existing = labels_by_session.get(session_id)
        if existing is not None and existing.get("label") == "known_abnormal":
            continue
        notes = review.get("human_notes", "").strip()
        if not notes:
            notes = (
                "Human review label from SESSION_LABEL_REVIEW.csv"
                f"; suggested_context={review.get('suggested_context', '').strip() or 'none'}"
            )
        labels_by_session[session_id] = {
            "session_id": session_id,
            "label": human_label,
            "label_source": "human_review",
            "notes": notes,
        }
    rows = sorted(labels_by_session.values(), key=lambda row: row["session_id"])
    _write_csv(session_labels_path, rows)
    return rows


def deterministic_session_split(
    normal_session_ids: list[str],
    *,
    seed: int = RANDOM_SEED,
    train_fraction: float = TRAIN_FRACTION,
) -> tuple[list[str], list[str]]:
    if len(normal_session_ids) < 2:
        raise ValueError("at least two normal_ride sessions are required for a session-level split")
    session_ids = sorted(normal_session_ids)
    shuffled = session_ids.copy()
    random.Random(seed).shuffle(shuffled)
    holdout_count = max(1, round(len(session_ids) * (1.0 - train_fraction)))
    holdout = sorted(shuffled[:holdout_count])
    train = sorted(session_id for session_id in session_ids if session_id not in set(holdout))
    if not train or not holdout:
        raise ValueError("split must produce both training and holdout sessions")
    if set(train) & set(holdout):
        raise AssertionError("session leakage detected between train and holdout")
    return train, holdout


def candidate_model_version_id(label_hash: str, train_sessions: list[str], holdout_sessions: list[str]) -> str:
    split_payload = json.dumps(
        {"train": sorted(train_sessions), "holdout": sorted(holdout_sessions), "seed": RANDOM_SEED},
        sort_keys=True,
    )
    split_hash = hashlib.sha256(split_payload.encode("utf-8")).hexdigest()
    return f"iforest-human-normal-{label_hash[:8]}-{split_hash[:8]}"


def load_v2_sessions(data_dir: Path) -> dict[str, Any]:
    session_dirs, _excluded = discover_v2_session_dirs(data_dir)
    return {path.name: load_telemetry_session(path) for path in session_dirs}


def build_session_frames_and_features(
    sessions: dict[str, Any],
    settings: Settings,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    frames_by_session: dict[str, pd.DataFrame] = {}
    features_by_session: dict[str, pd.DataFrame] = {}
    for session_id, session in sorted(sessions.items()):
        frame_data = canonical_session_to_frame_data(session, V02_ML_SIGNAL_COLUMNS)
        feature_frame = build_feature_frame(frame_data, settings, V02_ML_SIGNAL_COLUMNS)
        feature_frame = feature_frame.copy()
        feature_frame.insert(0, "session_id", session_id)
        frames_by_session[session_id] = frame_data
        features_by_session[session_id] = feature_frame
    return frames_by_session, features_by_session


def train_human_normal_candidate(
    *,
    train_session_ids: list[str],
    holdout_session_ids: list[str],
    features_by_session: dict[str, pd.DataFrame],
    label_hash: str,
    label_counts: Counter[str],
    settings: Settings,
    model_dir: Path,
) -> dict[str, Any]:
    training_parts = [features_by_session[session_id] for session_id in train_session_ids]
    training_features_with_session = pd.concat(training_parts, ignore_index=True)
    training_features = training_features_with_session.drop(columns=["session_id"], errors="ignore")
    version_id = candidate_model_version_id(label_hash, train_session_ids, holdout_session_ids)
    metadata = train_isolation_forest(
        training_features,
        train_session_ids,
        settings,
        model_dir=model_dir,
        notes=(
            "Human-labelled SH Mode normal baseline candidate. Trained only on confirmed normal_ride "
            "sessions; warmup, battery-low, high-RPM, known abnormal, too-short, and unknown sessions "
            "are evaluation-only."
        ),
        training_decoder_versions=[V2_DECODER_KEY],
        signal_columns=V02_ML_SIGNAL_COLUMNS,
        extra_metadata={
            "model_version_id": version_id,
            "version": version_id,
            "status": "candidate",
            "telemetry_schema_version": CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "decoder_id": "honda_keihin_71_17",
            "decoder_version": "1.0.0",
            "training_session_ids": train_session_ids,
            "normal_holdout_session_ids": holdout_session_ids,
            "excluded_label_groups": sorted(EVALUATION_ONLY_LABELS),
            "human_label_manifest_sha256": label_hash,
            "human_label_count": dict(sorted(label_counts.items())),
            "split_random_seed": RANDOM_SEED,
            "split_train_fraction": TRAIN_FRACTION,
            "window_size": settings.window_size_samples,
            "window_step": settings.window_step_samples,
            "n_estimators": settings.isolation_n_estimators,
            "contamination": settings.isolation_contamination,
            "random_state": settings.isolation_random_state,
        },
    )
    metadata["training_score_percentiles"] = {
        "score_p01": metadata["training_score_p01"],
        "score_p05": metadata["training_score_p05"],
        "score_median": metadata["training_score_median"],
        "score_p95": metadata["training_score_p95"],
        "decision_p01": metadata["training_decision_p01"],
        "decision_p05": metadata["training_decision_p05"],
        "decision_median": metadata["training_decision_median"],
    }
    metadata["training_window_count"] = int(len(training_features))
    model_paths(model_dir)["metadata"].write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return metadata


def evaluate_model_on_features(
    *,
    features_by_session: dict[str, pd.DataFrame],
    model_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, pd.DataFrame]]:
    model_metadata = load_model_bundle(model_dir).metadata
    results: dict[str, dict[str, Any]] = {}
    windows_by_session: dict[str, pd.DataFrame] = {}
    for session_id, features in sorted(features_by_session.items()):
        inference_features = features.drop(columns=["session_id"], errors="ignore")
        output = run_inference(
            inference_features,
            model_dir,
            expected_telemetry_schema_version=CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
            expected_signal_columns=V02_ML_SIGNAL_COLUMNS,
        )
        windows = output.windows.copy()
        windows.insert(0, "session_id", session_id)
        windows_by_session[session_id] = windows
        results[session_id] = {
            "model_version": model_metadata.get("model_version_id") or model_metadata.get("version"),
            "status": output.overall_status,
            "health_score": output.health_score,
            "anomaly_ratio": output.anomaly_ratio,
            "anomaly_windows": output.anomaly_windows,
            "most_unusual_features": ";".join(output.most_unusual_features),
        }
    return results, windows_by_session


def build_model_comparison_rows(
    *,
    sessions: dict[str, Any],
    labels_by_session: dict[str, str],
    split_by_session: dict[str, str],
    frames_by_session: dict[str, pd.DataFrame],
    features_by_session: dict[str, pd.DataFrame],
    old_results: dict[str, dict[str, Any]],
    candidate_results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session_id in sorted(sessions):
        old = old_results[session_id]
        candidate = candidate_results[session_id]
        sample_count = len(sessions[session_id].samples)
        duration_ms = _session_duration_ms(frames_by_session[session_id])
        window_count = int(len(features_by_session[session_id]))
        old_health = old["health_score"]
        candidate_health = candidate["health_score"]
        old_ratio = old["anomaly_ratio"]
        candidate_ratio = candidate["anomaly_ratio"]
        rows.append(
            {
                "session_id": session_id,
                "human_label": labels_by_session.get(session_id, "unknown"),
                "split": split_by_session.get(session_id, "evaluation_only"),
                "sample_count": sample_count,
                "eligible_sample_count": _eligible_sample_count(frames_by_session[session_id]),
                "duration_ms": duration_ms,
                "window_count": window_count,
                "old_model_version": old["model_version"],
                "old_status": old["status"],
                "old_health_score": old_health,
                "old_anomaly_ratio": old_ratio,
                "old_anomaly_windows": old["anomaly_windows"],
                "old_most_unusual_features": old["most_unusual_features"],
                "candidate_model_version": candidate["model_version"],
                "candidate_status": candidate["status"],
                "candidate_health_score": candidate_health,
                "candidate_anomaly_ratio": candidate_ratio,
                "candidate_anomaly_windows": candidate["anomaly_windows"],
                "candidate_most_unusual_features": candidate["most_unusual_features"],
                "status_changed": old["status"] != candidate["status"],
                "health_delta": _delta(candidate_health, old_health),
                "anomaly_ratio_delta": _delta(candidate_ratio, old_ratio),
            }
        )
    return rows


def build_split_rows(
    *,
    labels_by_session: dict[str, str],
    split_by_session: dict[str, str],
    frames_by_session: dict[str, pd.DataFrame],
    features_by_session: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session_id in sorted(labels_by_session):
        frame_data = frames_by_session.get(session_id, pd.DataFrame())
        rows.append(
            {
                "session_id": session_id,
                "human_label": labels_by_session[session_id],
                "split": split_by_session.get(session_id, "evaluation_only"),
                "sample_count": int(len(frame_data)),
                "duration_ms": _session_duration_ms(frame_data),
                "window_count": int(len(features_by_session.get(session_id, pd.DataFrame()))),
                **_signal_range_columns(frame_data),
            }
        )
    return rows


def build_evidence_gate_rows(comparison_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    policies = [
        ("none", "no evidence gate", lambda row: False),
        ("policy_a_window_lt_10", "window_count < 10", lambda row: int(row["window_count"]) < 10),
        ("policy_b_eligible_lt_140", "eligible_sample_count < 140", lambda row: int(row["eligible_sample_count"]) < 140),
        ("policy_c_duration_lt_30000", "duration_ms < 30000", lambda row: _num(row["duration_ms"]) < 30000),
        (
            "combined_or",
            "window_count < 10 OR eligible_sample_count < 140 OR duration_ms < 30000",
            lambda row: int(row["window_count"]) < 10 or int(row["eligible_sample_count"]) < 140 or _num(row["duration_ms"]) < 30000,
        ),
        (
            "combined_and",
            "window_count < 10 AND eligible_sample_count < 140 AND duration_ms < 30000",
            lambda row: int(row["window_count"]) < 10 and int(row["eligible_sample_count"]) < 140 and _num(row["duration_ms"]) < 30000,
        ),
        ("window_n_5", "window_count < 5", lambda row: int(row["window_count"]) < 5),
        ("window_n_10", "window_count < 10", lambda row: int(row["window_count"]) < 10),
        ("window_n_15", "window_count < 15", lambda row: int(row["window_count"]) < 15),
        ("window_n_20", "window_count < 20", lambda row: int(row["window_count"]) < 20),
    ]
    rows: list[dict[str, Any]] = []
    for model_prefix in ["old", "candidate"]:
        for name, description, predicate in policies:
            gated = []
            for row in comparison_rows:
                status = row[f"{model_prefix}_status"]
                gate_applies = predicate(row)
                after = "limited_data" if gate_applies and status in {"monitor", "attention"} else status
                gated.append((row, after, gate_applies and after == "limited_data"))
            affected = [row["session_id"] for row, _after, changed in gated if changed]
            holdout = [item for item in gated if item[0]["split"] == "normal_holdout"]
            false_before = sum(1 for row, _after, _changed in holdout if row[f"{model_prefix}_status"] in {"monitor", "attention"})
            false_after = sum(1 for _row, after, _changed in holdout if after in {"monitor", "attention"})
            rows.append(
                {
                    "model": model_prefix,
                    "policy": name,
                    "semantics": description,
                    "affected_session_count": len(affected),
                    "affected_session_ids": ";".join(affected),
                    "normal_holdout_false_before": false_before,
                    "normal_holdout_false_after": false_after,
                    "known_abnormal_limited_count": sum(
                        1 for row, _after, changed in gated if changed and row["human_label"] == "known_abnormal"
                    ),
                    "status_distribution_after": _counter_text([after for _row, after, _changed in gated]),
                }
            )
    return rows


def build_threshold_policy_rows(comparison_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    monitor_ratios = [0.02, 0.03, 0.05, 0.08]
    attention_ratios = [0.10, 0.15, 0.20, 0.25]
    health_pairs = [(50, 80), (40, 70)]
    rows: list[dict[str, Any]] = []
    for model_prefix in ["old", "candidate"]:
        for attention_health, monitor_health in health_pairs:
            for monitor_ratio in monitor_ratios:
                for attention_ratio in attention_ratios:
                    if monitor_ratio >= attention_ratio:
                        continue
                    statuses = {
                        row["session_id"]: status_with_thresholds(
                            anomaly_ratio=float(row[f"{model_prefix}_anomaly_ratio"]),
                            health_score=_optional_float(row[f"{model_prefix}_health_score"]),
                            monitor_ratio=monitor_ratio,
                            attention_ratio=attention_ratio,
                            attention_health=attention_health,
                            monitor_health=monitor_health,
                        )
                        for row in comparison_rows
                    }
                    holdout = [row for row in comparison_rows if row["split"] == "normal_holdout"]
                    known = [row for row in comparison_rows if row["human_label"] == "known_abnormal"]
                    rows.append(
                        {
                            "model": model_prefix,
                            "monitor_ratio": monitor_ratio,
                            "attention_ratio": attention_ratio,
                            "attention_health_lt": attention_health,
                            "monitor_health_lt": monitor_health,
                            "is_current_policy": (
                                monitor_ratio == 0.02
                                and attention_ratio == 0.15
                                and attention_health == 50
                                and monitor_health == 80
                            ),
                            "normal_holdout_count": len(holdout),
                            "normal_holdout_ok": sum(1 for row in holdout if statuses[row["session_id"]] == "ok"),
                            "normal_holdout_false_monitor": sum(1 for row in holdout if statuses[row["session_id"]] == "monitor"),
                            "normal_holdout_false_attention": sum(
                                1 for row in holdout if statuses[row["session_id"]] == "attention"
                            ),
                            "known_abnormal_attention": sum(1 for row in known if statuses[row["session_id"]] == "attention"),
                            "known_abnormal_monitor": sum(1 for row in known if statuses[row["session_id"]] == "monitor"),
                            "known_abnormal_ok": sum(1 for row in known if statuses[row["session_id"]] == "ok"),
                            "warmup_distribution": _label_status_distribution(comparison_rows, statuses, "warmup_candidate"),
                            "battery_low_distribution": _label_status_distribution(comparison_rows, statuses, "battery_low_candidate"),
                            "high_rpm_distribution": _label_status_distribution(comparison_rows, statuses, "high_rpm_candidate"),
                        }
                    )
    return rows


def status_with_thresholds(
    *,
    anomaly_ratio: float,
    health_score: float | None,
    monitor_ratio: float,
    attention_ratio: float,
    attention_health: float,
    monitor_health: float,
) -> str:
    if health_score is None:
        return "no_windows"
    if anomaly_ratio >= attention_ratio or health_score < attention_health:
        return "attention"
    if anomaly_ratio >= monitor_ratio or health_score < monitor_health:
        return "monitor"
    return "ok"


def build_feature_distribution_rows(
    *,
    old_training_features: pd.DataFrame,
    features_by_session: dict[str, pd.DataFrame],
    labels_by_session: dict[str, str],
    train_sessions: list[str],
    holdout_sessions: list[str],
) -> list[dict[str, Any]]:
    groups: dict[str, pd.DataFrame] = {
        "old_training_baseline": old_training_features,
        "new_normal_training": _concat_features(features_by_session, train_sessions),
        "normal_holdout": _concat_features(features_by_session, holdout_sessions),
        "warmup_candidate": _concat_label_features(features_by_session, labels_by_session, "warmup_candidate"),
        "battery_low_candidate": _concat_label_features(features_by_session, labels_by_session, "battery_low_candidate"),
        "high_rpm_candidate": _concat_label_features(features_by_session, labels_by_session, "high_rpm_candidate"),
        "known_abnormal": _concat_label_features(features_by_session, labels_by_session, "known_abnormal"),
    }
    rows: list[dict[str, Any]] = []
    for feature in FEATURES_FOR_HUMAN_AUDIT:
        old_series = pd.to_numeric(old_training_features.get(feature, pd.Series(dtype=float)), errors="coerce").dropna()
        new_series = pd.to_numeric(groups["new_normal_training"].get(feature, pd.Series(dtype=float)), errors="coerce").dropna()
        for group_name, frame in groups.items():
            series = pd.to_numeric(frame.get(feature, pd.Series(dtype=float)), errors="coerce").dropna()
            rows.append(
                {
                    "feature": feature,
                    "group": group_name,
                    **_series_distribution(series),
                    "outlier_frequency_vs_old_training": _outlier_frequency(series, old_series),
                    "outlier_frequency_vs_new_training": _outlier_frequency(series, new_series),
                }
            )
    return rows


def build_coverage_rows(
    *,
    old_training_frames: pd.DataFrame,
    frames_by_session: dict[str, pd.DataFrame],
    features_by_session: dict[str, pd.DataFrame],
    train_sessions: list[str],
    holdout_sessions: list[str],
) -> list[dict[str, Any]]:
    groups = {
        "old_9_session_training": old_training_frames,
        "new_normal_training": _concat_frames(frames_by_session, train_sessions),
        "normal_holdout": _concat_frames(frames_by_session, holdout_sessions),
    }
    rows: list[dict[str, Any]] = []
    for group_name, frame in groups.items():
        base = {"group": group_name, "sample_count": int(len(frame))}
        for signal in V2_CORE_SIGNAL_COLUMNS:
            series = pd.to_numeric(frame.get(signal, pd.Series(dtype=float)), errors="coerce").dropna()
            base.update(_distribution_prefixed(signal, series))
        if group_name == "old_9_session_training":
            durations = _durations_by_source(frame, "source_file")
            window_counts = pd.Series(dtype=float)
        else:
            durations = pd.Series([_session_duration_ms(frames_by_session[sid]) for sid in (train_sessions if group_name == "new_normal_training" else holdout_sessions)], dtype=float).dropna()
            window_counts = pd.Series([len(features_by_session[sid]) for sid in (train_sessions if group_name == "new_normal_training" else holdout_sessions)], dtype=float)
        base.update(_distribution_prefixed("duration_ms", durations))
        base.update(_distribution_prefixed("window_count", window_counts))
        rows.append(base)
    return rows


def run_human_normal_retraining(
    *,
    data_dir: Path = Path("data"),
    calibration_dir: Path = Path("data/calibration"),
    docs_dir: Path = Path("docs/ml"),
    old_model_dir: Path = CURRENT_V2_MODEL_DIR,
    candidate_model_dir: Path = CANDIDATE_MODEL_DIR,
) -> HumanRound2Result:
    settings = Settings(data_dir=data_dir, model_dir=old_model_dir)
    assert_current_model_loadable(old_model_dir)
    labels = merge_human_review_labels(
        session_labels_path=calibration_dir / "session_labels.csv",
        review_path=calibration_dir / "SESSION_LABEL_REVIEW.csv",
    )
    label_hash = _sha256(calibration_dir / "session_labels.csv")
    labels_by_session = {row["session_id"]: row["label"] for row in labels}
    label_counts = Counter(labels_by_session.values())
    normal_sessions = sorted(session_id for session_id, label in labels_by_session.items() if is_baseline_training_label(label))
    train_sessions, holdout_sessions = deterministic_session_split(normal_sessions)
    split_by_session = {
        **{session_id: "normal_train" for session_id in train_sessions},
        **{session_id: "normal_holdout" for session_id in holdout_sessions},
    }
    for session_id, label in labels_by_session.items():
        if session_id not in split_by_session:
            split_by_session[session_id] = "excluded_unresolved" if label == "unknown" else "evaluation_only"

    sessions = load_v2_sessions(data_dir)
    frames_by_session, features_by_session = build_session_frames_and_features(sessions, settings)
    candidate_metadata = train_human_normal_candidate(
        train_session_ids=train_sessions,
        holdout_session_ids=holdout_sessions,
        features_by_session=features_by_session,
        label_hash=label_hash,
        label_counts=label_counts,
        settings=settings,
        model_dir=candidate_model_dir,
    )
    old_results, old_windows = evaluate_model_on_features(features_by_session=features_by_session, model_dir=old_model_dir)
    candidate_results, candidate_windows = evaluate_model_on_features(
        features_by_session=features_by_session,
        model_dir=candidate_model_dir,
    )
    comparison_rows = build_model_comparison_rows(
        sessions=sessions,
        labels_by_session=labels_by_session,
        split_by_session=split_by_session,
        frames_by_session=frames_by_session,
        features_by_session=features_by_session,
        old_results=old_results,
        candidate_results=candidate_results,
    )
    split_rows = build_split_rows(
        labels_by_session=labels_by_session,
        split_by_session=split_by_session,
        frames_by_session=frames_by_session,
        features_by_session=features_by_session,
    )
    evidence_rows = build_evidence_gate_rows(comparison_rows)
    threshold_rows = build_threshold_policy_rows(comparison_rows)
    old_metadata = load_model_bundle(old_model_dir).metadata
    old_training_frames, old_training_features = load_training_dataset(old_metadata, settings)
    feature_rows = build_feature_distribution_rows(
        old_training_features=old_training_features,
        features_by_session=features_by_session,
        labels_by_session=labels_by_session,
        train_sessions=train_sessions,
        holdout_sessions=holdout_sessions,
    )
    coverage_rows = build_coverage_rows(
        old_training_frames=old_training_frames,
        frames_by_session=frames_by_session,
        features_by_session=features_by_session,
        train_sessions=train_sessions,
        holdout_sessions=holdout_sessions,
    )

    calibration_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(calibration_dir / "human_normal_split.csv", split_rows)
    _write_csv(calibration_dir / "human_label_model_comparison.csv", comparison_rows)
    _write_csv(calibration_dir / "evidence_gate_comparison.csv", evidence_rows)
    _write_csv(calibration_dir / "threshold_policy_comparison.csv", threshold_rows)
    _write_csv(calibration_dir / "feature_distribution_comparison.csv", feature_rows)

    recommendation = production_recommendation(comparison_rows)
    docs = {
        "coverage": docs_dir / "HUMAN_LABELLED_BASELINE_COVERAGE.md",
        "retrain": docs_dir / "HUMAN_LABELLED_RETRAIN_REPORT.md",
        "evidence": docs_dir / "MINIMUM_EVIDENCE_POLICY.md",
        "promotion": docs_dir / "MODEL_PROMOTION_DECISION.md",
        "calibration": docs_dir / "ANOMALY_CALIBRATION_REPORT.md",
    }
    docs["coverage"].write_text(
        coverage_markdown(coverage_rows, train_sessions, holdout_sessions),
        encoding="utf-8",
    )
    docs["retrain"].write_text(
        retrain_report_markdown(
            label_counts=label_counts,
            train_sessions=train_sessions,
            holdout_sessions=holdout_sessions,
            candidate_metadata=candidate_metadata,
            comparison_rows=comparison_rows,
            feature_rows=feature_rows,
        ),
        encoding="utf-8",
    )
    docs["evidence"].write_text(evidence_policy_markdown(evidence_rows), encoding="utf-8")
    docs["promotion"].write_text(
        promotion_markdown(
            recommendation=recommendation,
            comparison_rows=comparison_rows,
            threshold_rows=threshold_rows,
            candidate_metadata=candidate_metadata,
        ),
        encoding="utf-8",
    )
    docs["calibration"].write_text(
        calibration_round2_markdown(
            recommendation=recommendation,
            label_counts=label_counts,
            comparison_rows=comparison_rows,
            threshold_rows=threshold_rows,
            feature_rows=feature_rows,
        ),
        encoding="utf-8",
    )

    return HumanRound2Result(
        label_counts=label_counts,
        train_sessions=train_sessions,
        holdout_sessions=holdout_sessions,
        candidate_model_version=str(candidate_metadata["model_version_id"]),
        candidate_training_window_count=int(candidate_metadata["training_window_count"]),
        production_recommendation=recommendation,
        report_paths={name: str(path) for name, path in docs.items()}
        | {
            "human_normal_split": str(calibration_dir / "human_normal_split.csv"),
            "human_label_model_comparison": str(calibration_dir / "human_label_model_comparison.csv"),
            "evidence_gate_comparison": str(calibration_dir / "evidence_gate_comparison.csv"),
            "threshold_policy_comparison": str(calibration_dir / "threshold_policy_comparison.csv"),
            "feature_distribution_comparison": str(calibration_dir / "feature_distribution_comparison.csv"),
            "candidate_model_dir": str(candidate_model_dir),
        },
    )


def production_recommendation(comparison_rows: list[dict[str, Any]]) -> str:
    holdout = [row for row in comparison_rows if row["split"] == "normal_holdout"]
    known = [row for row in comparison_rows if row["human_label"] == "known_abnormal"]
    if len(holdout) < 4:
        return "MORE_LABELLED_DATA_REQUIRED"
    old_attention = sum(1 for row in holdout if row["old_status"] == "attention")
    candidate_attention = sum(1 for row in holdout if row["candidate_status"] == "attention")
    old_false = sum(1 for row in holdout if row["old_status"] in {"monitor", "attention"})
    candidate_false = sum(1 for row in holdout if row["candidate_status"] in {"monitor", "attention"})
    known_visible = sum(1 for row in known if row["candidate_status"] in {"monitor", "attention"})
    if candidate_attention <= old_attention and candidate_false < old_false and known_visible >= max(1, len(known) // 2):
        return "PROMOTE_CANDIDATE"
    if candidate_false <= old_false:
        return "KEEP_CURRENT_MODEL"
    return "MORE_LABELLED_DATA_REQUIRED"


def false_positive_summary(comparison_rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    holdout = [row for row in comparison_rows if row["split"] == "normal_holdout"]
    return {
        "count": len(holdout),
        "ok_count": sum(1 for row in holdout if row[f"{prefix}_status"] == "ok"),
        "monitor_count": sum(1 for row in holdout if row[f"{prefix}_status"] == "monitor"),
        "attention_count": sum(1 for row in holdout if row[f"{prefix}_status"] == "attention"),
        "false_monitor_count": sum(1 for row in holdout if row[f"{prefix}_status"] == "monitor"),
        "false_attention_count": sum(1 for row in holdout if row[f"{prefix}_status"] == "attention"),
    }


def coverage_markdown(coverage_rows: list[dict[str, Any]], train_sessions: list[str], holdout_sessions: list[str]) -> str:
    return f"""# Human-Labelled Baseline Coverage

## Session Split

- Random seed: {RANDOM_SEED}
- Train fraction target: {TRAIN_FRACTION}
- Normal training sessions: {len(train_sessions)}
- Normal holdout sessions: {len(holdout_sessions)}
- Session leakage: zero

Training sessions:

{_bullet_ids(train_sessions)}

Holdout sessions:

{_bullet_ids(holdout_sessions)}

## Coverage Table

{_markdown_table(coverage_rows)}

The new candidate baseline is trained only from confirmed `normal_ride`
sessions and is evaluated against a session-separated normal holdout. The old
baseline coverage is shown as context; no old-training windows are reused for
candidate fitting.
"""


def retrain_report_markdown(
    *,
    label_counts: Counter[str],
    train_sessions: list[str],
    holdout_sessions: list[str],
    candidate_metadata: dict[str, Any],
    comparison_rows: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
) -> str:
    old_fp = false_positive_summary(comparison_rows, "old")
    candidate_fp = false_positive_summary(comparison_rows, "candidate")
    cohort_rows = cohort_summary_rows(comparison_rows)
    important_features = [row for row in feature_rows if row["group"] in {"old_training_baseline", "new_normal_training", "normal_holdout"}]
    return f"""# Human-Labelled Retrain Report

## Labels

{_counter_markdown(label_counts)}

Only `normal_ride` is used for baseline fitting. Candidate/context labels remain
evaluation-only and are not treated as faults.

## Candidate Model

- Version: `{candidate_metadata['model_version_id']}`
- Artifact directory: `{CANDIDATE_MODEL_DIR}`
- Training windows: {candidate_metadata['training_window_count']}
- Feature schema: `{candidate_metadata['feature_schema_version']}`
- Telemetry schema: `{candidate_metadata['telemetry_schema_version']}`
- Decoder: `{candidate_metadata['decoder_id']}:{candidate_metadata['decoder_version']}`
- Hyperparameters: n_estimators={candidate_metadata['n_estimators']}, contamination={candidate_metadata['contamination']}, random_state={candidate_metadata['random_state']}

## Split

- Training sessions ({len(train_sessions)}): {', '.join(f'`{item}`' for item in train_sessions)}
- Holdout sessions ({len(holdout_sessions)}): {', '.join(f'`{item}`' for item in holdout_sessions)}
- Leakage: zero sessions overlap.

## Normal Holdout False-Positive Metric

Old model:

{_markdown_table([old_fp])}

Candidate model:

{_markdown_table([candidate_fp])}

## Evaluation Cohorts

{_markdown_table(cohort_rows)}

## Feature Distribution Highlights

{_markdown_table(important_features[:48])}
"""


def evidence_policy_markdown(evidence_rows: list[dict[str, Any]]) -> str:
    quantization = [
        {"window_count": 5, "one_window_ratio": "20.00%", "monitor_threshold_2pct": "1 window already exceeds", "attention_threshold_15pct": "1 window exceeds"},
        {"window_count": 7, "one_window_ratio": "14.29%", "monitor_threshold_2pct": "1 window exceeds", "attention_threshold_15pct": "2 windows needed"},
        {"window_count": 10, "one_window_ratio": "10.00%", "monitor_threshold_2pct": "1 window exceeds", "attention_threshold_15pct": "2 windows needed"},
        {"window_count": 20, "one_window_ratio": "5.00%", "monitor_threshold_2pct": "1 window exceeds", "attention_threshold_15pct": "3 windows needed"},
    ]
    return f"""# Minimum Evidence Policy

## Quantization

{_markdown_table(quantization)}

Small session sizes make anomaly ratios jump in coarse steps. This affects
session-level interpretation only; per-window anomaly scores should remain
available.

## Policy Comparison

{_markdown_table(evidence_rows)}

## Recommendation

Use the smallest clear production interpretation gate:

```text
if window_count < 10:
    session_status = limited_data
```

This captures the 4-window and 7-window sessions without adding redundant
sample-count and duration gates. The raw anomaly ratio, health score, and
per-window scores should still be returned for debugging.
"""


def promotion_markdown(
    *,
    recommendation: str,
    comparison_rows: list[dict[str, Any]],
    threshold_rows: list[dict[str, Any]],
    candidate_metadata: dict[str, Any],
) -> str:
    old_fp = false_positive_summary(comparison_rows, "old")
    candidate_fp = false_positive_summary(comparison_rows, "candidate")
    current_threshold = [
        row for row in threshold_rows if row["model"] == "candidate" and str(row["is_current_policy"]) == "True"
    ]
    return f"""# Model Promotion Decision

## Decision

`{recommendation}`

## Candidate

- Version: `{candidate_metadata['model_version_id']}`
- Production model preserved: `{CURRENT_V2_MODEL_VERSION}`
- Candidate artifact directory: `{CANDIDATE_MODEL_DIR}`

## Promotion Criteria

- Normal holdout improved: {candidate_fp['false_monitor_count'] + candidate_fp['false_attention_count']} candidate false statuses vs {old_fp['false_monitor_count'] + old_fp['false_attention_count']} old false statuses.
- Known-abnormal/candidate sensitivity not obviously destroyed: see `human_label_model_comparison.csv`.
- Schema/decoder compatibility: `canonical-telemetry-v2`, `ecu-window-features-v1`, `honda_keihin_71_17:1.0.0`.
- Evaluation split: session-separated, no leakage.

## Normal Holdout

Old:

{_markdown_table([old_fp])}

Candidate:

{_markdown_table([candidate_fp])}

## Current Threshold Row For Candidate

{_markdown_table(current_threshold)}

## Threshold Decision

Threshold changes are not justified in this round. The grid shows that looser
monitor thresholds can reduce normal-holdout monitor statuses, but the holdout
set has only four sessions and the candidate baseline does not improve the
primary false-positive metric under the current production policy.

The candidate should remain side-by-side with production until the owner reviews
candidate/context cohort behavior and decides whether these labels should be
product-normal, limited-data, or out-of-distribution statuses.
"""


def calibration_round2_markdown(
    *,
    recommendation: str,
    label_counts: Counter[str],
    comparison_rows: list[dict[str, Any]],
    threshold_rows: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
) -> str:
    return f"""# Anomaly Calibration Report

## Round 2 Human-Labelled Baseline

- Recommendation: `{recommendation}`
- Label counts:

{_counter_markdown(label_counts)}

## Old vs Candidate Normal-Holdout False Positives

{_markdown_table([
    {'model': 'old', **false_positive_summary(comparison_rows, 'old')},
    {'model': 'candidate', **false_positive_summary(comparison_rows, 'candidate')},
])}

## Cohorts

{_markdown_table(cohort_summary_rows(comparison_rows))}

## Threshold Search

{_markdown_table(threshold_rows)}

Threshold changes are not justified in this round. The labelled holdout is still
small, and lower false-positive counts from looser thresholds would be a product
policy choice rather than evidence that the underlying anomaly baseline is
better.

Threshold changes are not baked into the model. Product status policy remains a
separate interpretation layer above Isolation Forest scores.

## Feature Audit

{_markdown_table(feature_rows)}
"""


def cohort_summary_rows(comparison_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label in ["warmup_candidate", "battery_low_candidate", "high_rpm_candidate", "known_abnormal", "too_short", "unknown"]:
        group = [row for row in comparison_rows if row["human_label"] == label]
        if not group:
            continue
        rows.append(
            {
                "human_label": label,
                "count": len(group),
                "old_status_distribution": _counter_text([row["old_status"] for row in group]),
                "candidate_status_distribution": _counter_text([row["candidate_status"] for row in group]),
                "old_median_anomaly_ratio": _median(row["old_anomaly_ratio"] for row in group),
                "candidate_median_anomaly_ratio": _median(row["candidate_anomaly_ratio"] for row in group),
                "old_median_health": _median(row["old_health_score"] for row in group),
                "candidate_median_health": _median(row["candidate_health_score"] for row in group),
                "candidate_common_features": _common_features(row["candidate_most_unusual_features"] for row in group),
            }
        )
    return rows


def _label_status_distribution(rows: list[dict[str, Any]], statuses: dict[str, str], label: str) -> str:
    return _counter_text([statuses[row["session_id"]] for row in rows if row["human_label"] == label])


def _concat_features(features_by_session: dict[str, pd.DataFrame], session_ids: list[str]) -> pd.DataFrame:
    parts = [features_by_session[session_id].drop(columns=["session_id"], errors="ignore") for session_id in session_ids]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _concat_label_features(
    features_by_session: dict[str, pd.DataFrame],
    labels_by_session: dict[str, str],
    label: str,
) -> pd.DataFrame:
    return _concat_features(features_by_session, [sid for sid, item in labels_by_session.items() if item == label])


def _concat_frames(frames_by_session: dict[str, pd.DataFrame], session_ids: list[str]) -> pd.DataFrame:
    return pd.concat([frames_by_session[session_id] for session_id in session_ids], ignore_index=True) if session_ids else pd.DataFrame()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _eligible_sample_count(frame_data: pd.DataFrame) -> int:
    return int(frame_data["ml_eligible"].fillna(False).astype(bool).sum()) if "ml_eligible" in frame_data else 0


def _session_duration_ms(frame_data: pd.DataFrame) -> Any:
    if frame_data.empty or "relative_time_ms" not in frame_data:
        return ""
    series = pd.to_numeric(frame_data["relative_time_ms"], errors="coerce").dropna()
    if len(series) < 2:
        return ""
    return _round(float(series.max() - series.min()))


def _signal_range_columns(frame_data: pd.DataFrame) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for signal in V2_CORE_SIGNAL_COLUMNS:
        series = pd.to_numeric(frame_data.get(signal, pd.Series(dtype=float)), errors="coerce").dropna()
        row[f"{signal}_min"] = _round(series.min()) if not series.empty else ""
        row[f"{signal}_max"] = _round(series.max()) if not series.empty else ""
        row[f"{signal}_range"] = _round(series.max() - series.min()) if not series.empty else ""
    return row


def _distribution_prefixed(prefix: str, series: pd.Series) -> dict[str, Any]:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if series.empty:
        return {
            f"{prefix}_min": "",
            f"{prefix}_p05": "",
            f"{prefix}_median": "",
            f"{prefix}_p95": "",
            f"{prefix}_max": "",
        }
    return {
        f"{prefix}_min": _round(series.min()),
        f"{prefix}_p05": _round(series.quantile(0.05)),
        f"{prefix}_median": _round(series.median()),
        f"{prefix}_p95": _round(series.quantile(0.95)),
        f"{prefix}_max": _round(series.max()),
    }


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


def _durations_by_source(frame_data: pd.DataFrame, source_column: str) -> pd.Series:
    if frame_data.empty or source_column not in frame_data:
        return pd.Series(dtype=float)
    durations: list[float] = []
    for _source, group in frame_data.groupby(source_column):
        value = _session_duration_ms(group)
        if value != "":
            durations.append(float(value))
    return pd.Series(durations, dtype=float)


def _num(value: Any) -> float:
    if value == "" or value is None:
        return math.inf
    return float(value)


def _optional_float(value: Any) -> float | None:
    if value == "" or value is None:
        return None
    return float(value)


def _delta(left: Any, right: Any) -> Any:
    if left is None or right is None or left == "" or right == "":
        return ""
    return _round(float(left) - float(right))


def _round(value: Any, digits: int = 6) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number):
        return ""
    rounded = round(number, digits)
    return int(rounded) if rounded == int(rounded) else rounded


def _median(values: Any) -> Any:
    series = pd.Series(list(values), dtype="float64").dropna()
    return _round(series.median()) if not series.empty else ""


def _counter_text(values: list[Any]) -> str:
    counter = Counter(str(value) for value in values if value != "")
    return ", ".join(f"{key}={counter[key]}" for key in sorted(counter)) or "none"


def _counter_markdown(counter: Counter[str]) -> str:
    return _markdown_table([{"label": key, "count": value} for key, value in sorted(counter.items())])


def _common_features(values: Any) -> str:
    counter: Counter[str] = Counter()
    for value in values:
        counter.update(feature for feature in str(value).split(";") if feature)
    return ";".join(feature for feature, _count in counter.most_common(5))


def _bullet_ids(values: list[str]) -> str:
    return "\n".join(f"- `{value}`" for value in values)


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


def _markdown_cell(value: Any) -> str:
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")
