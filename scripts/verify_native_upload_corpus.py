"""Real-model RAW1 regression, using one fresh process per original file."""
from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.config import Settings
from app.context_annotation.models import SessionSource
from app.context_annotation.session_loader import load_source
from app.context_annotation.speed import SpeedResolver
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.main import create_app
from app.services.analysis_service import AnalysisService
from scripts.verify_long_session_compatibility import expected_window_bounds, isolated_settings, pipeline_checks


def worker(source: Path, output: Path) -> dict:
    started = perf_counter()
    report = {"session_id": source.stem, "source_path": str(source), "source_sha256": file_sha256(source), "checks": [], "error": None}
    try:
        records = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
        expected = import_native_jsonl_file(source).canonical_session
        report.update(input_frames=len(records), canonical_count=len(expected.samples), duration_s=(records[-1]["elapsed_ms"] - records[0]["elapsed_ms"]) / 1000,
                      decoder_id=expected.decoder_id, decoder_version=expected.decoder_version)
        with tempfile.TemporaryDirectory(prefix="raw1-upload-") as directory:
            settings = isolated_settings(Path(directory), ROOT / "data/models", Settings())
            app = create_app(settings)
            route = "/api/v1/import/raw-session" if int(source.stem[-6:]) % 2 else "/api/v1/sessions"
            with TestClient(app) as client:
                def post():
                    with source.open("rb") as handle:
                        return client.post(route, files={"file": (source.name, handle, "application/jsonl")},
                                           data={"device_id": "raw1-isolated-smoke", "session_id": source.stem})
                response = post()
                assert response.status_code == 200, response.text[:1000]
                result = response.json()
                repository = app.state.repository
                session_id = result["session_id"]
                count, samples = repository.read_telemetry_page(session_id, len(records) + 1, 0)
                assert count == len(samples) == len(records)
                assert samples == [sample.model_dump(mode="json") for sample in expected.samples], "decoded sample / timestamp / sequence / quality flag parity failure"
                report["canonical_parity_all_fields"] = True
                entity = repository.read_session(session_id)
                assert entity["raw_frame_count"] == len(records)
                assert entity["telemetry_schema_version"] == "canonical-telemetry-v2"
                bounds = expected_window_bounds(len(records), settings.window_size_samples, settings.window_step_samples)
                total, windows = repository.read_analysis_windows_page(result["analysis_run_id"], len(bounds) + 1, 0)
                assert total == len(bounds) == len(windows) == result["statistics"]["windows_generated"]
                assert [(w["start_sequence"], w["end_sequence"]) for w in windows] == bounds
                analysis = repository.read_analysis(result["analysis_run_id"])
                model_provenance = analysis["model_artifact_provenance"]
                assert Path(model_provenance["artifact_path"]) == ROOT / "data/models/honda_keihin_71_17_v2/isolation_forest.joblib"
                assert model_provenance["artifact_sha256"] == file_sha256(Path(model_provenance["artifact_path"]))
                assert model_provenance["scaler_artifact_sha256"] == file_sha256(Path(model_provenance["scaler_artifact_path"]))
                report["model_artifact_provenance"] = model_provenance
                assert analysis["diagnostic_evidence"] == result["diagnostic_evidence"]
                pipeline_checks(report, {**analysis, "window_count": total}, len(bounds))
                assert all(check["passed"] for check in report["checks"]), report["checks"]
                feature_path = repository.analysis_root / result["analysis_run_id"] / "window_features.jsonl"
                with feature_path.open() as handle:
                    feature_records = [json.loads(line) for line in handle if line.strip()]
                assert len(feature_records) == total
                assert file_sha256(Path(result["artifacts"]["raw"])) == report["source_sha256"]
                retry = post()
                assert retry.status_code == 200
                assert retry.json()["duplicate_reused"] and retry.json()["analysis_run_id"] == result["analysis_run_id"]
                assert len(repository.list_sessions(200)) == len(repository.list_analyses_for_session(session_id)) == 1
                report.update(route=route, generated_windows=total, unwindowed_tail_samples=len(records) - bounds[-1][1] - 1,
                              feature_records=len(feature_records), one_unsplit_session=True, retry_idempotent=True,
                              persisted_analysis_sha256=file_sha256(repository.analysis_root / result["analysis_run_id"] / "analysis_run.json"),
                              persisted_windows_sha256=file_sha256(repository.analysis_root / result["analysis_run_id"] / "analysis_windows.jsonl"),
                              persisted_features_sha256=file_sha256(feature_path), diagnostic_evidence=result["diagnostic_evidence"])
                detectors = result["diagnostic_evidence"]["detectors"]
                report["detectors"] = {key: {"status": value["execution_status"], "scored": value["scored_window_count"],
                                                  "skipped": total - value["scored_window_count"], "reason": value["skip_or_failure_reason"],
                                                  "model_version": value["model_version"]} for key, value in detectors.items()}
                loaded = load_source(SessionSource(source_id=source.stem, filename=source.name, path=str(source), sha256=report["source_sha256"]))
                speeds, provenance = SpeedResolver().resolve(loaded)
                assert provenance["available_count"] == len(records) == len(speeds)
                assert provenance["raw_source_sha256"] == report["source_sha256"]
                report["speed_provenance"] = provenance
                if source.stem == "session_000018":
                    direct = AnalysisService(settings).analyze(expected.with_session_id(session_id), persist=False)
                    for column in ("prediction", "decision_score", "score_sample"):
                        assert [w[column] for w in windows] == direct.windows[column].tolist(), f"analysis parity: {column}"
                    for record, (_, row) in zip(feature_records, direct.windows.iterrows(), strict=True):
                        for column, value in record["features_json"].items():
                            assert value == row[column], f"feature/contextual parity: window {record['window_index']}, {column}"
                    assert direct.feature_frame.shape[0] == total
                    assert direct.summary["overall_status"] == result["result"]["overall_status"]
                    assert direct.summary["diagnostic_evidence"]["aggregate"]["window_summary"] == result["diagnostic_evidence"]["aggregate"]["window_summary"]
                    assert direct.summary["diagnostic_evidence"]["capability_status"] == result["diagnostic_evidence"]["capability_status"]
                    report["direct_native_analysis_parity"] = True
                    report["direct_native_contextual_and_feature_parity"] = True
                    report["contextual_status_counts"] = dict(Counter(item["features_json"]["contextual_status"] for item in feature_records))
                report["verdict"] = "PASS"
    except Exception as exc:
        report.update(verdict="FAIL", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    report["processing_seconds"] = perf_counter() - started
    report["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    atomic_write_json(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "real_data/fulldata_afterclean")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/research/raw1_upload")
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.worker:
        report = worker(args.worker.resolve(), args.output_dir / f"{args.worker.stem}.json")
        print(json.dumps({key: report.get(key) for key in ("session_id", "verdict", "input_frames", "generated_windows", "processing_seconds", "peak_rss_mib", "error")}))
        return 0 if report["verdict"] == "PASS" else 1
    protected_paths = [p for directory in (ROOT / "data/models", ROOT / "data/evaluation", ROOT / "data/context_annotation", ROOT / "data/telemetry")
                       for p in directory.rglob("*") if p.is_file()]
    protected_paths.extend(args.input_dir.glob("*.jsonl"))
    before = {str(p): file_sha256(p) for p in protected_paths}
    reports = []
    suite_started = perf_counter()
    for source in sorted(args.input_dir.glob("*.jsonl")):
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(source), "--output-dir", str(args.output_dir)], capture_output=True, text=True)
        report_path = args.output_dir / f"{source.stem}.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
        else:
            report = {"session_id": source.stem, "verdict": "FAIL", "error": completed.stderr[-3000:]}
        reports.append(report)
        print(json.dumps({key: report.get(key) for key in ("session_id", "verdict", "input_frames", "generated_windows", "processing_seconds", "error")}), flush=True)
    after = {path: file_sha256(Path(path)) for path in before}
    summary = {"schema_version": "raw1-native-upload-corpus-smoke-v1", "generated_at": utc_now_iso(),
               "sessions": [{key: value for key, value in report.items() if key not in {"diagnostic_evidence", "traceback"}} for report in reports],
               "source_count": len(reports), "independent_physical_captures": None,
               "total_input_frames": sum(report.get("input_frames", 0) for report in reports),
               "total_canonical_samples": sum(report.get("canonical_count", 0) for report in reports),
               "total_duration_s": sum(report.get("duration_s", 0) for report in reports),
               "total_windows": sum(report.get("generated_windows", 0) for report in reports),
               "total_seconds": perf_counter() - suite_started, "protected_files_unchanged": before == after,
               "protected_sha256_before": before, "protected_sha256_after": after,
               "verdict": "native_upload_ready" if len(reports) == 18 and all(r["verdict"] == "PASS" for r in reports) and before == after else "partial_compatibility"}
    atomic_write_json(args.output_dir / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key not in {"sessions", "protected_sha256_before", "protected_sha256_after"}}, indent=2))
    return 0 if summary["verdict"] == "native_upload_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
