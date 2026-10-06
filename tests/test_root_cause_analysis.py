from __future__ import annotations

from app.ml.contextual_detector import CONTEXTUAL_BATTERY_DETECTOR_ID
from app.ml.iforest_detector import ISOLATION_FOREST_DETECTOR_ID
from app.ml.root_cause_analysis import RootCauseAnalyzer, rca_contract


def _iforest(finding: str, *, features: str = "rpm_median;tps_raw_min", status: str = "ok") -> dict:
    applicable = status == "ok" and finding in {"positive", "negative"}
    return {
        "detector_id": ISOLATION_FOREST_DETECTOR_ID,
        "model_version": "iforest-test",
        "execution_status": status,
        "applicable": applicable,
        "applicability_reason": None if applicable else "missing_features",
        "finding": finding,
        "is_anomaly": True if finding == "positive" else False if finding == "negative" else None,
        "prediction": -1 if finding == "positive" else 1 if finding == "negative" else None,
        "detector_local_score": {"value": -0.6, "direction": "lower_is_more_anomalous"} if applicable else None,
        "detector_local_threshold": {"value": 0.0, "column": "decision_score"},
        "evidence": {"most_unusual_features": features, "window_health_score": 12.0} if applicable else {},
        "missing_features": ["tps_raw_median"] if not applicable else [],
        "error": None,
    }


def _contextual(finding: str, *, top: dict | None = None, status: str = "ok") -> dict:
    applicable = status == "ok" and finding in {"positive", "negative"}
    evidence = {}
    if applicable and top is not None:
        evidence = {
            "operating_context": "idle_closed_throttle",
            "contextual_evidence": {
                "operating_context": "idle_closed_throttle",
                "top_deviation": top,
                "threshold": 4.5,
                "status": "ok",
            },
        }
    return {
        "detector_id": CONTEXTUAL_BATTERY_DETECTOR_ID,
        "model_version": "contextual-test",
        "execution_status": status,
        "applicable": applicable,
        "applicability_reason": None if applicable else "missing_features",
        "finding": finding,
        "is_anomaly": True if finding == "positive" else False if finding == "negative" else None,
        "prediction": -1 if finding == "positive" else 1 if finding == "negative" else None,
        "detector_local_score": {"value": 8.0, "direction": "higher_is_more_anomalous"} if applicable else None,
        "detector_local_threshold": {"value": 4.5, "column": "contextual_threshold"} if applicable else None,
        "evidence": evidence,
        "missing_features": ["tps_raw_median"] if not applicable else [],
        "error": None,
    }


def _item(iforest: dict, contextual: dict, *, coverage: str = "full", evidence_state: str | None = None) -> dict:
    findings = {ISOLATION_FOREST_DETECTOR_ID: iforest, CONTEXTUAL_BATTERY_DETECTOR_ID: contextual}
    positive = [detector_id for detector_id, finding in findings.items() if finding["finding"] == "positive" and finding["execution_status"] == "ok"]
    negative = [detector_id for detector_id, finding in findings.items() if finding["finding"] == "negative" and finding["execution_status"] == "ok"]
    unavailable = [detector_id for detector_id, finding in findings.items() if finding["execution_status"] != "ok" or finding["finding"] == "unavailable"]
    if evidence_state is None:
        if coverage == "none":
            evidence_state = "insufficient_coverage"
        elif positive and negative:
            evidence_state = "detector_disagreement"
        elif len(positive) > 1:
            evidence_state = "multiple_detector_evidence"
        elif positive:
            evidence_state = "single_detector_evidence"
        else:
            evidence_state = "no_evidence"
    return {
        "validation_item_id": "h6-test-item",
        "source_h5_review_item_id": "h5-test-item",
        "session_id": "test-session",
        "validation_region_type": "synthetic_rule_test",
        "event": {
            "event_id": "test-event",
            "event_type": "detector_specific_event",
            "evidence_state": evidence_state,
            "coverage_status": coverage,
            "start_window_index": 0,
            "end_window_index": 0,
            "duration_ms": 2500.0,
            "window_count": 1,
        },
        "detector_outputs": {
            "positive_detector_ids": positive,
            "negative_detector_ids": negative,
            "unavailable_detector_ids": unavailable,
            "not_applicable_detector_ids": [],
            "by_detector": findings,
        },
        "telemetry_evidence": {
            "operating_context": "idle_closed_throttle",
            "contextual_top_deviation": contextual.get("evidence", {}).get("contextual_evidence", {}).get("top_deviation"),
            "isolation_forest_most_unusual_features": iforest.get("evidence", {}).get("most_unusual_features"),
            "telemetry": {
                "battery_voltage_min": 10.9,
                "battery_voltage_mean": 14.0,
                "battery_voltage_median": 14.4,
                "rpm_median": 1700.0,
                "tps_raw_median": 0.0,
                "tps_voltage_median": 0.48,
            },
        },
        "independent_label": {"label": "inconclusive"},
        "provenance": {"path": "synthetic-rule-test"},
    }


def _hypothesis_ids(result: dict) -> set[str]:
    return {hypothesis["hypothesis_id"] for hypothesis in result["hypotheses"]}


def test_rca_contract_declares_rules_and_forbids_operational_claims() -> None:
    contract = rca_contract()
    assert contract["policy"]["llm_used"] is False
    assert contract["policy"]["agents_used"] is False
    assert contract["policy"]["physical_fault_claims_allowed"] is False
    assert {rule["rule_id"] for rule in contract["rules"]} >= {
        "rca.electrical.contextual_voltage_deviation",
        "rca.detectors.disagreement_preservation",
    }


def test_contextual_voltage_evidence_generates_electrical_hypothesis_with_evidence_against() -> None:
    top = {"feature": "battery_voltage_min", "observed": 10.9, "reference_median": 14.2, "robust_z": 9.4}
    result = RootCauseAnalyzer().analyze_item(_item(_iforest("negative"), _contextual("positive", top=top)))

    assert "h.electrical_supply_variation" in _hypothesis_ids(result)
    hypothesis = result["hypotheses"][0]
    assert hypothesis["status"] == "supported"
    assert hypothesis["physical_fault_claim_made"] is False
    assert hypothesis["triggering_evidence"][0]["values"]["top_deviation"] == top
    assert any(evidence["evidence_id"] == "e.isolation_forest.negative" for evidence in hypothesis["evidence_against"])
    assert any(rule["rule_id"] == "rca.detectors.disagreement_preservation" and rule["applied"] for rule in result["applied_rules"])


def test_missing_required_contextual_evidence_prevents_voltage_rule() -> None:
    result = RootCauseAnalyzer().analyze_item(_item(_iforest("negative"), _contextual("positive", top=None)))

    assert "h.electrical_supply_variation" not in _hypothesis_ids(result)
    voltage_rule = next(rule for rule in result["applied_rules"] if rule["rule_id"] == "rca.electrical.contextual_voltage_deviation")
    assert voltage_rule["applied"] is False
    assert voltage_rule["reason"] == "contextual top deviation is unavailable"


def test_skipped_detector_does_not_become_voltage_evidence() -> None:
    result = RootCauseAnalyzer().analyze_item(
        _item(_iforest("negative"), _contextual("unavailable", top={"feature": "battery_voltage_min"}, status="skipped"), coverage="partial")
    )

    assert result["hypotheses"] == []
    assert "h.electrical_supply_variation" not in _hypothesis_ids(result)
    contextual_observation = next(
        observation for observation in result["observed_facts"] if observation["observation_id"] == "obs.detector.contextual_battery_voltage"
    )
    assert contextual_observation["evidence"]["score"] is None


def test_insufficient_coverage_produces_no_speculative_hypotheses() -> None:
    result = RootCauseAnalyzer().analyze_item(
        _item(
            _iforest("unavailable", status="skipped"),
            _contextual("unavailable", status="skipped"),
            coverage="none",
            evidence_state="insufficient_coverage",
        )
    )

    assert result["rca_state"] == "insufficient_evidence"
    assert result["hypotheses"] == []
    assert any(rule["rule_id"] == "rca.coverage.insufficient_evidence" and rule["applied"] for rule in result["applied_rules"])


def test_iforest_rules_preserve_disagreement_without_forced_consensus() -> None:
    result = RootCauseAnalyzer().analyze_item(
        _item(
            _iforest("positive", features="rpm_median;tps_raw_min;battery_voltage_constant_signal"),
            _contextual("negative", top={"feature": "battery_voltage_min", "robust_z": 0.1}),
        )
    )

    assert {
        "h.multivariate_pattern_outside_reference",
        "h.operating_state_or_throttle_input_variation",
        "h.possible_acquisition_or_constant_signal_artifact",
    }.issubset(_hypothesis_ids(result))
    assert all("consensus" not in hypothesis["hypothesis_id"] for hypothesis in result["hypotheses"])
    assert any(limit["limitation_id"] == "limit.detector_disagreement" for limit in result["evidence_limitations"])
    multivariate = next(
        hypothesis for hypothesis in result["hypotheses"] if hypothesis["hypothesis_id"] == "h.multivariate_pattern_outside_reference"
    )
    assert any(evidence["evidence_id"] == "e.contextual_voltage_not_corroborating" for evidence in multivariate["evidence_against"])
