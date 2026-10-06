from __future__ import annotations

from fastapi.testclient import TestClient

from app.evaluation.diagnostic_integration import build_representative_examples
from app.integration.diagnostic_evidence import (
    build_diagnostic_evidence,
    build_research_diagnostic_evidence,
    diagnostic_evidence_contract,
)
from app.main import create_app


def _session_stub():
    class Session:
        session_id = "session-current"
        vehicle_id = "bike-001"
        device_id = "device-001"
        decoder_id = "decoder-a"
        decoder_version = "1.0.0"
        decoder_version_key = "decoder-a:1.0.0"

    return Session()


def _detector_summary(status: str = "ok", anomaly_count: int | None = 0) -> dict:
    return {
        "detector_id": "isolation_forest",
        "model_name": "iforest",
        "model_family": "IsolationForest",
        "model_version": "iforest-v1",
        "enabled": True,
        "status": status,
        "reason": None if status == "ok" else "model_unavailable",
        "required_features": ["rpm_mean"],
        "missing_features": [],
        "scored_window_count": 4 if status == "ok" else 0,
        "anomaly_window_count": anomaly_count,
        "anomaly_ratio": (anomaly_count or 0) / 4 if status == "ok" else None,
        "anomaly_score": {"column": "score_sample", "direction": "lower_is_more_anomalous"},
        "prediction": {"column": "prediction", "is_anomaly_column": "is_anomaly"},
        "available_evidence": ["score_sample", "decision_score"],
        "warnings": [],
    }


def _canonical_payload(count: int = 25) -> dict:
    return {
        "session_id": "sess_h9_api",
        "vehicle_id": "bike-001",
        "device_id": "xiao-ecu-01",
        "ecu_profile_id": "honda_keihin_legacy_29",
        "decoder_id": "honda_keihin_legacy_29",
        "decoder_version": "0.1.0",
        "sampling": {"sample_interval_ms": 100},
        "samples": [
            {
                "sequence": index,
                "timestamp_ms": index * 100,
                "rpm": 1200 + index,
                "tps_voltage": 0.5,
                "tps_raw_candidate": 0,
                "battery_voltage": 12.8,
                "iat_c": 33,
                "ect_c_candidate": 52,
                "map_raw": 89,
                "frame_valid": True,
                "checksum_valid": True,
            }
            for index in range(count)
        ],
    }


def _research_result() -> dict:
    detector_output = {
        "positive_detector_ids": ["isolation_forest"],
        "negative_detector_ids": ["contextual_battery_voltage"],
        "unavailable_detector_ids": [],
        "by_detector": {
            "isolation_forest": {
                "model_version": "iforest-v1",
                "execution_status": "ok",
                "applicable": True,
                "finding": "positive",
                "detector_local_score": {"value": -0.7, "direction": "lower_is_more_anomalous"},
                "detector_local_threshold": {"value": 0.0},
                "evidence": {},
                "missing_features": [],
            },
            "contextual_battery_voltage": {
                "model_version": "ctx-v1",
                "execution_status": "ok",
                "applicable": True,
                "finding": "negative",
                "detector_local_score": {"value": 0.1, "direction": "higher_is_more_anomalous"},
                "detector_local_threshold": {"value": 4.5},
                "evidence": {},
                "missing_features": [],
            },
        },
    }
    return {
        "source_case_id": "case-1",
        "source_review_item_id": "review-1",
        "session_id": "session-research",
        "source_event": {
            "event_id": "event-1",
            "evidence_state": "detector_disagreement",
            "coverage_status": "full",
        },
        "observations": [
            {
                "observation_id": "obs.v2.detector_disagreement",
                "statement": "Detector disagreement preserved.",
                "provenance": {"detector_output": detector_output},
            }
        ],
        "symptoms": [],
        "possible_causes": [],
        "recommended_checks": [{"check_id": "check.v2.detector_scope_review"}],
        "evidence_limitations": [],
        "recommended_check_priorities": [
            {
                "check_id": "check.v2.detector_scope_review",
                "priority": "priority",
                "reason": "Comparable history shows recurrent evidence.",
            }
        ],
        "historical_evidence": {
            "status": "available",
            "summary": {},
            "comparisons": [
                {
                    "recurrence": {"category": "recurrent"},
                    "trend": {"trend_status": "no_observable_trend"},
                    "compatibility": {"comparable_session_count": 3},
                    "historical_occurrences": {"occurrence_count": 2},
                }
            ],
        },
        "provenance": {"rule_catalog_version": "evidence-grounded-rca-v2"},
    }


def test_contract_is_additive_and_blocks_unsafe_claims() -> None:
    contract = diagnostic_evidence_contract()

    assert contract["policy"]["additive_backward_compatible"] is True
    assert contract["policy"]["production_anomaly_status_changed"] is False
    assert contract["policy"]["h7_v1_hypotheses_exposed_as_current_conclusions"] is False
    assert contract["policy"]["check_priority_is_failure_probability"] is False


def test_skipped_detector_is_unavailable_not_normal() -> None:
    evidence = build_diagnostic_evidence(
        analysis_run_id="analysis-1",
        session=_session_stub(),
        model_version=None,
        telemetry_schema_version="canonical-telemetry-v1",
        feature_schema_version="ecu-window-features-v1",
        signal_columns=["rpm"],
        detector_results=[_detector_summary(status="skipped", anomaly_count=None)],
    )

    detector = evidence["detectors"]["isolation_forest"]
    assert detector["finding"] == "unavailable"
    assert detector["applicability"]["applicable"] is False
    assert evidence["aggregate"]["evidence_state"] == "insufficient_coverage"
    assert evidence["aggregate"]["no_evidence_is_verified_healthy"] is False


def test_no_evidence_is_not_verified_healthy() -> None:
    evidence = build_diagnostic_evidence(
        analysis_run_id="analysis-1",
        session=_session_stub(),
        model_version="iforest-v1",
        telemetry_schema_version="canonical-telemetry-v1",
        feature_schema_version="ecu-window-features-v1",
        signal_columns=["rpm"],
        detector_results=[_detector_summary(status="ok", anomaly_count=0)],
    )

    assert evidence["detectors"]["isolation_forest"]["finding"] == "negative"
    assert evidence["aggregate"]["evidence_state"] == "no_evidence"
    assert evidence["aggregate"]["no_evidence_is_verified_healthy"] is False


def test_analysis_api_returns_and_persists_additive_diagnostic_evidence(settings) -> None:
    client = TestClient(create_app(settings))

    response = client.post("/api/v1/analysis", json=_canonical_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["overall_status"] == "model_unavailable"
    assert "diagnostic_evidence" in body
    assert body["diagnostic_evidence"]["production_boundary"]["production_anomaly_status_changed"] is False
    assert body["diagnostic_evidence"]["detectors"]["isolation_forest"]["finding"] == "unavailable"

    persisted = client.get(f"/api/v1/analyses/{body['analysis_run_id']}").json()
    assert persisted["overall_status"] == body["overall_status"]
    assert persisted["diagnostic_evidence"]["schema_version"] == "analyzer-diagnostic-evidence-v1"


def test_upload_response_keeps_result_shape_and_adds_top_level_evidence(settings, fixture_204330) -> None:
    client = TestClient(create_app(settings))
    with fixture_204330.open("rb") as handle:
        response = client.post(
            "/api/v1/sessions",
            files={"file": ("serial_log.txt", handle, "text/plain")},
            data={"sample_interval_ms": "100"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["result"]["overall_status"] == "model_unavailable"
    assert "diagnostic_evidence" in body
    assert "diagnostic_evidence" not in body["result"]
    assert body["diagnostic_evidence"]["history"]["status"] == "unavailable"


def test_research_payload_keeps_rca_v2_sections_and_no_causal_claim_from_history() -> None:
    evidence = build_research_diagnostic_evidence(_research_result())

    assert evidence["aggregate"]["evidence_state"] == "detector_disagreement"
    assert evidence["interpretation"]["h7_v1_hypotheses_exposed"] is False
    assert evidence["interpretation"]["possible_causes"] == []
    assert evidence["history"]["future_sessions_allowed"] is False
    assert evidence["recommended_check_priorities"][0]["priority"] == "priority"
    assert evidence["production_boundary"]["root_cause_confirmed"] is False


def test_representative_examples_detect_required_scenarios() -> None:
    evidence = build_research_diagnostic_evidence(_research_result())
    payloads = {
        "payloads": [
            {
                "source_case_id": "case-1",
                "session_id": "session-research",
                "source_event_id": "event-1",
                "diagnostic_evidence": evidence,
            }
        ]
    }

    examples = build_representative_examples(payloads)

    assert "detector_disagreement" in examples["covered_scenarios"]
    assert "recurrent_history" in examples["covered_scenarios"]
    assert "history_prioritized_check" in examples["covered_scenarios"]
