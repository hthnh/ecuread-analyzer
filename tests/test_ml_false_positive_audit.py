from __future__ import annotations

from pathlib import Path

from app.audit.ml_false_positive_audit import (
    KNOWN_ABNORMAL_V2_SESSIONS,
    assert_current_model_loadable,
    build_session_labels,
    can_train_candidate,
    evidence_policy_status,
    status_from_scores,
)


def test_short_session_evidence_gate_returns_limited_data() -> None:
    status, reason = evidence_policy_status(
        overall_status="attention",
        window_count=7,
        duration_ms=29_000,
        eligible_samples=117,
    )

    assert status == "limited_data"
    assert "window_count<10" in reason


def test_status_mapping_matches_current_runtime_thresholds() -> None:
    assert status_from_scores(0.15, 100.0) == "attention"
    assert status_from_scores(0.01, 49.99) == "attention"
    assert status_from_scores(0.02, 100.0) == "monitor"
    assert status_from_scores(0.0, 79.99) == "monitor"
    assert status_from_scores(0.0, 80.0) == "ok"


def test_unknown_sessions_are_not_labelled_normal_from_scores() -> None:
    rows = [
        {
            "session_id": "ride-unlabelled-ok-looking",
            "window_count": 50,
            "overall_status": "ok",
            "anomaly_ratio": 0.0,
        }
    ]

    labels = build_session_labels(rows)

    assert labels[0]["label"] == "unknown"


def test_known_abnormal_labels_use_explicit_fixture_ids() -> None:
    session_id = next(iter(KNOWN_ABNORMAL_V2_SESSIONS))
    labels = build_session_labels([{"session_id": session_id, "window_count": 100}])

    assert labels[0]["label"] == "known_abnormal"
    assert labels[0]["label_source"] == "explicit_demo_seed_generation"


def test_candidate_training_requires_confirmed_normal_labels() -> None:
    can_train, reason = can_train_candidate(
        [
            {"session_id": "a", "label": "unknown"},
            {"session_id": "b", "label": "known_abnormal"},
            {"session_id": "c", "label": "too_short"},
        ]
    )

    assert can_train is False
    assert "insufficient confirmed normal_ride" in reason


def test_current_v2_model_remains_loadable_when_artifacts_exist() -> None:
    model_dir = Path("data/models/honda_keihin_71_17_v2")
    if not model_dir.exists():
        return

    metadata = assert_current_model_loadable(model_dir)

    assert metadata["model_version_id"] == "iforest-baseline-20260920T091709Z"
