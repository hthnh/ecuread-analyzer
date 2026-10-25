from copy import deepcopy
import json

import pytest

import app.evaluation.context_benchmark_readiness as readiness
from app.evaluation.context_benchmark_readiness import (
    CONTENT_SHA, ROOT, benchmark_design, distribution,
    independent_assignment, score_statistics, union_duration, verify_manifest,
)


def interval(id, a, b, label="moving_episode"):
    return {"id": id, "start_s": a, "end_s": b, "label": label, "review_provenance": "bulk_operator_endorsement"}


def test_boundary_crossing_is_not_majority_voted():
    rows = [interval("a", 0, 9), interval("b", 9, 10, "stationary_running")]
    result = independent_assignment(rows, 0, 10, 10)
    assert result["status"] == "ambiguous"
    assert result["assigned_label"] is None
    assert result["interval_ids"] == ["a", "b"]


def test_identical_label_boundary_remains_ambiguous():
    result = independent_assignment([interval("a", 0, 5), interval("b", 5, 10)], 0, 9, 10)
    assert result["status"] == "ambiguous"
    assert result["boundary_times_s"] == [5]


def test_unknown_is_abstention_not_missing():
    assert independent_assignment([interval("a", 0, 10, "unknown")], 1, 9, 10)["status"] == "abstained"
    assert independent_assignment([interval("a", 0, 4), interval("b", 6, 10)], 1, 9, 10)["status"] == "unresolved"


def test_last_sample_and_no_sample_boundary_policy():
    assert independent_assignment([interval("a", 0, 10)], 1, 10, 10)["status"] == "homogeneous"
    rows = [interval("a", 0, 5), interval("tiny_stop", 5, 5.01, "stationary_running"), interval("b", 5.01, 10)]
    result = independent_assignment(rows, 4.9, 5.1, 10)
    assert result["status"] == "ambiguous"
    assert "tiny_stop" in result["interval_ids"]


def test_alert_time_does_not_sum_overlapping_windows():
    assert union_duration([(0, 12.25), (2.5, 14.75), (17, 20)]) == 17.75
    assert union_duration([]) == 0
    with pytest.raises(ValueError):
        union_duration([(2, 1)])


def test_descriptive_scores_have_no_fault_metrics():
    result = score_statistics([{"session_id": "a", "is_anomaly": True, "score_sample": -.6, "decision_score": -.1}])
    assert result["flag_frequency"] == 1
    assert "false_positive_rate" not in result
    assert score_statistics([])["flag_frequency"] is None
    with pytest.raises(ValueError):
        distribution([float("nan")])


def test_pinned_export_and_tampering_fail_closed(tmp_path):
    manifest, audit = verify_manifest()
    assert manifest["content_sha256"] == CONTENT_SHA
    assert audit["canonical_sample_count"] == 100688
    bad = deepcopy(manifest)
    bad["frozen"] = True
    (tmp_path / "export_manifest.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="content SHA"):
        verify_manifest(tmp_path)


def test_referenced_file_hash_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(readiness, "file_sha256", lambda path: "invalid")
    with pytest.raises(ValueError, match="file missing/SHA mismatch"):
        verify_manifest()


def test_existing_output_is_never_overwritten(tmp_path):
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text("unchanged")
    with pytest.raises(ValueError, match="refusing overwrite"):
        readiness.run(tmp_path)
    assert sentinel.read_text() == "unchanged"


def test_benchmark_design_preserves_human_and_fault_gates():
    design = benchmark_design({"representations": {"macro": {"by_label": {}}}}, {"summary": {}})
    assert design["verdict"] == "descriptive_analysis_only"
    assert design["human_boundary_validation"] == "pending"
    assert design["verified_physical_fault_labels"] == 0
    assert design["smallest_next_experiment"]["execution_now"].startswith("none")


def test_published_h10_outputs_are_complete_and_read_only():
    base = ROOT / "data/evaluation/h10"
    if not base.exists():
        pytest.skip("run the H10 read-only audit to generate corpus-specific artifacts")
    audit = json.loads((base / "h10_source_leakage_audit.json").read_text())
    assert audit["input_verification"]["windows"] == 9989
    assert audit["historical_entries_verified"] == 57
    assert len(audit["per_capture_matrix"]) == 18
    assert all(not s["unseen_test_set"] for s in audit["per_capture_matrix"])
    assert audit["summary"] == {"native_fit_overlap_captures": 7, "candidate_fit_and_reference_captures": 8,
                                "candidate_train_holdout_correlated_capture_groups": 2, "previous_selection_exposed_captures": 18}
    assert len(audit["historical_input_sample_hash_checks"]) == 114
    assert all(r["matched"] for r in audit["historical_input_sample_hash_checks"])
    pinned = audit["input_verification"]["protected_input_sha256"]
    from app.evaluation.published_integrity import verify_published_inputs
    assert not verify_published_inputs(pinned)["changed_files"]
    eligible = json.loads((base / "h10_context_window_eligibility.json").read_text())
    assert eligible["missing_mapping_count"] == eligible["mapping_conflicts"] == 0
    assert eligible["evidence_discrepancy_sample_count_unique"] == 19
    assert eligible["representations"]["macro"]["counts"]["both_known_without_measured_discrepancy"] == 5419
    for rep in ("macro", "micro"):
        for axis in ("operating", "thermal"):
            assert sum(v for k, v in eligible["representations"][rep]["counts"].items() if k.startswith(axis + ":")) == 9989
    baseline = json.loads((base / "h10_baseline_context_audit.json").read_text())
    assert len(baseline["window_results"]) == baseline["all_windows"]["windows"] == 9989
    assert baseline["all_windows"]["flagged_windows"] == 719
    assert sum(s["windows"] for s in baseline["per_capture"]) == 9989
    assert sum(s["contextual_battery"]["scored_windows"] + s["contextual_battery"]["skipped_windows"] for s in baseline["per_capture"]) == 9989
