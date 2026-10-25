"""Bounded loopback HTTP/resource probe; never touches production storage."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from app.config import Settings
from app.context_annotation.storage import atomic_write_json, utc_now_iso
from app.main import create_app
from scripts.verify_long_session_compatibility import isolated_settings


def process_memory(pid: int) -> dict:
    fields = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(("VmHWM:", "VmRSS:")):
            fields[line.split(":")[0]] = int(line.split()[1]) / 1024
    return fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.serve:
        import uvicorn
        settings = isolated_settings(args.serve, ROOT / "data/models", Settings())
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="error")
        return
    report = {"schema_version": "raw1-bounded-resource-probe-v1", "created_at": utc_now_iso(),
              "scope": "One loopback single-worker server; one full 17976-frame upload, two concurrent tiny uploads, two concurrent retries, one 25MiB+1 guard request. Not production capacity certification.",
              "deployment_limits_changed": False, "upload_limit_mib": Settings().max_upload_mb,
              "window_size_samples": Settings().window_size_samples, "window_step_samples": Settings().window_step_samples}
    with tempfile.TemporaryDirectory(prefix="raw1-resource-") as tmp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", tmp, "--port", str(port)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(120):
                assert server.poll() is None, "isolated server exited"
                try:
                    if httpx.get(base + "/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                sleep(.25)
            else:
                raise AssertionError("server did not become ready")

            def post(sid):
                source = ROOT / "real_data/fulldata_afterclean" / f"{sid}.jsonl"
                started = perf_counter()
                with source.open("rb") as handle:
                    response = httpx.post(base + "/api/v1/sessions", files={"file": (source.name, handle, "application/jsonl")},
                                          data={"device_id": "raw1-resource-probe", "session_id": sid}, timeout=180)
                assert response.status_code == 200, response.text[:500]
                return response.json(), perf_counter() - started

            result, elapsed = post("session_000018")
            assert result["statistics"]["frames_parsed"] == 17976
            assert result["statistics"]["windows_generated"] == 1793
            report["full_upload"] = {"input_frames": 17976, "canonical_samples": 17976, "generated_windows": 1793,
                                     "http_seconds": elapsed, "peak_rss_mib": process_memory(server.pid)["VmHWM"],
                                     "detectors": result["diagnostic_evidence"]["detectors"]}
            started = perf_counter()
            with ThreadPoolExecutor(max_workers=2) as pool:
                small = list(pool.map(post, ["session_000001", "session_000020"]))
            assert len({item[0]["session_id"] for item in small}) == 2
            assert [item[0]["statistics"]["frames_parsed"] for item in small] == [117, 87]
            report["bounded_concurrency"] = {"simultaneous_clients": 2, "input_frames": [117, 87],
                                             "seconds": perf_counter() - started, "separate_complete_sessions": True,
                                             "large_concurrent_uploads_tested": False}
            with ThreadPoolExecutor(max_workers=2) as pool:
                retries = list(pool.map(post, ["session_000018", "session_000018"]))
            assert all(item[0]["duplicate_reused"] and item[0]["analysis_run_id"] == result["analysis_run_id"] for item in retries)
            session_root = Path(tmp) / "entities/sessions"
            sessions = list(session_root.glob("*.json"))
            assert len(sessions) == 3
            report["concurrent_identical_retries"] = {"reused_original": True, "total_sessions": 3}
            oversized = httpx.post(base + "/api/v1/sessions",
                                   files={"file": ("oversized.jsonl", b"x" * (Settings().max_upload_bytes + 1), "application/jsonl")}, timeout=30)
            assert oversized.status_code == 413, oversized.text[:500]
            assert len(list(session_root.glob("*.json"))) == 3
            report["byte_guard"] = {"file_bytes": Settings().max_upload_bytes + 1, "status_code": 413, "no_session_created": True}
            report["final_peak_rss_mib"] = process_memory(server.pid)["VmHWM"]
            report["verdict"] = "PASS"
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    atomic_write_json(ROOT / "data/research/raw1_upload/resources.json", report)
    print(json.dumps({key: value for key, value in report.items() if key != "full_upload"}, indent=2))
    print(json.dumps({key: value for key, value in report["full_upload"].items() if key != "detectors"}, indent=2))


if __name__ == "__main__":
    main()
