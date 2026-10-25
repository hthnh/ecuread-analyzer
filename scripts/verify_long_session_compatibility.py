"""Run real, unsplit RAW through existing analyzers in disposable storage.

No training, annotation acceptance, campaign edits, or production configuration
changes are performed. HTTP probes use the actual ASGI routes, not a live proxy.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import sys
import tempfile
import traceback
from collections import Counter
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.config import Settings
from app.context_annotation.auto_labeler import suggest_context
from app.context_annotation.session_loader import discover_sources, load_source
from app.context_annotation.speed import SpeedResolver
from app.context_annotation.storage import atomic_write_json
from app.ecu.profiles.honda_keihin_71_17 import decode_production_values, normalize_frame, validate_normalized_frame
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.main import create_app


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_window_bounds(count: int, size: int, step: int) -> list[tuple[int, int]]:
    if size <= 0 or step <= 0:
        raise ValueError("window size and step must be positive")
    return [(start, start + size - 1) for start in range(0, count - size + 1, step)]


def check(report: dict, name: str, passed: bool, **details: Any) -> None:
    report.setdefault("checks", []).append({"check": name, "passed": bool(passed), **details})


def isolated_settings(data_dir: Path, model_dir: Path, defaults: Settings) -> Settings:
    settings = replace(defaults, data_dir=data_dir, model_dir=model_dir)
    # Only the existing research references are shared; all writable artifacts
    # (entities, telemetry, analyses, uploads) remain in the temporary directory.
    for family, filename in (
        ("h3", "contextual_reference.json"),
        ("h8", "historical_observation_index.json"),
        ("h2", "evaluation_manifest.json"),
    ):
        destination = data_dir / "evaluation" / family / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(ROOT / "data/evaluation" / family / filename)
    return settings


def pipeline_checks(report: dict, summary: dict, expected_windows: int) -> None:
    evidence = summary.get("diagnostic_evidence", {})
    components = evidence.get("capability_status", {}).get("component_status", {})
    detectors = evidence.get("detectors", {})
    forest = next((d for d in summary.get("detectors", []) if d["detector_id"] == "isolation_forest"), {})
    check(report, "all_applicable_windows_generated", summary.get("window_count") == expected_windows)
    check(report, "isolation_forest_completed", forest.get("status") == "ok"
          and forest.get("scored_window_count") == expected_windows)
    contextual = detectors.get("contextual_battery_voltage", {})
    check(report, "contextual_battery_completed_or_not_applicable",
          contextual.get("execution_status") == "ok"
          or (contextual.get("execution_status") == "skipped"
              and contextual.get("skip_or_failure_reason") == "no_applicable_context"))
    contextual_counts = contextual.get("evidence", {}).get("metadata", {})
    check(report, "every_contextual_window_accounted_for",
          contextual_counts.get("applicable_window_count", 0)
          + contextual_counts.get("skipped_window_count", 0) == expected_windows)
    for component in ("evidence_aggregation", "rca_v2", "historical_evidence"):
        check(report, component + "_completed", components.get(component) == "available")
    check(report, "all_windows_aggregated", evidence.get("aggregate", {}).get("window_summary", {}).get("window_count") == expected_windows)


def run_native(report: dict, source: Path, records: list[dict], settings: Settings) -> None:
    total_started = perf_counter()
    started = perf_counter()
    imported = import_native_jsonl_file(source, session_id=source.stem)
    canonical = imported.canonical_session
    canonical_times = [s.timestamp_ms for s in canonical.samples]
    raw_times = [row["elapsed_ms"] for row in records]
    duration = (raw_times[-1] - raw_times[0]) / 1000.0
    check(report, "entire_file_ingested", len(canonical.samples) == len(records) > 5000)
    check(report, "canonical_count_matches_valid_raw", len(canonical.samples) == report["input"]["valid_raw_frames"])
    check(report, "all_canonical_samples_ml_eligible", all(s.frame_valid and s.checksum_valid for s in canonical.samples))
    check(report, "timestamps_and_order_preserved", canonical_times == raw_times
          and all(b > a for a, b in zip(canonical_times, canonical_times[1:])))
    report["ingestion"] = {"canonical_samples": len(canonical.samples), "duration_s": duration,
                           "statistics": imported.statistics, "seconds": perf_counter() - started}
    expected = expected_window_bounds(len(records), settings.window_size_samples, settings.window_step_samples)
    application = create_app(settings)
    with TestClient(application) as client:
        started = perf_counter()
        payload = canonical.model_dump(mode="json")
        report["canonical_request_bytes"] = len(json.dumps(payload, separators=(",", ":")).encode())
        response = client.post("/api/v1/analysis", json=payload)
        report["analysis_http_status"] = response.status_code
        report["analysis_seconds"] = perf_counter() - started
        if response.status_code != 200:
            raise RuntimeError(f"full canonical analysis: {response.status_code} {response.text[:1000]}")
        summary = response.json()
        report["analysis"] = summary
        pipeline_checks(report, summary, len(expected))
        repository = application.state.repository
        count, windows = repository.read_analysis_windows_page(summary["analysis_run_id"], len(expected) + 1, 0)
        actual = [(int(w["start_sequence"]), int(w["end_sequence"])) for w in windows]
        check(report, "exact_window_boundaries_persisted", count == len(expected) and actual == expected)
        check(report, "every_window_has_if_score", len(windows) == len(expected)
              and all(w.get("decision_score") is not None and w.get("prediction") is not None for w in windows))
        # Read every page, including the final partial page. A page limit is not
        # an ingestion limit and must not hide samples from the smoke assertion.
        persisted = []
        page_counts = []
        for offset in range(0, len(records), 5000):
            page = client.get(f"/api/v1/sessions/{canonical.session_id}/telemetry", params={"limit": 5000, "offset": offset})
            page.raise_for_status()
            body = page.json()
            page_counts.append(len(body["records"]))
            persisted.extend(body["records"])
            check(report, f"telemetry_total_at_offset_{offset}", body["count"] == len(records))
        check(report, "all_canonical_fields_survive_persistence", persisted == payload["samples"])
        check(report, "one_unsplit_session_one_analysis", len(repository.list_sessions(200)) == 1
              and len(repository.list_analyses_for_session(canonical.session_id)) == 1)
        report["windows"] = {"expected": len(expected), "generated": summary["window_count"],
                             "first_bounds": actual[0] if actual else None, "last_bounds": actual[-1] if actual else None,
                             "unwindowed_tail_samples": len(records) - expected[-1][1] - 1 if expected else len(records),
                             "tail_policy": "Full windows only, unchanged stride; tail samples retained in canonical and CTX2.",
                             "telemetry_page_counts": page_counts}
        feature_path = repository.analysis_root / summary["analysis_run_id"] / "window_features.jsonl"
        with feature_path.open(encoding="utf-8") as handle:
            features = [json.loads(line)["features_json"] for line in handle if line.strip()]
        check(report, "every_window_feature_record_persisted", len(features) == len(expected))
        contextual_statuses = dict(Counter(row.get("contextual_status", "missing") for row in features))
        report["scoring"] = {"isolation_forest": {
            "scored": summary["detectors"][0]["scored_window_count"],
            "skipped": len(expected) - summary["detectors"][0]["scored_window_count"],
        }, "contextual_battery": {"window_status_counts": contextual_statuses}}

        started = perf_counter()
        loaded = discover_sources(settings.data_dir / "telemetry")[0]
        mapping_path = settings.data_dir / "smoke_raw_sources.json"
        atomic_write_json(mapping_path, {"sessions": [{
            "session_id": loaded.source_id, "canonical_sha256": loaded.sha256,
            "raw_source_path": str(source), "raw_source_sha256": report["input"]["sha256"],
            "sample_offset": 0, "sample_count": len(records), "timestamp_field": "elapsed_ms",
        }]})
        context = load_source(loaded, settings)
        speeds, provenance = SpeedResolver(mapping_path).resolve(context)
        suggestions = suggest_context(context, speeds=speeds, speed_provenance=provenance)
        check(report, "ctx2_full_session_and_speed_alignment", context.samples == persisted
              and provenance["available_count"] == len(records)
              and provenance.get("alignment") == "full_signal_sequence_and_timestamp_intervals")
        segments = suggestions.segments
        check(report, "ctx2_full_duration_and_no_artificial_breaks",
              suggestions.metrics["duration_s"] == duration
              and suggestions.timestamp_integrity["capture_gaps"] == []
              and bool(segments) and segments[0].start_s == 0 and segments[-1].end_s == duration
              and all(abs(a.end_s - b.start_s) < 1e-6 for a, b in zip(segments, segments[1:]))
              and abs(sum(s.end_s - s.start_s for s in segments) - duration) < 1e-6)
        check(report, "ctx2_no_automatic_acceptance", all(s.review_status == "pending" for s in [*segments, *suggestions.events]))
        report["ctx2"] = {"samples": len(context.samples), "speed_provenance": provenance,
                          "metrics": suggestions.metrics, "timestamp_integrity": suggestions.timestamp_integrity,
                          "last_segment": segments[-1].model_dump(mode="json") if segments else None,
                          "seconds": perf_counter() - started, "suggestions_saved": False}
    report["total_processing_seconds"] = perf_counter() - total_started


def probe_raw_api(source: Path, records: list[dict], settings: Settings) -> dict:
    application = create_app(settings)
    payload = {"session_id": source.stem, "raw_representation": "native24_table17", "sampling": {},
               "records": [{"sequence": i, "timestamp_ms": row["elapsed_ms"], "raw_hex": row["raw_hex"]}
                           for i, row in enumerate(records)]}
    started = perf_counter()
    with TestClient(application) as client:
        response = client.post("/api/v1/raw/decode-analyze", json=payload)
        errors = response.json().get("detail", [])
        return {"http_status": response.status_code, "records_submitted": len(records),
                "request_bytes": len(json.dumps(payload, separators=(",", ":")).encode()),
                "errors": [{k: error[k] for k in ("type", "loc", "msg", "ctx") if k in error}
                           for error in errors] if isinstance(errors, list) else errors,
                "session_count": len(application.state.repository.list_sessions(200)),
                "seconds": perf_counter() - started,
                "verdict": "PASS" if response.status_code == 200 else "FAIL"}


def probe_upload(source: Path, settings: Settings, native_samples: int) -> dict:
    application = create_app(settings)
    data = {"device_id": "isolated-long-session-smoke", "session_id": source.stem}
    started = perf_counter()
    with TestClient(application) as client:
        with source.open("rb") as handle:
            response = client.post("/api/v1/sessions", files={"file": (source.name, handle, "application/jsonl")}, data=data)
        body = response.json()
        result = {"http_status": response.status_code, "seconds": perf_counter() - started}
        if response.status_code != 200:
            result.update({"error": body, "verdict": "FAIL"})
            return result
        repository = application.state.repository
        session_id = body["session_id"]
        session = repository.read_session(session_id)
        total, samples = repository.read_telemetry_page(session_id, native_samples + 1, 0)
        differences: Counter[str] = Counter()
        example = None
        aliases = {"tps_raw": "tps_raw_candidate", "ect_c": "ect_c_candidate"}
        with source.open(encoding="utf-8") as handle:
            raw_records = [json.loads(line) for line in handle if line.strip()]
        for record, sample in zip(raw_records, samples):
            native, _ = normalize_frame(list(bytes.fromhex(record["raw_hex"])))
            decoded = decode_production_values(native)
            mismatch = {field: {"native_v2": decoded[field], "upload": sample.get(aliases.get(field, field))}
                        for field in ("rpm", "tps_voltage", "tps_raw", "battery_voltage", "iat_c", "ect_c")
                        if decoded[field] != sample.get(aliases.get(field, field))}
            differences.update(mismatch.keys())
            if mismatch and example is None:
                example = {"sequence": sample["sequence"], "differences": mismatch}
        with source.open("rb") as handle:
            retry = client.post("/api/v1/sessions", files={"file": (source.name, handle, "application/jsonl")}, data=data)
        retried = retry.json()
        result.update({"statistics": body["statistics"], "result": body["result"],
                       "component_status": body["diagnostic_evidence"]["capability_status"]["component_status"],
                       "detectors": body["diagnostic_evidence"]["detectors"],
                       "signal_difference_sample_counts_vs_native_v2": dict(differences),
                       "first_signal_difference": example,
                       "telemetry_schema_version": session["telemetry_schema_version"],
                       "canonical_samples": total, "last_timestamp_ms": samples[-1]["timestamp_ms"] if samples else None,
                       "raw_hash_preserved": sha256(Path(body["artifacts"]["raw"])) == sha256(source),
                       "session_count_after_retry": len(repository.list_sessions(200)),
                       "analysis_count_after_retry": len(repository.list_analyses_for_session(session_id)),
                       "retry_http_status": retry.status_code,
                       "retry_reused_session_and_analysis": retried.get("duplicate_reused") is True
                       and retried.get("session_id") == session_id and retried.get("analysis_run_id") == body["analysis_run_id"],
                       "native_v2_contract": session["telemetry_schema_version"] == "canonical-telemetry-v2",
                       "verdict": "PASS" if total == native_samples and session["telemetry_schema_version"] == "canonical-telemetry-v2"
                       and body["diagnostic_evidence"]["capability_status"]["component_status"].get("isolation_forest") == "ok" else "FAIL"})
        return result


def run(source: Path, output: Path) -> dict:
    defaults = Settings()
    model_root = defaults.model_dir.resolve()
    v2_model = model_root / "honda_keihin_71_17_v2"
    protected = {source, *model_root.glob("*.joblib"), *model_root.glob("*.json"),
                 *v2_model.glob("*.joblib"), *v2_model.glob("*.json"),
                 ROOT / "app/config.py", ROOT / "app/ml/inference.py", ROOT / "app/ml/windowing.py",
                 ROOT / "app/ml/contextual_detector.py", ROOT / "app/context_annotation/auto_labeler.py",
                 ROOT / "data/evaluation/h3/contextual_reference.json",
                 ROOT / "data/evaluation/h8/historical_observation_index.json",
                 ROOT / "data/evaluation/h2/evaluation_manifest.json"}
    campaign = ROOT / "data/context_annotation"
    protected.add(ROOT / "ecu_raw_18_manifest.json")
    protected.update(campaign.glob("*.json"))
    protected.update((campaign / "annotations").glob("*.json"))
    if output in protected or output.is_relative_to(model_root) or output.is_relative_to(campaign):
        raise ValueError("smoke output must not overwrite a source, model, reference, or annotation campaign")
    before = {str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path): sha256(path) for path in protected if path.is_file()}
    report: dict[str, Any] = {"schema_version": "long-session-compatibility-smoke-v1", "created_at": datetime.now(UTC).isoformat(),
                              "environment": {"python": sys.version, "platform": platform.platform(),
                                              "packages": {name: importlib.metadata.version(name) for name in ("fastapi", "pydantic", "pandas", "numpy", "scikit-learn")},
                                              "settings": {k: str(v) if isinstance(v, Path) else v for k, v in asdict(defaults).items()},
                                              "transport": "FastAPI TestClient (ASGI); deployed proxy/network not tested"},
                              "protected_sha256_before": before, "checks": []}
    suite_started = perf_counter()
    with source.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    valid = 0
    for row in records:
        normalized, representation = normalize_frame(list(bytes.fromhex(row["raw_hex"])))
        valid += int(validate_normalized_frame(normalized, source_format=representation).parse_valid)
    report["input"] = {"path": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
                       "bytes": source.stat().st_size, "sha256": sha256(source), "records": len(records),
                       "valid_raw_frames": valid, "first_timestamp_ms": records[0]["elapsed_ms"],
                       "last_timestamp_ms": records[-1]["elapsed_ms"]}
    candidate_manifest = ROOT / "ecu_raw_18_manifest.json"
    if candidate_manifest.is_file():
        candidate = json.loads(candidate_manifest.read_text(encoding="utf-8"))
        entry = next((entry for entry in candidate.get("source_entries", []) if entry.get("source_session_id") == source.stem), None)
        report["input"]["capture_manifest_entry"] = entry
        report["input"]["capture_independence"] = candidate.get("capture_independence")
        if entry:
            check(report, "candidate_capture_hash_matches", entry["sha256"] == report["input"]["sha256"])
    inventory = [{"filename": path.name, "sha256": sha256(path)} for path in sorted(source.parent.glob("*.jsonl"))]
    report["source_folder_hash_audit"] = {"files": len(inventory), "sources": inventory,
                                           "duplicate_hashes": {digest: [row["filename"] for row in inventory if row["sha256"] == digest]
                                                                for digest, count in Counter(row["sha256"] for row in inventory).items() if count > 1},
                                           "capture_independence_inferred": False}
    report["limits"] = {
        "raw_json_records_per_request": {"limit": 5000, "behavior": "422 rejection, not truncation", "tested_below": "raw_json_api"},
        "file_upload_bytes": {"limit": defaults.max_upload_bytes, "input_bytes": source.stat().st_size,
                              "within_limit": source.stat().st_size <= defaults.max_upload_bytes,
                              "behavior": "413 above limit; upload is fully read into memory before this check", "oversize_probe_executed": False},
        "canonical_analysis_request": {"record_cap": None, "app_body_byte_cap": None, "tested_records": len(records)},
        "telemetry_page_size": {"limit": 5000, "behavior": "Read pagination only; all pages checked"},
        "inference_batch": {"batch_rows": len(expected_window_bounds(len(records), defaults.window_size_samples, defaults.window_step_samples)),
                            "app_window_cap": None, "all_windows_submitted_together": True},
        "ml_windows": {"size_samples": defaults.window_size_samples, "stride_samples": defaults.window_step_samples,
                       "partial_windows_padded": False, "quality_thresholds_unchanged": True},
        "representative_evidence": {"representatives_per_event_type": 5, "response_event_items": 15,
                                    "scope": "RCA uses representative events; aggregate uses every generated window"},
        "runtime": {"app_analysis_timeout_seconds": None, "deployed_proxy_timeout_tested": False,
                    "process_cpu_rlimit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)), "unlimited_rlimit_value": resource.RLIM_INFINITY},
        "memory": {"streaming": False, "process_address_space_rlimit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
                   "scope": "In-memory parse, canonical payload, feature frame, inference and pagination. No memory limit in repository compose file; external deployment limits not exercised."},
    }
    with tempfile.TemporaryDirectory(prefix="ecu-long-session-") as temporary:
        temporary_root = Path(temporary)
        try:
            run_native(report, source, records, isolated_settings(temporary_root / "native", v2_model, defaults))
        except Exception as exc:
            report["native_error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
            check(report, "native_pipeline_completed", False)
        report["native_pipeline_verdict"] = "PASS" if report["checks"] and all(c["passed"] for c in report["checks"]) else "FAIL"
        report["native_peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        report["native_peak_memory_scope"] = "Linux process high-water RSS through native import, HTTP analysis/pagination, persisted-canonical CTX2; includes interpreter/imports; excludes later probes."
        for name, probe, arguments in (
            ("raw_json_api", probe_raw_api, (source, records, isolated_settings(temporary_root / "raw_api", model_root, defaults))),
            ("file_upload_api", probe_upload, (source, isolated_settings(temporary_root / "upload", model_root, defaults), len(records))),
        ):
            try:
                report[name] = probe(*arguments)
            except Exception as exc:
                report[name] = {"verdict": "FAIL", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    after = {key: sha256(ROOT / key) for key in before}
    report["protected_inputs_unchanged"] = before == after
    report["protected_sha256_after"] = after
    report["suite_seconds"] = perf_counter() - suite_started
    report["suite_peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    report["verdict"] = "PASS" if report["native_pipeline_verdict"] == "PASS" and report["protected_inputs_unchanged"] and all(
        report[key]["verdict"] == "PASS" for key in ("raw_json_api", "file_upload_api")) else "FAIL"
    report["verdict_scope"] = "Unqualified original-RAW compatibility across native analysis, RAW JSON API, and file-upload API; individual path verdicts reported separately."
    atomic_write_json(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "real_data/fulldata_afterclean/session_000018.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/long_session_compatibility/smoke_report.json")
    args = parser.parse_args()
    report = run(args.input.resolve(), args.output.resolve())
    print(json.dumps({"verdict": report["verdict"], "native_pipeline_verdict": report["native_pipeline_verdict"],
                      "input": report["input"], "windows": report.get("windows"),
                      "total_processing_seconds": report.get("total_processing_seconds"),
                      "native_peak_rss_mib": report["native_peak_rss_mib"],
                      "failed_checks": [c for c in report["checks"] if not c["passed"]],
                      "native_error": report.get("native_error"),
                      "raw_json_api": {k: v for k, v in report["raw_json_api"].items() if k != "traceback"},
                      "file_upload_api": report["file_upload_api"], "report": str(args.output)}, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
