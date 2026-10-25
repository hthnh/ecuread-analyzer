from __future__ import annotations

import json

from app.ml.contextual_detector import (
    CONTEXTUAL_BATTERY_DETECTOR_ID,
    CONTEXTUAL_BATTERY_MODEL_FAMILY,
    CONTEXTUAL_BATTERY_MODEL_NAME,
    CONTEXTUAL_REQUIRED_FEATURES,
)
from app.services.analysis_service import AnalysisService

from tests.test_limited_data_gate import make_v2_session, write_fixed_model


def write_contextual_reference(settings, *, median: float = 14.5, threshold: float = 4.5) -> None:
    path = settings.data_dir / "evaluation" / "h3" / "contextual_reference.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    feature_stats = {
        "count": 80,
        "median": median,
        "mad": 0.0,
        "iqr": 0.0,
        "robust_scale": 0.35,
        "scale_floor": 0.35,
        "p10": median,
        "p90": median,
    }
    payload = {
        "schema_version": "contextual-battery-reference-v1",
        "detector_id": CONTEXTUAL_BATTERY_DETECTOR_ID,
        "model_name": CONTEXTUAL_BATTERY_MODEL_NAME,
        "model_family": CONTEXTUAL_BATTERY_MODEL_FAMILY,
        "model_version": "ctx-test-reference",
        "configuration": {
            "required_features": list(CONTEXTUAL_REQUIRED_FEATURES),
            "robust_z_threshold": threshold,
        },
        "contexts": {
            "idle_closed_throttle": {
                "window_count": 80,
                "reference_session_count": 1,
                "reference_session_ids": ["reference"],
                "features": {
                    "battery_voltage_median": feature_stats,
                    "battery_voltage_mean": feature_stats,
                    "battery_voltage_min": feature_stats,
                },
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def battery_session(voltage: float, *, session_id: str = "live-research-session"):
    session = make_v2_session(80, session_id=session_id)
    samples = [sample.model_copy(update={"battery_voltage": voltage}) for sample in session.samples]
    return session.model_copy(update={"samples": samples})


def test_live_research_harness_executes_contextual_detector(settings) -> None:
    write_fixed_model(settings.model_dir, anomaly_count=0)
    write_contextual_reference(settings)

    output = AnalysisService(settings, model_dir=settings.model_dir).analyze(
        battery_session(14.5),
        process_with_model=True,
        persist=False,
    )

    evidence = output.summary["diagnostic_evidence"]
    assert output.summary["overall_status"] == "ok"
    assert evidence["production_boundary"]["production_anomaly_status_changed"] is False
    assert evidence["detectors"]["isolation_forest"]["finding"] == "negative"
    assert evidence["detectors"]["contextual_battery_voltage"]["execution_status"] == "ok"
    assert evidence["detectors"]["contextual_battery_voltage"]["finding"] == "negative"
    assert evidence["aggregate"]["evidence_state"] == "no_evidence"
    assert evidence["interpretation"]["status"] == "available"


def test_live_research_harness_preserves_detector_disagreement(settings) -> None:
    write_fixed_model(settings.model_dir, anomaly_count=0)
    write_contextual_reference(settings)

    output = AnalysisService(settings, model_dir=settings.model_dir).analyze(
        battery_session(11.0, session_id="live-contextual-anomaly"),
        process_with_model=True,
        persist=False,
    )

    evidence = output.summary["diagnostic_evidence"]
    assert output.summary["overall_status"] == "ok"
    assert output.summary["anomaly_window_count"] == 0
    assert evidence["detectors"]["isolation_forest"]["finding"] == "negative"
    assert evidence["detectors"]["contextual_battery_voltage"]["finding"] == "positive"
    assert evidence["aggregate"]["evidence_state"] == "detector_disagreement"
    assert evidence["aggregate"]["detector_disagreement"]["disagreement_window_count"] > 0


def test_contextual_missing_reference_is_visible_skip_not_normal(settings) -> None:
    write_fixed_model(settings.model_dir, anomaly_count=0)

    output = AnalysisService(settings, model_dir=settings.model_dir).analyze(
        battery_session(14.5, session_id="live-contextual-skipped"),
        process_with_model=True,
        persist=False,
    )

    detector = output.summary["diagnostic_evidence"]["detectors"]["contextual_battery_voltage"]
    assert detector["execution_status"] == "skipped"
    assert detector["finding"] == "unavailable"
    assert detector["skip_or_failure_reason"] == "contextual_reference_unavailable"


def test_contextual_bad_reference_is_visible_failure_not_normal(settings) -> None:
    write_fixed_model(settings.model_dir, anomaly_count=0)
    path = settings.data_dir / "evaluation" / "h3" / "contextual_reference.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")

    output = AnalysisService(settings, model_dir=settings.model_dir).analyze(
        battery_session(14.5, session_id="live-contextual-failed"),
        process_with_model=True,
        persist=False,
    )

    detector = output.summary["diagnostic_evidence"]["detectors"]["contextual_battery_voltage"]
    assert output.summary["overall_status"] == "ok"
    assert detector["execution_status"] == "failed"
    assert detector["finding"] == "unavailable"
    assert detector["error"]["type"] == "JSONDecodeError"


def test_production_result_is_identical_with_and_without_research_reference(settings) -> None:
    write_fixed_model(settings.model_dir, anomaly_count=2)
    service = AnalysisService(settings, model_dir=settings.model_dir)

    without_reference = service.analyze(
        battery_session(14.5, session_id="live-without-reference"),
        process_with_model=True,
        persist=False,
    ).summary
    write_contextual_reference(settings)
    with_reference = service.analyze(
        battery_session(14.5, session_id="live-with-reference"),
        process_with_model=True,
        persist=False,
    ).summary

    keys = [
        "overall_status",
        "health_score",
        "anomaly_ratio",
        "anomaly_window_count",
        "evidence_window_count",
        "minimum_windows_for_status",
        "evidence_sufficient",
        "most_unusual_features",
        "model_loaded",
    ]
    assert {key: without_reference[key] for key in keys} == {key: with_reference[key] for key in keys}
