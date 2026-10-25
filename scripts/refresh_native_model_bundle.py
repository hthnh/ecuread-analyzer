"""Build, verify, and optionally activate an all-18 native detector bundle locally."""
from __future__ import annotations

import argparse
import json
import resource
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from fastapi.testclient import TestClient

from app.config import Settings
from app.evaluation.context_benchmark_readiness import verify_manifest, verify_mapping
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.ml.active_bundle import activate_bundle, load_bundle, sha256
from app.ml.contextual_detector import build_contextual_battery_reference
from app.ml.contextual_speed import build_speed_reference, speed_window_features
from app.ml.features import get_feature_names
from app.domain.telemetry import V2_CORE_SIGNAL_COLUMNS as SIGNALS
from app.ml.training import train_isolation_forest
from app.services.analysis_service import AnalysisService, build_feature_frame, canonical_session_to_frame_data
from app.context_annotation.storage import atomic_write_json

DATA = ROOT / "data"
OUTPUT = DATA / "model_refresh"


def artifact(path):
    return {"path": str(path.relative_to(DATA)), "sha256": sha256(path)}


def manifest_for(bundle_id, artifacts, provenance):
    return {"schema_version": "ecu-detector-bundle-v1", "bundle_id": bundle_id,
            "telemetry_schema_version": "canonical-telemetry-v2", "feature_schema_version": "ecu-window-features-v1",
            "feature_names": get_feature_names(SIGNALS), "signal_columns": SIGNALS,
            "window_geometry": {"size": 50, "stride": 10}, "artifacts": artifacts, "provenance": provenance}


def build():
    start = perf_counter()
    source_manifest, audit = verify_manifest()
    settings = Settings(data_dir=DATA, model_dir=DATA / "models")
    old_dir = DATA / "models/honda_keihin_71_17_v2"
    old_meta = json.loads((old_dir / "model_metadata.json").read_text())
    assert (settings.isolation_n_estimators, settings.isolation_contamination, settings.isolation_random_state) == (old_meta["n_estimators"], old_meta["contamination"], old_meta["hyperparameters"]["random_state"])
    assert (settings.window_size_samples, settings.window_step_samples) == (50, 10)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bundle_id = "native-full18-" + stamp
    directory = DATA / "models/bundles" / bundle_id
    directory.mkdir(parents=True, exist_ok=False)
    authorization = {"authorization": "FINAL MODEL PASS operator instruction: full corpus training and local activation approved",
        "approved_dataset_content_sha256": source_manifest["content_sha256"],
        "source_hashes": source_manifest["source_hashes"], "operator_attested_independent_captures": 18,
        "machine_verified_independent_captures": None, "created_at": datetime.now(UTC).isoformat(),
        "prior_manual_review_records": "unchanged; no manual reviews invented", "public_deployment_authorized": False}
    atomic_write_json(directory / "training_authorization.json", authorization)
    features, speed_frames, inventory = {}, {}, []
    for source in audit["sessions"]:
        sid, path = source["session_id"], ROOT / source["source_path"]
        assert sha256(path) == source["source_sha256"]
        imported = import_native_jsonl_file(path, session_id=sid)
        canonical = imported.canonical_session
        verify_mapping(source, canonical)
        assert all(s.frame_valid and s.checksum_valid and s.vehicle_speed_kmh is not None for s in canonical.samples)
        frames = canonical_session_to_frame_data(canonical)
        assert frames.relative_time_ms.is_monotonic_increasing and frames.relative_time_ms.diff().dropna().gt(0).all()
        frame = build_feature_frame(frames, settings, SIGNALS)
        assert len(frame) == source["mapping"]["windows"]
        assert len(get_feature_names(SIGNALS)) == 81
        features[sid] = frame
        speed_frames[sid] = speed_window_features(frames, frame)
        assert speed_frames[sid].speed_status.eq("ok").all()
        inventory.append({"session_id": sid, "source_sha256": source["source_sha256"],
            "physical_capture_group_id": source["physical_capture_group_id"], "sample_count": len(frames),
            "window_count": len(frame), "decoder_version": canonical.decoder_version_key,
            "duration_s": (frames.relative_time_ms.iloc[-1] - frames.relative_time_ms.iloc[0]) / 1000})
        print(json.dumps(inventory[-1]), flush=True)
    assert sum(r["sample_count"] for r in inventory) == 100688 and sum(r["window_count"] for r in inventory) == 9989
    provenance = {**authorization, "inventory": inventory, "training_window_count": 9989,
        "training_sample_count": 100688, "training_scope": "observed operating data, not independently verified healthy",
        "decoder_versions": sorted({r["decoder_version"] for r in inventory}),
        "annotation_usage": "approved label mapping integrity checked; native IF features and RPM/TPS context rules unchanged",
        "annotation_content_sha256": source_manifest["content_sha256"]}
    combined = pd.concat(features.values(), ignore_index=True)
    meta = train_isolation_forest(combined, sorted(features), settings, directory / "iforest", signal_columns=SIGNALS,
        training_decoder_versions=provenance["decoder_versions"], extra_metadata={"telemetry_schema_version": "canonical-telemetry-v2",
            "model_version_id": "iforest-full18-" + stamp, "version": "iforest-full18-" + stamp,
            "training_provenance": provenance, "training_scope": provenance["training_scope"]})
    battery = build_contextual_battery_reference(features, provenance=provenance, capture_balanced=True, min_context_captures=3)
    speed = build_speed_reference(speed_frames, provenance=provenance)
    atomic_write_json(directory / "battery_reference.json", battery)
    atomic_write_json(directory / "speed_reference.json", speed)
    manifest = manifest_for(bundle_id, {"if_model": artifact(directory / "iforest/isolation_forest.joblib"),
        "if_scaler": artifact(directory / "iforest/robust_scaler.joblib"), "if_metadata": artifact(directory / "iforest/model_metadata.json"),
        "battery": artifact(directory / "battery_reference.json"), "speed": artifact(directory / "speed_reference.json")}, provenance)
    atomic_write_json(directory / "manifest.json", manifest)
    rollback_path = DATA / "models/bundles/native-pre-full18/manifest.json"
    rollback = manifest_for("native-pre-full18", {"if_model": artifact(old_dir / "isolation_forest.joblib"),
        "if_scaler": artifact(old_dir / "robust_scaler.joblib"), "if_metadata": artifact(old_dir / "model_metadata.json"),
        "battery": artifact(DATA / "evaluation/h3/contextual_reference.json")}, {"purpose": "unchanged pre-refresh rollback"})
    if rollback_path.exists():
        assert json.loads(rollback_path.read_text()) == rollback
    else:
        atomic_write_json(rollback_path, rollback)
    load_bundle(settings, directory / "manifest.json")
    load_bundle(settings, rollback_path)
    atomic_write_json(OUTPUT / "build.json", {"bundle_manifest": str((directory / "manifest.json").relative_to(ROOT)),
        "rollback_manifest": str(rollback_path.relative_to(ROOT)), "model_version": meta["version"],
        "battery_version": battery["model_version"], "speed_version": speed["model_version"],
        "inventory": inventory, "elapsed_s": perf_counter() - start,
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024})
    return directory / "manifest.json"


def smoke(manifest_path):
    """All18 file uploads through a disposable repository, including retry and parity."""
    from app.main import create_app
    source_manifest, audit = verify_manifest()
    report, start = [], perf_counter()
    with tempfile.TemporaryDirectory(prefix="ecu-full18-bundle-smoke-") as temporary:
        data = Path(temporary) / "data"
        settings = Settings(data_dir=data, model_dir=data / "models")
        manifest = json.loads(manifest_path.read_text())
        for item in manifest["artifacts"].values():
            dest = data / item["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DATA / item["path"], dest)
        for relative in ("evaluation/h2/evaluation_manifest.json", "evaluation/h8/historical_observation_index.json"):
            # Optional history is copied unchanged when present, never rewritten.
            origin = DATA / relative
            if origin.exists():
                dest = data / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, dest)
        local_manifest = data / "models/smoke-manifest.json"
        atomic_write_json(local_manifest, manifest)
        activate_bundle(settings, local_manifest)
        application = create_app(settings)
        with TestClient(application) as client:
            for source in audit["sessions"]:
                began = perf_counter()
                sid, path = source["session_id"], ROOT / source["source_path"]
                with path.open("rb") as handle:
                    response = client.post("/api/v1/import/raw-session", files={"file": (path.name, handle, "application/jsonl")}, data={"session_id": sid, "device_id": "local-full18-smoke"})
                assert response.status_code == 200, response.text[:2000]
                result = response.json()
                repo = application.state.repository
                canonical = import_native_jsonl_file(path, session_id=result["session_id"]).canonical_session
                with path.open() as handle:
                    input_frame_count = sum(bool(line.strip()) for line in handle)
                assert input_frame_count == len(canonical.samples)
                count, samples = repo.read_telemetry_page(result["session_id"], len(canonical.samples) + 1, 0)
                assert samples == [s.model_dump(mode="json") for s in canonical.samples]
                expected = source["mapping"]["windows"]
                total, windows = repo.read_analysis_windows_page(result["analysis_run_id"], expected + 1, 0)
                assert total == len(windows) == expected == result["statistics"]["windows_generated"]
                assert len({(w["start_sequence"], w["end_sequence"]) for w in windows}) == total
                analysis = repo.read_analysis(result["analysis_run_id"])
                evidence = analysis["diagnostic_evidence"]
                detectors = evidence["detectors"]
                assert set(detectors) == {"isolation_forest", "contextual_battery_voltage", "contextual_speed"}
                assert all(d["execution_status"] in {"ok", "skipped"} for d in detectors.values())
                assert detectors["isolation_forest"]["scored_window_count"] == expected
                assert evidence["aggregate"]["window_summary"]["window_count"] == expected
                assert evidence["interpretation"]["status"] == "available"
                assert evidence["provenance"]["model_bundle"]["bundle_id"] == manifest["bundle_id"]
                if sid in {"session_000001", "session_000003", "session_000018"}:
                    atomic_write_json(OUTPUT / f"{sid}_analysis.json", analysis)
                feature_path = repo.analysis_root / result["analysis_run_id"] / "window_features.jsonl"
                features = [json.loads(line)["features_json"] for line in feature_path.read_text().splitlines()]
                assert len(features) == expected
                for i, row in enumerate(features):
                    first, last = canonical.samples[i * 10], canonical.samples[i * 10 + 49]
                    assert row["start_time_ms"] == first.timestamp_ms and row["end_time_ms"] == last.timestamp_ms
                    assert row["speed_evidence"]["start_time_ms"] == first.timestamp_ms
                    assert len(windows[i]["detector_results"]) == 3
                if sid == "session_000018":
                    direct = AnalysisService(settings).analyze(canonical, persist=False)
                    assert len(direct.windows) == 1793 and count == 17976
                    for column in ("decision_score", "prediction", "contextual_anomaly_score", "speed_anomaly_score"):
                        actual = direct.windows[column].tolist()
                        saved = [w[column] if column in w else f.get(column) for w, f in zip(windows, features, strict=True)]
                        pd.testing.assert_series_equal(pd.to_numeric(pd.Series(actual)), pd.to_numeric(pd.Series(saved)), check_names=False)
                    missing = canonical.model_copy(update={"samples": [s.model_copy(update={"vehicle_speed_kmh": None, "speed_provenance": {}}) for s in canonical.samples]})
                    no_speed = AnalysisService(settings).analyze(missing, persist=False)
                    assert no_speed.summary["diagnostic_evidence"]["detectors"]["contextual_speed"]["execution_status"] == "skipped"
                    assert no_speed.windows.decision_score.tolist() == direct.windows.decision_score.tolist()
                    with path.open("rb") as handle:
                        retry = client.post("/api/v1/import/raw-session", files={"file": (path.name, handle, "application/jsonl")}, data={"session_id": sid, "device_id": "local-full18-smoke"}).json()
                    assert retry["duplicate_reused"] and retry["analysis_run_id"] == result["analysis_run_id"]
                    atomic_write_json(OUTPUT / "session_000018_diagnostic_evidence.json", evidence)
                report.append({"session_id": sid, "source_sha256": sha256(path), "canonical_count": count,
                    "input_frame_count": input_frame_count,
                    "generated_windows": expected, "decoder_version": canonical.decoder_version_key,
                    "duration_s": (canonical.samples[-1].timestamp_ms - canonical.samples[0].timestamp_ms) / 1000,
                    "detectors": {k: {"status": d["execution_status"], "scored": d["scored_window_count"], "skipped": expected - d["scored_window_count"], "flagged": d["anomaly_window_count"]} for k, d in detectors.items()},
                    "history_status": evidence["history"]["status"], "upload_processing_s": result["processing_duration_seconds"],
                    "elapsed_s": perf_counter() - began, "errors": []})
                print(json.dumps(report[-1]), flush=True)
            assert len(application.state.repository.list_sessions(100)) == 18
    verification = {"sessions": report, "source_count": len(report), "canonical_count": sum(r["canonical_count"] for r in report),
        "window_count": sum(r["generated_windows"] for r in report), "all_canonical_values_upload_parity": True,
        "timestamp_window_identity_parity": True, "missing_speed_safe_skip": True, "long_retry_idempotent": True,
        "approved_dataset_content_sha256": source_manifest["content_sha256"], "elapsed_s": perf_counter() - start,
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, "status": "passed",
        "runtime_code_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in (ROOT / "app").rglob("*.py")}}
    atomic_write_json(OUTPUT / "corpus_smoke.json", verification)
    return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "smoke", "activate"))
    args = parser.parse_args()
    if args.command == "build":
        print(build())
    else:
        record = json.loads((OUTPUT / "build.json").read_text())
        manifest_path = ROOT / record["bundle_manifest"]
        if args.command == "smoke":
            smoke(manifest_path)
        else:
            result = json.loads((OUTPUT / "corpus_smoke.json").read_text())
            assert result["status"] == "passed" and result["window_count"] == 9989
            print(json.dumps(activate_bundle(Settings(data_dir=DATA, model_dir=DATA / "models"), manifest_path), indent=2))
