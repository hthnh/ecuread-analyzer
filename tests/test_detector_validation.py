from __future__ import annotations

from app.evaluation.detector_validation import (
    CONTROLLED_LABEL_SOURCE,
    build_validation_metrics,
    controlled_condition_type,
    detector_controlled_metrics,
    event_category,
    independent_label,
    target_relevant_detectors,
)
from app.ml.contextual_detector import CONTEXTUAL_BATTERY_DETECTOR_ID
from app.ml.iforest_detector import ISOLATION_FOREST_DETECTOR_ID


def test_independent_labels_never_come_from_detector_output() -> None:
    controlled = independent_label(
        {
            "label": "known_abnormal",
            "label_source": CONTROLLED_LABEL_SOURCE,
            "notes": "synthetically injected low battery session from demo seed generation",
        }
    )
    assert controlled["label"] == "controlled_condition"
    assert controlled["controlled_condition_type"] == "synthetic_low_battery"
    assert controlled["suitable_for_controlled_metrics"] is True
    assert controlled["assigned_from_detector_output"] is False

    normal = independent_label({"label": "normal_ride", "label_source": "human_review"})
    assert normal["label"] == "observed_normal_behavior"
    assert normal["expert_only"] is True
    assert normal["suitable_for_controlled_metrics"] is False

    unusual = independent_label({"label": "battery_low_candidate", "label_source": "human_review"})
    assert unusual["label"] == "observed_unusual_behavior"
    assert unusual["expert_only"] is True

    inconclusive = independent_label({"label": None, "label_source": None})
    assert inconclusive["label"] == "inconclusive"
    assert inconclusive["assigned_from_detector_output"] is False


def test_controlled_conditions_map_only_to_target_relevant_detectors() -> None:
    assert controlled_condition_type("synthetically injected high RPM/load session") == "synthetic_high_rpm_load"
    assert controlled_condition_type("synthetically injected high ECT session") == "synthetic_high_ect"
    assert target_relevant_detectors("synthetic_low_battery") == [
        ISOLATION_FOREST_DETECTOR_ID,
        CONTEXTUAL_BATTERY_DETECTOR_ID,
    ]
    assert target_relevant_detectors("synthetic_high_rpm_load") == [ISOLATION_FOREST_DETECTOR_ID]
    assert target_relevant_detectors("synthetic_high_ect") == [ISOLATION_FOREST_DETECTOR_ID]


def test_event_category_preserves_no_evidence_and_coverage_gap_semantics() -> None:
    assert (
        event_category(
            {
                "event_type": "coverage_gap_region",
                "coverage_status": "partial",
                "positive_detector_ids": [ISOLATION_FOREST_DETECTOR_ID],
            }
        )
        == "insufficient_coverage_region"
    )
    assert (
        event_category(
            {
                "event_type": "detector_specific_event",
                "coverage_status": "partial",
                "positive_detector_ids": [ISOLATION_FOREST_DETECTOR_ID],
            }
        )
        == "isolation_forest_only_event"
    )
    assert event_category({"event_type": "no_evidence_region", "positive_detector_ids": []}) == "no_evidence_region"
    assert event_category({"positive_detector_ids": [ISOLATION_FOREST_DETECTOR_ID]}) == "isolation_forest_only_event"
    assert event_category({"positive_detector_ids": [CONTEXTUAL_BATTERY_DETECTOR_ID]}) == "contextual_only_event"
    assert (
        event_category({"positive_detector_ids": [ISOLATION_FOREST_DETECTOR_ID, CONTEXTUAL_BATTERY_DETECTOR_ID]})
        == "overlapping_event"
    )


def test_detector_metrics_ignore_non_target_controlled_conditions() -> None:
    controlled_cases = [
        {
            "session_id": "low-battery",
            "target_relevant_detectors": [ISOLATION_FOREST_DETECTOR_ID, CONTEXTUAL_BATTERY_DETECTOR_ID],
            "responses": {
                ISOLATION_FOREST_DETECTOR_ID: {"detected": True, "detection_latency_windows": 0},
                CONTEXTUAL_BATTERY_DETECTOR_ID: {"detected": True, "detection_latency_windows": 2},
            },
        },
        {
            "session_id": "high-rpm",
            "target_relevant_detectors": [ISOLATION_FOREST_DETECTOR_ID],
            "responses": {
                ISOLATION_FOREST_DETECTOR_ID: {"detected": True, "detection_latency_windows": 0},
                CONTEXTUAL_BATTERY_DETECTOR_ID: {"detected": False, "detection_latency_windows": None},
            },
        },
    ]

    if_metrics = detector_controlled_metrics(ISOLATION_FOREST_DETECTOR_ID, controlled_cases)
    contextual_metrics = detector_controlled_metrics(CONTEXTUAL_BATTERY_DETECTOR_ID, controlled_cases)

    assert if_metrics["controlled_condition_recall"]["eligible_case_count"] == 2
    assert if_metrics["controlled_condition_recall"]["value"] == 1.0
    assert contextual_metrics["controlled_condition_recall"]["eligible_case_count"] == 1
    assert contextual_metrics["controlled_condition_recall"]["value"] == 1.0
    assert contextual_metrics["controlled_condition_recall"]["eligible_sessions"] == ["low-battery"]


def test_build_validation_metrics_keeps_unlabeled_data_out_of_precision_recall() -> None:
    h5_benchmark = {
        "sessions": [
            {
                "session_id": "low-battery",
                "training_evaluation_split": {
                    "label": "known_abnormal",
                    "label_source": CONTROLLED_LABEL_SOURCE,
                    "notes": "synthetically injected low battery session from demo seed generation",
                },
                "detectors": [
                    {
                        "detector_id": ISOLATION_FOREST_DETECTOR_ID,
                        "status": "ok",
                        "events": {"items": [{"start_window_index": 0}]},
                    },
                    {
                        "detector_id": CONTEXTUAL_BATTERY_DETECTOR_ID,
                        "status": "ok",
                        "events": {"items": [{"start_window_index": 1}]},
                    },
                ],
            },
            {
                "session_id": "unlabeled",
                "training_evaluation_split": {"label": None, "label_source": None},
                "detectors": [],
            },
        ]
    }
    validation_manifest = {
        "items": [
            {"independent_label": {"expert_only": True, "label": "observed_normal_behavior"}},
            {"independent_label": {"expert_only": False, "label": "inconclusive"}},
        ]
    }

    metrics = build_validation_metrics(h5_benchmark, validation_manifest)

    assert len(metrics["controlled_cases"]) == 1
    assert metrics["metric_policy"]["unlabeled_events_treated_as_normal"] is False
    assert metrics["detectors"][CONTEXTUAL_BATTERY_DETECTOR_ID]["event_level_precision"]["computed"] is False
