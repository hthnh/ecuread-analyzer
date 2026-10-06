from __future__ import annotations

from app.evaluation.historical_evidence import (
    FAMILY_CONTEXTUAL_VOLTAGE,
    FAMILY_IFOREST,
    attach_historical_evidence_to_rca_v2_results,
    build_baseline_governance,
    build_historical_comparisons,
    build_historical_observation_index,
    compare_current_record_to_history,
    historical_evidence_contract,
)


def _session(session_id: str, *, split: str = "unassigned", label: str | None = None) -> dict:
    return {
        "session_id": session_id,
        "temporal_order_key": session_id,
        "capture_provenance": {
            "vehicle_id": "vehicle-a",
            "device_id": "device-a",
            "ecu_profile_id": "profile-a",
            "source_type": "canonical",
        },
        "decoder": {
            "decoder_id": "decoder-a",
            "decoder_version": "1.0.0",
            "decoder_version_key": "decoder-a:1.0.0",
        },
        "telemetry_schema_version": "canonical-telemetry-v2",
        "verified_signals": ["battery_voltage", "rpm", "tps_raw", "tps_voltage"],
        "signal_columns": ["battery_voltage", "rpm", "tps_raw", "tps_voltage"],
        "sample_count": 100,
        "window_count": 20,
        "evaluation_ready": True,
        "path": f"data/telemetry/{session_id}",
        "artifacts": {"samples_sha256": f"sha-{session_id}", "metadata_sha256": f"meta-{session_id}"},
        "training_evaluation_split": {
            "split": split,
            "label": label,
            "label_source": "human_review" if label else None,
            "ground_truth_available": bool(label),
            "suitable_for_precision_recall_f1": False,
        },
    }


def _h5_item(session_id: str, *, finding: str = "positive") -> dict:
    return {
        "review_item_id": f"h5-{session_id}",
        "event": {
            "event_id": f"{session_id}:finding-0001",
            "event_type": "detector_specific_event",
            "session_id": session_id,
            "evidence_state": "single_detector_evidence",
            "coverage_status": "full",
            "start_time_ms": 0.0,
            "end_time_ms": 2500.0,
            "duration_ms": 2500.0,
            "window_count": 1,
            "representative_window": {
                "detector_findings": [
                    {
                        "detector_id": "isolation_forest",
                        "execution_status": "ok",
                        "finding": finding,
                        "model_version": "iforest-v1",
                        "detector_local_score": {"value": -0.7, "direction": "lower_is_more_anomalous"},
                        "detector_local_threshold": {"value": 0.0},
                        "evidence": {"most_unusual_features": "battery_voltage_median;rpm_median"},
                    }
                ]
            },
            "detector_versions": {"isolation_forest": "iforest-v1"},
        },
        "provenance": {
            "session_id": session_id,
            "path": f"data/telemetry/{session_id}",
            "artifacts": {"samples_sha256": f"sha-{session_id}"},
        },
    }


def _rca_result(session_id: str, *, case_id: str = "case-current", possible_causes: list | None = None) -> dict:
    return {
        "source_case_id": case_id,
        "source_review_item_id": "h7-review-current",
        "session_id": session_id,
        "source_event": {
            "event_id": f"{session_id}:finding-current",
            "event_type": "detector_specific_event",
            "evidence_state": "single_detector_evidence",
            "coverage_status": "full",
            "start_time_ms": 0.0,
            "end_time_ms": 2500.0,
            "duration_ms": 2500.0,
            "window_count": 1,
        },
        "observations": [
            {
                "observation_id": "obs.v2.iforest_outside_reference",
                "observation_type": "detector_observation",
                "statement": "IF positive.",
                "evidence": {
                    "detector_id": "isolation_forest",
                    "most_unusual_features": ["battery_voltage_median"],
                    "detector_finding": {
                        "detector_id": "isolation_forest",
                        "execution_status": "ok",
                        "finding": "positive",
                        "model_version": "iforest-v1",
                        "detector_local_score": {"value": -0.8, "direction": "lower_is_more_anomalous"},
                    },
                },
                "provenance": {
                    "rule_version": "evidence-grounded-rca-v2",
                    "source_rule_id": "rca.v2.iforest.detector_observation",
                    "detector_output": {
                        "by_detector": {
                            "isolation_forest": {"model_version": "iforest-v1"},
                        }
                    },
                },
            }
        ],
        "symptoms": [],
        "possible_causes": possible_causes or [],
        "recommended_checks": [{"check_id": "check.v2.iforest_feature_review"}],
        "evidence_limitations": [],
        "provenance": {
            "source_case_provenance": {
                "session_id": session_id,
                "path": f"data/telemetry/{session_id}",
                "artifacts": {"samples_sha256": f"sha-{session_id}"},
            }
        },
    }


def _h2_manifest() -> dict:
    return {
        "sessions": [
            _session("ride-20260901-0800", split="normal_train", label="normal_ride"),
            _session("ride-20260902-0800", split="normal_train", label="normal_ride"),
            _session("ride-20260903-0800"),
        ]
    }


def test_contract_forbids_memory_and_root_cause_claims() -> None:
    contract = historical_evidence_contract()

    assert contract["policy"]["llm_used"] is False
    assert contract["policy"]["vector_database_used"] is False
    assert contract["policy"]["history_can_create_root_cause"] is False
    assert contract["policy"]["unsupported_h7_v1_hypotheses_indexed_as_facts"] is False


def test_index_preserves_versions_and_excludes_h7_v1_hypotheses_as_facts() -> None:
    h73 = {"results": [_rca_result("ride-20260903-0800")]}
    index = build_historical_observation_index(_h2_manifest(), {"items": [_h5_item("ride-20260901-0800")]}, h73)

    assert index["policy"]["h7_v1_hypotheses_indexed"] is False
    assert any(record["detector_versions"].get("isolation_forest") == "iforest-v1" for record in index["records"])
    assert any(record["source_artifact"] == "h7_3_rca_v2_results" for record in index["records"])
    assert not any("hypothesis" in (record.get("source_section") or "") for record in index["records"])


def test_future_sessions_cannot_leak_into_current_interpretation() -> None:
    h2 = _h2_manifest()
    h73 = {"results": [_rca_result("ride-20260902-0800")]}
    h5 = {"items": [_h5_item("ride-20260901-0800"), _h5_item("ride-20260903-0800")]}
    index = build_historical_observation_index(h2, h5, h73)
    comparisons = build_historical_comparisons(h2, index, h73)
    iforest = next(
        comparison
        for comparison in comparisons["comparisons"]
        if comparison["current"]["observation_family"] == FAMILY_IFOREST
    )

    occurrence_sessions = set(iforest["historical_occurrences"]["occurrence_session_ids"])
    assert "ride-20260901-0800" in occurrence_sessions
    assert "ride-20260903-0800" not in occurrence_sessions
    assert comparisons["policy"]["future_data_leakage_prevented"] is True


def test_recurrence_uses_compatible_prior_sessions_only() -> None:
    h2 = _h2_manifest()
    h73 = {"results": [_rca_result("ride-20260903-0800")]}
    h5 = {"items": [_h5_item("ride-20260901-0800"), _h5_item("ride-20260902-0800")]}
    index = build_historical_observation_index(h2, h5, h73)
    comparisons = build_historical_comparisons(h2, index, h73)
    iforest = next(
        comparison
        for comparison in comparisons["comparisons"]
        if comparison["current"]["observation_family"] == FAMILY_IFOREST
    )

    assert iforest["compatibility"]["comparable_session_count"] == 2
    assert iforest["historical_occurrences"]["occurrence_session_count"] == 2
    assert iforest["recurrence"]["category"] == "recurrent"


def test_record_level_version_mismatch_blocks_occurrence() -> None:
    current = {
        "record_id": "current",
        "session_id": "ride-20260902-0800",
        "temporal_order_key": "ride-20260902-0800",
        "capture_timestamp": "20260902080000",
        "vehicle_identity": {"vehicle_id": "vehicle-a"},
        "decoder": {"decoder_version_key": "decoder-a:1.0.0"},
        "source_provenance": {"artifacts": {"samples_sha256": "sha-current"}},
        "signals": ["battery_voltage"],
        "observation_family": FAMILY_IFOREST,
        "detector_versions": {"isolation_forest": "iforest-v2"},
        "event_magnitude": {"value": -0.8, "direction": "lower_is_more_anomalous"},
    }
    historical = {
        **current,
        "record_id": "history",
        "session_id": "ride-20260901-0800",
        "temporal_order_key": "ride-20260901-0800",
        "capture_timestamp": "20260901080000",
        "detector_versions": {"isolation_forest": "iforest-v1"},
        "source_provenance": {"artifacts": {"samples_sha256": "sha-history"}},
        "event": {"event_id": "history-event"},
    }
    comparison = compare_current_record_to_history(1, current, [historical], [_session("ride-20260901-0800")])

    assert comparison["compatibility"]["comparable_session_count"] == 1
    assert comparison["historical_occurrences"]["occurrence_count"] == 0
    assert "detector_version_mismatch:isolation_forest" in comparison["comparison_limitations"]


def test_history_prioritizes_checks_without_creating_causes() -> None:
    h2 = _h2_manifest()
    h73 = {"results": [_rca_result("ride-20260903-0800")]}
    h5 = {"items": [_h5_item("ride-20260901-0800"), _h5_item("ride-20260902-0800")]}
    index = build_historical_observation_index(h2, h5, h73)
    comparisons = build_historical_comparisons(h2, index, h73)
    with_history = attach_historical_evidence_to_rca_v2_results(h73, comparisons)
    result = with_history["results"][0]

    assert result["possible_causes"] == []
    assert result["recommended_checks"] == [{"check_id": "check.v2.iforest_feature_review"}]
    assert result["recommended_check_priorities"][0]["priority"] == "priority"
    assert with_history["policy"]["history_created_possible_causes"] is False


def test_baseline_governance_separates_history_from_reference_candidates() -> None:
    h2 = _h2_manifest()
    index = build_historical_observation_index(h2, {"items": [_h5_item("ride-20260901-0800")]}, {"results": []})
    governance = build_baseline_governance(h2, index)

    assert governance["policy"]["historical_evidence_corpus_is_nominal_baseline"] is False
    assert governance["policy"]["normal_train_means_verified_healthy"] is False
    assert governance["nominal_reference_baseline"]["trusted_normal_ground_truth_count"] == 0
    assert governance["nominal_reference_baseline"]["excluded_candidate_count"] == 1


def test_contextual_voltage_history_remains_symptom_context_not_cause() -> None:
    result = _rca_result("ride-20260903-0800")
    result["observations"][0]["observation_id"] = "obs.v2.contextual_voltage_deviation"
    result["observations"][0]["evidence"] = {
        "top_deviation": {"feature": "battery_voltage_min", "robust_z": 7.1},
        "detector_finding": {
            "detector_id": "contextual_battery_voltage",
            "execution_status": "ok",
            "finding": "positive",
            "model_version": "ctx-v1",
        },
    }
    result["symptoms"] = [
        {
            "symptom_id": "sym.v2.contextual_supply_voltage_deviation",
            "symptom_type": "electrical_voltage",
            "statement": "Voltage symptom.",
            "evidence": result["observations"][0]["evidence"],
            "provenance": result["observations"][0]["provenance"],
        }
    ]
    result["recommended_checks"] = [{"check_id": "check.v2.external_supply_measurement"}]
    h2 = _h2_manifest()
    h73 = {"results": [result]}
    h5 = {"items": []}
    index = build_historical_observation_index(h2, h5, h73)
    comparisons = build_historical_comparisons(h2, index, h73)
    with_history = attach_historical_evidence_to_rca_v2_results(h73, comparisons)

    assert any(
        comparison["current"]["observation_family"] == FAMILY_CONTEXTUAL_VOLTAGE
        for comparison in comparisons["comparisons"]
    )
    assert with_history["results"][0]["possible_causes"] == []
    assert with_history["results"][0]["historical_evidence"]["history_can_create_root_cause"] is False
