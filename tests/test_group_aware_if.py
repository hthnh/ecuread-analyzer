from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from app.evaluation import group_aware_if as research


def sources():
    return [{"session_id": f"session_{i:06}", "physical_capture_group_id": f"group:{i}",
             "source_sha256": f"sha:{i}", "window_count": 8} for i in range(18)]


def frames():
    random = np.random.default_rng(42)
    return {s["session_id"]: pd.DataFrame(random.normal(size=(8, 81)), columns=research.FEATURES) for s in sources()}


def test_all_logo_folds_hold_out_a_complete_group_once():
    folds = research.make_folds(sources())
    assert len(folds) == 18
    assert len({f["held_out"]["physical_capture_group_id"] for f in folds}) == 18
    for f in folds:
        assert len(f["training"]) == 17
        assert f["held_out"]["physical_capture_group_id"] not in {s["physical_capture_group_id"] for s in f["training"]}


def test_duplicate_capture_groups_rejected():
    bad = sources()
    bad[1]["physical_capture_group_id"] = bad[0]["physical_capture_group_id"]
    with pytest.raises(ValueError, match="distinct attested groups"):
        research.make_folds(bad)


def test_held_out_poison_does_not_enter_training():
    fold, data = research.make_folds(sources())[0], frames()
    before = research.array_sha(research.training_matrix(fold, data).to_numpy())
    data[fold["held_out"]["session_id"]] += 1e9
    assert research.array_sha(research.training_matrix(fold, data).to_numpy()) == before


@pytest.mark.parametrize("field", ["session_id", "physical_capture_group_id", "source_sha256"])
def test_partition_leakage_rejected(field):
    fold = deepcopy(research.make_folds(sources())[0])
    fold["training"][0][field] = fold["held_out"][field]
    with pytest.raises(ValueError, match="leakage"):
        research.training_matrix(fold, frames())


def test_train_only_fit_and_offset_in_disposable_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "STUDY", tmp_path)
    (tmp_path / "protocol.json").write_text("{}")
    protocol = {"study_id": "unit-test", "decoder": "honda_keihin_71_17:1.0.0", "telemetry_schema": "canonical-telemetry-v2",
                "window_size": 50, "window_step": 10, "quality_policy": {"max_checksum_error_ratio": 0, "max_invalid_decoded_ratio": 0},
                "hyperparameters": {"n_estimators": 200, "contamination": .03, "random_state": 42}}
    fold, data = research.make_folds(sources())[0], frames()
    result = research.fit_fold(fold, data, protocol, tmp_path / "model")
    assert result["training_only_scaler_verified"]
    assert result["training_only_offset_verified"]
    assert result["training_windows"] == 136
    assert result["training_source_intersection_test"] == []


def test_fits_cannot_write_outside_disposable_storage():
    with pytest.raises(ValueError, match="disposable /tmp"):
        research.fit_fold({}, {}, {}, research.ROOT / "data/models/forbidden-research-fit")


def test_run_breaks_and_overlapping_time_union():
    rows = [{"window_index": i, "row_start": i * 10, "row_end": i * 10 + 49,
             "start_s": i * 2.5, "end_s": i * 2.5 + 12.25,
             "is_anomaly": True, "score_sample": -.6, "decision_score": -.1} for i in range(3)]
    frame = pd.DataFrame({"frame_index": range(70), "relative_time_ms": np.arange(70) * 250})
    result = research.burden(rows, [True, False, True], {"frame_data": frame})
    assert result["run_count"] == 2
    assert result["flag_time_union_s"] == 17.25
    assert result["windows"] == 2


def test_no_support_not_zero_alarm_rate():
    result = research.burden([], [], {"frame_data": pd.DataFrame()}, labeled_s=10)
    assert result["flag_frequency"] is None
    assert result["supported"] is False
    assert result["labeled_context_duration_s"] == 10


def test_capture_bootstrap_is_reproducible_and_sparse_groups_abstain():
    assert research.bootstrap_interval([0, .2, .7]) == research.bootstrap_interval([0, .2, .7])
    assert research.bootstrap_interval([.9])["interval"] is None


def test_ambiguous_windows_not_assigned_a_context():
    row = {"contexts": {"macro_operating": {"status": "ambiguous", "assigned_label": None}}}
    assert research.stratum(row, "macro_operating") == "__ambiguous"


def test_decision_never_interprets_sparse_or_unstable_contexts_as_adequacy():
    contrast = {"track": "macro_operating", "left": "stationary_running", "right": "moving_episode",
                "supported_groups": 5, "mean_paired_flag_difference": .2,
                "leave_capture_out_influence": {"1": {"positive_in_all": True}}}
    sensitivity = {"short": {"contrasts": [deepcopy(contrast)]}, "discrepant": {"contrasts": [deepcopy(contrast)]}}
    assert research.decide([contrast], sensitivity)["verdict"] == "context_experiment_justified"
    sensitivity["short"]["contrasts"][0]["mean_paired_flag_difference"] = -.1
    assert research.decide([contrast], sensitivity)["verdict"] == "insufficient_evidence"
    contrast["supported_groups"] = 1
    assert research.decide([contrast], sensitivity)["verdict"] == "insufficient_evidence"


def test_preregistration_tamper_fails_closed(tmp_path):
    for name in ("protocol", "fold_manifest", "protected_inputs"):
        (tmp_path / (name + ".json")).write_text("{}")
    registration = {name + "_sha256": research.file_sha256(tmp_path / (name + ".json")) for name in ("protocol", "fold_manifest", "protected_inputs")}
    (tmp_path / "preregistration.json").write_text(json.dumps(registration))
    (tmp_path / "protocol.json").write_text("changed")
    with pytest.raises(ValueError, match="preregistration changed"):
        research.load_registration(tmp_path)


def test_complete_published_experiment():
    study = research.STUDY
    if not (study / "refit_reproducibility.json").exists():
        pytest.skip("complete H10.1 run and guarded repeat first")
    protocol, folds, protected = research.load_registration()
    assert protocol["outer_folds"] == 18
    predictions = research.read_jsonl(study / "held_out_predictions.jsonl")
    assert len(predictions) == 9989
    assert len({(r["session_id"], r["window_index"]) for r in predictions}) == 9989
    assert len(folds) == len(research.read_json(study / "fitted_artifact_provenance.json")["folds"]) == 18
    assert all(r["model_scaler_byte_identical"] and r["held_out_poison_fit_invariant"] for r in research.read_json(study / "refit_reproducibility.json")["folds"])
    from app.evaluation.published_integrity import verify_published_inputs
    assert not verify_published_inputs(protected)["changed_files"]
    assert research.read_json(study / "final_decision.json")["manual_boundary_adjudication"] == "pending"
