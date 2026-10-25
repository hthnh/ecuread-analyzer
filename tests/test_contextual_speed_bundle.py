from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.domain.telemetry import TelemetrySample, V2_CORE_SIGNAL_COLUMNS
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file, raw_records_to_canonical_session
from app.ml.active_bundle import activate_bundle, load_bundle, resolve_active_bundle
from app.ml.contextual_speed import ContextualSpeedDetector, build_speed_reference, speed_context, speed_window_features
from app.ml.robust_reference import capture_balanced_stats
from app.ml.harness import ModelHarness
from app.services.analysis_service import AnalysisService, build_feature_frame, canonical_session_to_frame_data
from tests.test_live_research_harness import battery_session, write_contextual_reference
from tests.test_limited_data_gate import write_fixed_model

ROOT = Path(__file__).resolve().parents[1]


def feature_rows(speed=30.0, rpm=3000.0, count=60):
    return pd.DataFrame({"speed_median": [speed] * count, "speed_rate": [0.0] * count,
        "speed_rate_dispersion": [0.0] * count, "speed_stationary_fraction": [0.0 if speed else 1.0] * count,
        "speed_status": ["ok"] * count, "rpm_median": [rpm] * count,
        "tps_voltage_median": [.8] * count, "tps_raw_median": [10.0] * count,
        "speed_start_time_ms": np.arange(count) * 2500.0, "speed_end_time_ms": np.arange(count) * 2500.0 + 12250})


def reference(speed=30.0, rpm=3000.0):
    return build_speed_reference({str(i): feature_rows(speed, rpm) for i in range(3)}, provenance={"test": True})


def test_capture_balance_not_window_weight():
    a = pd.DataFrame({"reference_session_id": ["a"] * 10000 + ["b"] * 10 + ["c"] * 10,
                      "value": [100.0] * 10000 + [10.0] * 20})
    stats = capture_balanced_stats(a, "value", 1)
    assert stats["median"] == 10.0
    assert stats["weighting"] == "equal_capture_within_context"


def test_speed_native_raw_live_parity():
    path = ROOT / "real_data/fulldata_afterclean/session_000001.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    imported = import_native_jsonl_file(path).canonical_session
    live = raw_records_to_canonical_session(
        [{"sequence": i, "timestamp_ms": r["elapsed_ms"], "raw_hex": r["raw_hex"]} for i, r in enumerate(records)],
        raw_representation="native24_table17").canonical_session
    for sample, other, raw in zip(imported.samples, live.samples, records, strict=True):
        assert sample.vehicle_speed_kmh == other.vehicle_speed_kmh == bytes.fromhex(raw["raw_hex"])[19]
        assert sample.timestamp_ms == other.timestamp_ms
        assert sample.speed_provenance == other.speed_provenance


def test_invalid_frame_does_not_supply_speed():
    path = ROOT / "real_data/fulldata_afterclean/session_000001.jsonl"
    record = json.loads(path.read_text().splitlines()[0])
    frame = bytearray.fromhex(record["raw_hex"])
    frame[-1] ^= 1
    result = raw_records_to_canonical_session([{"raw_hex": frame.hex()}], raw_representation="native24_table17")
    assert result.canonical_session.samples[0].vehicle_speed_kmh is None


@pytest.mark.parametrize("value", [-1, 256, float("nan"), float("inf")])
def test_invalid_canonical_speed(value):
    with pytest.raises(ValueError):
        TelemetrySample(sequence=0, vehicle_speed_kmh=value)


def test_missing_speed_does_not_change_primary(settings):
    write_fixed_model(settings.model_dir, anomaly_count=2)
    write_contextual_reference(settings)
    output = AnalysisService(settings, model_dir=settings.model_dir).analyze(battery_session(14.5), persist=False)
    assert output.summary["anomaly_window_count"] == 2
    assert output.summary["diagnostic_evidence"]["detectors"]["contextual_battery_voltage"]["execution_status"] == "ok"


def test_speed_sparse_reference_abstains():
    r = build_speed_reference({"one": feature_rows()}, provenance={})
    output = ContextualSpeedDetector(r).infer(feature_rows(), None)
    assert output.status == "skipped" and output.scored_window_count == 0
    assert r["contexts"] == {} and r["unsupported_contexts"]


def test_speed_no_fixed_cvt_ratio_or_engine_off_assumption():
    for speed, rpm in ((0, 6000), (5, 6000), (50, 3000)):
        output = ContextualSpeedDetector(reference(speed, rpm)).infer(feature_rows(speed, rpm), None)
        assert output.status == "ok" and output.anomaly_window_count == 0
    rows = feature_rows()
    rows["rpm_median"] = 4400.0
    assert ContextualSpeedDetector(reference()).infer(rows, None).anomaly_window_count == 0


def test_speed_temporal_positive_and_supported_identity():
    rows = feature_rows()
    rows["speed_rate"] = 10.0
    result = ModelHarness([ContextualSpeedDetector(reference())]).run(rows).results[0]
    assert result.status == "ok" and result.anomaly_window_count == len(rows)
    assert result.window_payload(0)["evidence"]["speed_evidence"]["reason"] == "unusual_speed_change"
    assert result.window_payload(0)["evidence"]["speed_evidence"]["end_time_ms"] == 12250


def test_speed_quantization_and_missing_clock(settings):
    session = battery_session(14.5)
    samples = [s.model_copy(update={"vehicle_speed_kmh": 30.0 + i % 2}) for i, s in enumerate(session.samples)]
    session = session.model_copy(update={"samples": samples})
    frames = canonical_session_to_frame_data(session)
    feature_frame = build_feature_frame(frames, settings, V2_CORE_SIGNAL_COLUMNS)
    speed = speed_window_features(frames, feature_frame)
    assert speed.speed_status.eq("ok").all()
    assert speed.speed_rate.abs().max() < 1
    frames["relative_time_ms"] = None
    assert speed_window_features(frames, feature_frame).speed_status.ne("ok").all()


def test_active_bundle_and_rollback_are_loadable():
    from app.config import Settings
    settings = Settings(data_dir=ROOT / "data", model_dir=ROOT / "data/models")
    record = json.loads((ROOT / "data/model_refresh/build.json").read_text())
    current = load_bundle(settings, ROOT / record["bundle_manifest"])
    previous = load_bundle(settings, ROOT / record["rollback_manifest"])
    assert current.speed is not None and previous.speed is None
    assert current.manifest["provenance"]["training_window_count"] == 9989
    assert len(current.manifest["feature_names"]) == 81


def test_bundle_hash_failure_never_falls_back(tmp_path):
    from app.config import Settings
    settings = Settings(data_dir=tmp_path, model_dir=tmp_path / "models")
    settings.model_dir.mkdir()
    path = tmp_path / "manifest.json"
    path.write_text("{}")
    (settings.model_dir / "active_bundle.json").write_text(json.dumps({"manifest": "manifest.json", "sha256": "wrong"}))
    with pytest.raises(ValueError, match="manifest SHA mismatch"):
        resolve_active_bundle(settings)


def test_actual_active_bundle_missing_speed_and_rollback(tmp_path):
    from app.config import Settings
    from app.ml.inference import run_inference
    record = json.loads((ROOT / "data/model_refresh/build.json").read_text())
    settings = Settings(data_dir=ROOT / "data", model_dir=tmp_path)
    source = import_native_jsonl_file(ROOT / "real_data/fulldata_afterclean/session_000001.jsonl").canonical_session
    activate_bundle(settings, ROOT / record["bundle_manifest"])
    current = AnalysisService(settings).analyze(source, persist=False)
    missing = source.model_copy(update={"samples": [s.model_copy(update={"vehicle_speed_kmh": None}) for s in source.samples]})
    skipped = AnalysisService(settings).analyze(missing, persist=False)
    assert skipped.summary["diagnostic_evidence"]["detectors"]["contextual_speed"]["execution_status"] == "skipped"
    assert skipped.windows.decision_score.tolist() == current.windows.decision_score.tolist()
    assert skipped.windows.contextual_anomaly_score.tolist() == current.windows.contextual_anomaly_score.tolist()
    activate_bundle(settings, ROOT / record["rollback_manifest"])
    rollback = AnalysisService(settings).analyze(source, persist=False)
    direct = run_inference(rollback.feature_frame, ROOT / "data/models/honda_keihin_71_17_v2",
        expected_telemetry_schema_version="canonical-telemetry-v2", expected_signal_columns=V2_CORE_SIGNAL_COLUMNS)
    assert rollback.windows.decision_score.tolist() == direct.windows.decision_score.tolist()
    assert "contextual_speed" not in rollback.summary["diagnostic_evidence"]["detectors"]
    assert rollback.summary["model_bundle"]["bundle_id"] == "native-pre-full18"
    assert current.summary["model_bundle"]["bundle_id"] != "native-pre-full18"


def test_bundle_feature_contract_mismatch_rejected(tmp_path):
    from app.config import Settings
    record = json.loads((ROOT / "data/model_refresh/build.json").read_text())
    manifest = json.loads((ROOT / record["bundle_manifest"]).read_text())
    manifest["feature_names"] = manifest["feature_names"][::-1]
    path = tmp_path / "incompatible.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="compatibility mismatch"):
        load_bundle(Settings(data_dir=ROOT / "data"), path)


def test_bundle_artifact_hash_mismatch_rejected(tmp_path):
    from app.config import Settings
    record = json.loads((ROOT / "data/model_refresh/build.json").read_text())
    manifest = json.loads((ROOT / record["bundle_manifest"]).read_text())
    manifest["artifacts"]["speed"]["sha256"] = "0" * 64
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="SHA mismatch: speed"):
        load_bundle(Settings(data_dir=ROOT / "data"), path)


@pytest.mark.parametrize("relationship", [True, False])
def test_rca_speed_evidence_no_duplicate_operating_observation(relationship):
    from app.ml.root_cause_analysis_v2 import RootCauseAnalyzerV2
    from app.services.research_harness import _rca_case
    from app.ml.evidence_aggregation import AggregationPolicy, aggregate_window_results, build_aggregate_events
    from app.ml.harness import DetectorIdentity, DetectorResult
    features = feature_rows(count=1)
    features["window_index"] = 0
    features["start_frame_index"] = 0
    features["end_frame_index"] = 49
    features["duration_ms"] = 12250.0
    features["most_unusual_features"] = [["rpm_median", "tps_raw_median"]]
    features["is_anomaly"] = True
    primary = DetectorResult(identity=DetectorIdentity("isolation_forest", "IF", "test"), status="ok",
        required_features=(), windows=features, is_anomaly_column="is_anomaly", evidence_columns=("most_unusual_features",))
    if relationship:
        features["rpm_median"] = 7400
        features["tps_voltage_median"] = 3.4
        features["tps_raw_median"] = 100
    else:
        features["speed_rate"] = 10
    speed = ContextualSpeedDetector(reference(rpm=4800 if relationship else 3000)).infer(features, None)
    policy = AggregationPolicy(detector_ids=("isolation_forest", "contextual_speed"))
    rows = aggregate_window_results(session_id="test", feature_frame=features, results=[primary, speed], policy=policy)
    event = build_aggregate_events(rows)["finding_events"][0]
    case = _rca_case(analysis_run_id="test", session=battery_session(14.5), event=event, index=1,
        signal_columns=V2_CORE_SIGNAL_COLUMNS, telemetry_schema_version="canonical-telemetry-v2")
    result = RootCauseAnalyzerV2().analyze_case(case)
    operating = [o for o in result["observations"] if o["observation_type"] == "operating_pattern_observation"]
    assert len(operating) == (1 if relationship else 2)
    if relationship:
        assert operating[0]["evidence"]["supporting_speed_finding"]["finding"] == "positive"
    else:
        assert any(o["observation_id"] == "obs.v2.contextual_speed_deviation" for o in operating)
    assert any(o["observation_id"] == "obs.v2.iforest_outside_reference" for o in result["observations"])
    assert all("speed" not in c.get("cause_id", "") for c in result["possible_causes"])


def test_model_api_exposes_actual_active_bundle(tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    settings = Settings(data_dir=ROOT / "data", model_dir=tmp_path)
    record = json.loads((ROOT / "data/model_refresh/build.json").read_text())
    activate_bundle(settings, ROOT / record["bundle_manifest"])
    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/v1/model").json()
        assert payload["model_bundle"]["bundle_id"].startswith("native-full18-")
        assert payload["metadata"]["version"] == record["model_version"]
        assert client.get("/health").json()["model_loaded"] is True


def test_published_verification_never_archives_raw_changes(tmp_path, monkeypatch):
    from app.evaluation import published_integrity as integrity
    monkeypatch.setattr(integrity, "ROOT", tmp_path)
    record = tmp_path / "transition.json"
    monkeypatch.setattr(integrity, "RECORD", record)
    raw = tmp_path / "real_data/capture.jsonl"
    raw.parent.mkdir()
    raw.write_text("changed")
    record.write_text(json.dumps({"files": {"real_data/capture.jsonl": {
        "before_sha256": "original", "after_sha256": integrity.digest(raw)}}}))
    with pytest.raises(ValueError, match="without exact authorized transition"):
        integrity.verify_published_inputs({"real_data/capture.jsonl": "original"})
