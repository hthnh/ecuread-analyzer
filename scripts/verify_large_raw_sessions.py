"""Bounded P0 regressions against actual active artifacts, with disposable writes only.

Each size runs in a fresh process so peak RSS is attributable to that case.
The optional DriveSafe check uses a new loopback server and in-memory SQLite.
No request is sent to a deployed application.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
from time import perf_counter, sleep
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
COUNTS = (5000, 5001, 9917, 12625, 17976)
SOURCE = ROOT / "real_data/fulldata_afterclean/session_000018.jsonl"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def copy_active_artifacts(destination):
    source = ROOT / "data"
    pointer_path = source / "models/active_bundle.json"
    pointer = json.loads(pointer_path.read_text())
    manifest = json.loads((source / pointer["manifest"]).read_text())
    paths = [Path("models/active_bundle.json"), Path(pointer["manifest"])]
    paths += [Path(item["path"]) for item in manifest["artifacts"].values()]
    paths += [Path("evaluation/h2/evaluation_manifest.json"), Path("evaluation/h8/historical_observation_index.json")]
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    return {str(source / path): digest(source / path) for path in paths}


def worker(count, directory):
    sys.path.insert(0, str(ROOT))
    data = directory / "data"
    protected = copy_active_artifacts(data)
    protected[str(SOURCE)] = digest(SOURCE)
    os.environ.update(DATA_DIR=str(data), MODEL_DIR=str(data / "models"))
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app

    source_rows = [json.loads(line) for line in SOURCE.read_text().splitlines()][:count]
    assert len(source_rows) == count
    records = [{"sequence": i, "timestamp_ms": row["elapsed_ms"], "raw_hex": row["raw_hex"]} for i, row in enumerate(source_rows)]
    payload = {"session_id": f"disposable-p0-{count}", "device_id": "p0-verifier", "raw_representation": "native24_table17",
               "sampling": {"sample_interval_ms": 250}, "process_with_model": True, "idempotency_key": f"p0-{count}-attempt-1", "records": records}
    before = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    settings = Settings(data_dir=data, model_dir=data / "models", window_size_samples=50, window_step_samples=10)
    app = create_app(settings)
    with TestClient(app) as client:
        started = perf_counter()
        response = client.post("/api/v1/raw/decode-analyze", json=payload)
        elapsed = perf_counter() - started
        assert response.status_code == 200, response.text[:1500]
        body = response.json()
        summary = body["analysis"]
        canonical = body["canonical_session"]["samples"]
        assert len(canonical) == count
        persisted_count, persisted = app.state.repository.read_telemetry_page(payload["session_id"], count, 0)
        assert persisted_count == count
        for record, sample, stored in zip(records, canonical, persisted, strict=True):
            assert sample == {k: v for k, v in stored.items() if v is not None}
            assert sample["sequence"] == record["sequence"]
            assert sample["timestamp_ms"] == record["timestamp_ms"]
            raw = bytes.fromhex(record["raw_hex"])
            for key, expected in {"rpm": (raw[4] << 8) | raw[5], "tps_voltage": raw[6] * 5 / 256,
                                  "tps_raw": raw[7], "battery_voltage": raw[14] / 10,
                                  "iat_c": raw[11] - 40, "ect_c": raw[13] - 40, "vehicle_speed_kmh": raw[19]}.items():
                assert sample[key] == expected, (record["sequence"], key)
            assert sample["speed_provenance"]["source_byte"] == 19
            assert sample["frame_valid"] and sample["checksum_valid"]
        run_id = summary["analysis_run_id"]
        run = app.state.repository.read_analysis(run_id)
        window_count, windows = app.state.repository.read_analysis_windows_page(run_id, count, 0)
        expected_bounds = [(i, i + 49) for i in range(0, count - 49, 10)]
        assert window_count == summary["window_count"] == len(expected_bounds)
        assert [(w["start_sequence"], w["end_sequence"]) for w in windows] == expected_bounds
        for boundary in (5000, 10000, 15000):
            if boundary + 49 < count:
                assert any(start < boundary <= end for start, end in expected_bounds)
        evidence = summary["diagnostic_evidence"]
        assert run["diagnostic_evidence"] == evidence
        assert run["model_bundle"] == summary["model_bundle"]
        assert summary["model_bundle"]["bundle_id"] == json.loads((data / "models/active_bundle.json").read_text())["bundle_id"]
        assert set(evidence["detectors"]) == {"isolation_forest", "contextual_battery_voltage", "contextual_speed"}
        for detector in evidence["detectors"].values():
            assert detector["execution_status"] in {"ok", "skipped"}, detector
            assert detector["model_version"]
            if detector["execution_status"] == "skipped":
                assert detector["skip_or_failure_reason"]
        assert all(len(w["detector_results"]) == 3 for w in windows)
        assert evidence["aggregate"]["window_summary"]["window_count"] == window_count
        assert evidence["interpretation"]["status"] == "available"
        assert evidence["interpretation"]["observations"]
        assert evidence["interpretation"]["recommended_checks"]
        primary_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        retry_started = perf_counter()
        retry = client.post("/api/v1/raw/decode-analyze", json=payload)
        retry_elapsed = perf_counter() - retry_started
        assert retry.status_code == 200 and retry.json() == body
        assert len(app.state.repository.list_analyses_for_session(payload["session_id"])) == 1
    assert hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() == before
    assert all(digest(path) == value for path, value in protected.items())
    (directory / "response.json").write_text(json.dumps(body))
    result = {"records": count, "windows": window_count, "first_window": expected_bounds[0], "last_window": expected_bounds[-1],
              "tail_samples": count - expected_bounds[-1][1] - 1, "http_seconds": round(elapsed, 3),
              "peak_worker_rss_mib": round(primary_peak, 2), "retry_seconds": round(retry_elapsed, 3),
              "request_bytes": len(response.request.content), "response_bytes": len(response.content),
              "analysis_run_id": run_id, "model_version": summary["model_version"], "bundle_id": summary["model_bundle"]["bundle_id"],
              "detectors": {k: {field: d.get(field) for field in ("execution_status", "model_version", "scored_window_count", "anomaly_window_count", "skip_or_failure_reason")} for k, d in evidence["detectors"].items()},
              "interpretation_counts": {k: len(evidence["interpretation"][k]) for k in ("observations", "symptoms", "possible_causes", "recommended_checks")},
              "retry_same_run": True, "protected_inputs_unchanged": True, "status": "passed"}
    return result


def drivesafe_round_trip(directory):
    data = directory / "data"
    protected = copy_active_artifacts(data)
    input_copy = directory / "original.jsonl"
    shutil.copyfile(SOURCE, input_copy)
    protected[str(SOURCE)] = digest(SOURCE)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "DATA_DIR": str(data), "MODEL_DIR": str(data / "models"), "PYTHONDONTWRITEBYTECODE": "1"}
    with (directory / "server.log").open("w") as log:
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError((directory / "server.log").read_text())
                try:
                    urllib.request.urlopen(base + "/health", timeout=1).read()
                    break
                except OSError:
                    sleep(0.1)
            else:
                raise RuntimeError("Disposable Analyzer did not start")
            consumer = ROOT.parent / "drisafe"
            output = directory / "consumer_result.json"
            subprocess.run([str(consumer / ".venv/bin/python"), str(consumer / "scripts/verify_large_session_evidence.py"),
                            "--analyzer-url", base, "--input", str(input_copy), "--output", str(output)], cwd=consumer, check=True, timeout=120)
            result = json.loads(output.read_text())
            run = json.loads((data / "analyses" / result["analysis_run_id"] / "analysis_run.json").read_text())
            assert run["diagnostic_evidence"] == result.pop("diagnostic_evidence")
            assert run["total_window_count"] == 1258
            assert digest(input_copy) == digest(SOURCE)
            assert all(digest(path) == value for path, value in protected.items())
            result["analyzer_persistence_matches_cloud"] = True
            return result
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-count", type=int, choices=COUNTS)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--counts", nargs="+", type=int, choices=COUNTS, default=COUNTS)
    parser.add_argument("--with-drivesafe", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/integration/P0_LARGE_SESSION_REGRESSION_2026_10_23.json")
    args = parser.parse_args()
    if args.worker_count:
        print(json.dumps(worker(args.worker_count, args.directory)))
        return
    report = {"scope": "Fresh disposable workers, copied active artifacts, no training or deployed requests", "source_sha256": digest(SOURCE),
              "limits": {"records": 20000, "body_bytes": 8388608, "body_seconds": 10, "analysis_stage_budget_seconds": 45, "concurrent_raw_requests_per_data_volume": 1}, "cases": []}
    code_paths = [ROOT / path for path in ("app/api/raw.py", "app/config.py", "app/services/analysis_service.py", "scripts/verify_large_raw_sessions.py")]
    code_paths += [ROOT.parent / "drisafe" / path for path in ("services/analysis.py", "services/analysis_submission.py", "integrations/analyzer_client.py", "services/demo_data.py", "templates/session_detail.html", "scripts/verify_large_session_evidence.py")]
    report["code_sha256"] = {str(path): digest(path) for path in code_paths}
    with tempfile.TemporaryDirectory(prefix="ecuread-p0-") as temp:
        root = Path(temp)
        for count in args.counts:
            directory = root / str(count)
            directory.mkdir()
            child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker-count", str(count), "--directory", str(directory)], cwd=ROOT, capture_output=True, text=True, timeout=120)
            if child.returncode:
                raise RuntimeError(child.stderr[-6000:] + child.stdout[-1000:])
            result = json.loads(child.stdout.splitlines()[-1])
            report["cases"].append(result)
            print(f"{count}: {result['http_seconds']}s, {result['peak_worker_rss_mib']} MiB, {result['windows']} windows", flush=True)
        if args.with_drivesafe:
            directory = root / "integration"
            directory.mkdir()
            report["drivesafe"] = drivesafe_round_trip(directory)
        report["status"] = "passed"
        assert all(digest(path) == value for path, value in report["code_sha256"].items()), "Implementation changed during verification"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
