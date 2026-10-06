from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.evaluation.model_harness_evaluation import (
    anomaly_events,
    default_paths,
    detector_disagreements,
    identify_pre_h1_source,
    label_metrics_policy,
)
from app.ml.harness import DetectorIdentity, DetectorResult


def test_anomaly_events_group_consecutive_windows_and_use_frame_time() -> None:
    windows = pd.DataFrame(
        [
            {"window_index": 0, "start_frame_index": 0, "end_frame_index": 4, "is_anomaly": False},
            {"window_index": 1, "start_frame_index": 5, "end_frame_index": 9, "is_anomaly": True},
            {"window_index": 2, "start_frame_index": 10, "end_frame_index": 14, "is_anomaly": True},
            {"window_index": 3, "start_frame_index": 15, "end_frame_index": 19, "is_anomaly": False},
            {"window_index": 4, "start_frame_index": 20, "end_frame_index": 24, "is_anomaly": True},
        ]
    )
    frame_data = pd.DataFrame(
        {
            "frame_index": list(range(25)),
            "relative_time_ms": [index * 100.0 for index in range(25)],
        }
    )

    events = anomaly_events(windows, frame_data=frame_data)

    assert events == [
        {
            "start_window_index": 1,
            "end_window_index": 2,
            "start_sequence": 5,
            "end_sequence": 14,
            "window_count": 2,
            "duration_ms": 900.0,
        },
        {
            "start_window_index": 4,
            "end_window_index": 4,
            "start_sequence": 20,
            "end_sequence": 24,
            "window_count": 1,
            "duration_ms": 400.0,
        },
    ]


def test_anomaly_events_ignore_unavailable_window_flags() -> None:
    windows = pd.DataFrame(
        [
            {"window_index": 0, "start_frame_index": 0, "end_frame_index": 4, "is_anomaly": None},
            {"window_index": 1, "start_frame_index": 5, "end_frame_index": 9, "is_anomaly": True},
            {"window_index": 2, "start_frame_index": 10, "end_frame_index": 14, "is_anomaly": float("nan")},
        ]
    )

    events = anomaly_events(windows)

    assert len(events) == 1
    assert events[0]["start_window_index"] == 1


def test_detector_disagreements_do_not_compare_unavailable_windows_as_normal() -> None:
    left = DetectorResult(
        identity=DetectorIdentity("left", "left", "1"),
        status="ok",
        required_features=(),
        windows=pd.DataFrame({"is_anomaly": [False, True, None, True]}),
        is_anomaly_column="is_anomaly",
    )
    right = DetectorResult(
        identity=DetectorIdentity("right", "right", "1"),
        status="ok",
        required_features=(),
        windows=pd.DataFrame({"shadow_is_anomaly": [False, False, True, None]}),
        is_anomaly_column="shadow_is_anomaly",
    )

    disagreements = detector_disagreements([left, right])

    pair = disagreements["pairs"][0]
    assert pair["compared_windows"] == 2
    assert pair["unavailable_windows"] == 2
    assert pair["window_disagreement_count"] == 1


def test_label_metrics_are_not_computed_for_context_labels() -> None:
    manifest = {
        "sessions": [
            {
                "training_evaluation_split": {
                    "label": "normal_ride",
                    "suitable_for_precision_recall_f1": False,
                }
            },
            {
                "training_evaluation_split": {
                    "label": "battery_low_candidate",
                    "suitable_for_precision_recall_f1": False,
                }
            },
        ]
    }

    policy = label_metrics_policy(manifest)

    assert policy["computed"] is False
    assert "no verified binary normal/fault labels" in policy["reason"]


def test_pre_h1_source_is_identified_from_handoff_tarball() -> None:
    paths = default_paths(repo_root=Path(__file__).resolve().parents[1])

    source = identify_pre_h1_source(paths)

    assert source["available"] is True
    assert source["source"] == "pi_analyzer_handoff.tar.gz"
    assert source["legacy_inference_sha256"] == "1c33c0a4f78ab520cbdc8256f6abfdbd70c1f06c0178e3c4678f17325f310ff7"
