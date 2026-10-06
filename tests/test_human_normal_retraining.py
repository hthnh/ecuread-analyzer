from __future__ import annotations

from collections import Counter
from pathlib import Path

from app.audit.human_normal_retraining import (
    CANDIDATE_MODEL_DIR,
    build_evidence_gate_rows,
    build_model_comparison_rows,
    candidate_model_version_id,
    deterministic_session_split,
    is_baseline_training_label,
    is_evaluation_only_label,
)
from app.ml.inference import load_model_bundle


def test_label_eligibility_is_explicit() -> None:
    assert is_baseline_training_label("normal_ride") is True
    assert is_baseline_training_label("battery_low_candidate") is False
    assert is_baseline_training_label("warmup_candidate") is False
    assert is_baseline_training_label("high_rpm_candidate") is False
    assert is_baseline_training_label("unknown") is False
    assert is_baseline_training_label("") is False

    assert is_evaluation_only_label("battery_low_candidate") is True
    assert is_evaluation_only_label("warmup_candidate") is True
    assert is_evaluation_only_label("high_rpm_candidate") is True
    assert is_evaluation_only_label("unknown") is True
    assert is_evaluation_only_label("") is True


def test_session_split_is_deterministic_and_has_no_leakage() -> None:
    sessions = [f"ride-{index:02d}" for index in range(16)]

    train_a, holdout_a = deterministic_session_split(sessions)
    train_b, holdout_b = deterministic_session_split(sessions)

    assert train_a == train_b
    assert holdout_a == holdout_b
    assert len(train_a) == 12
    assert len(holdout_a) == 4
    assert set(train_a).isdisjoint(holdout_a)


def test_candidate_model_version_depends_on_label_and_split_hashes() -> None:
    version = candidate_model_version_id("a" * 64, ["train-a", "train-b"], ["holdout-a"])

    assert version.startswith("iforest-human-normal-aaaaaaaa-")


def test_evidence_gate_window_boundary() -> None:
    rows = [
        {
            "session_id": "short",
            "split": "normal_holdout",
            "human_label": "normal_ride",
            "window_count": 9,
            "eligible_sample_count": 139,
            "duration_ms": 29_000,
            "old_status": "attention",
            "candidate_status": "monitor",
        },
        {
            "session_id": "long",
            "split": "normal_holdout",
            "human_label": "normal_ride",
            "window_count": 10,
            "eligible_sample_count": 140,
            "duration_ms": 30_000,
            "old_status": "monitor",
            "candidate_status": "ok",
        },
    ]

    gate_rows = build_evidence_gate_rows(rows)
    candidate_window_10 = next(
        row for row in gate_rows if row["model"] == "candidate" and row["policy"] == "policy_a_window_lt_10"
    )

    assert candidate_window_10["affected_session_count"] == 1
    assert candidate_window_10["normal_holdout_false_after"] == 0


def test_model_comparison_row_deltas() -> None:
    rows = build_model_comparison_rows(
        sessions={"s": type("Session", (), {"samples": [1, 2, 3]})()},
        labels_by_session={"s": "normal_ride"},
        split_by_session={"s": "normal_holdout"},
        frames_by_session={"s": __import__("pandas").DataFrame({"ml_eligible": [True], "relative_time_ms": [0]})},
        features_by_session={"s": __import__("pandas").DataFrame({"window_index": [0]})},
        old_results={
            "s": {
                "model_version": "old",
                "status": "monitor",
                "health_score": 70.0,
                "anomaly_ratio": 0.05,
                "anomaly_windows": 1,
                "most_unusual_features": "ect_c_delta",
            }
        },
        candidate_results={
            "s": {
                "model_version": "candidate",
                "status": "ok",
                "health_score": 90.0,
                "anomaly_ratio": 0.0,
                "anomaly_windows": 0,
                "most_unusual_features": "",
            }
        },
    )

    assert rows[0]["status_changed"] is True
    assert rows[0]["health_delta"] == 20
    assert rows[0]["anomaly_ratio_delta"] == -0.05


def test_candidate_artifacts_are_loadable_when_present() -> None:
    if not CANDIDATE_MODEL_DIR.exists():
        return

    metadata = load_model_bundle(CANDIDATE_MODEL_DIR).metadata

    assert metadata["status"] == "candidate"
    assert metadata["training_session_ids"]
    assert metadata["normal_holdout_session_ids"]
    assert metadata["human_label_count"]["normal_ride"] >= 1
