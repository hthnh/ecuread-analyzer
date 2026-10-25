"""Read-only all-source verification of the CTX2.5 research handoff."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET

import pandas as pd

from app.context_annotation.approved_export import export_approved
from app.context_annotation.auto_labeler import stable_hash
from app.context_annotation.bulk_endorsement import endorse_campaign, read_json
from app.context_annotation.raw_campaign import inspect_raw, verify_snapshot
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso
from app.ml.windowing import create_windows


def verify(manifest_path: Path, dataset: Path, junit: Path, supersedes: Path | None = None) -> dict:
    export = read_json(dataset / "export_manifest.json")
    stable = {k: v for k, v in export.items() if k not in {"created_at", "content_sha256"}}
    assert stable_hash(stable) == export["content_sha256"]
    for name, sha in export["files"].items():
        assert file_sha256(dataset / name) == sha, name
    campaign = manifest_path.resolve().parent
    baseline = read_json(campaign / "provenance/raw_reset_protected_baseline.json")
    protected = verify_snapshot(baseline["sha256"])
    assert protected["unchanged"], protected
    retry = endorse_campaign(manifest_path)
    assert retry["files_written_this_run"] == 0 and not retry["unresolved"]
    service = ContextAnnotationService(corpus_manifest=manifest_path)
    manifest = read_json(manifest_path)
    results, distributions = [], {name: Counter() for name in ("micro_operating", "micro_thermal", "macro_operating", "macro_thermal")}
    for entry in manifest["sessions"]:
        sid = entry["session_id"]
        source = Path(entry["source_path"])
        assert file_sha256(source) == entry["source_sha256"]
        frames, raw_times = inspect_raw(source.read_bytes())
        loaded = service.load_session(sid)
        assert len(frames) == len(loaded.samples) == len(loaded.elapsed_s)
        assert all(a < b for a, b in zip(loaded.elapsed_s, loaded.elapsed_s[1:]))
        assert loaded.metadata.duration_s == (raw_times[-1] - raw_times[0]) / 1000
        directory = dataset / "sessions" / sid
        labels = read_json(directory / "labels.json")
        assert read_json(service.storage.annotation_path(sid)) == labels["annotation"]
        assert read_json(service.storage.suggestion_path(sid)) == labels["detailed_proposals"]
        assert read_json(service.macro_review.path(sid)) == labels["macro_review"]
        rows = labels["detailed_proposals"]["segments"]
        macros = labels["macro_review"]["episodes"]
        trace_pairs = 0
        for axis in ("operating", "thermal"):
            for child in rows:
                related = [e for e in macros if e["axis"] == axis and child["id"] in e["child_ids"]]
                intersections = sorted((max(e["start_s"], child["start_s"]), min(e["end_s"], child["end_s"])) for e in related)
                assert intersections and intersections[0][0] == child["start_s"] and intersections[-1][1] == child["end_s"]
                assert all(a[1] == b[0] for a, b in zip(intersections, intersections[1:]))
                assert sum(b-a for a,b in intersections) == child["end_s"] - child["start_s"]
                trace_pairs += len(related)
                if child["operating_context"] == "stationary_running" and axis == "operating":
                    assert all(e["label"] == "stationary_running" for e in related)
        expected_ids = {track: [None] * len(loaded.samples) for track in labels["tracks"]}
        for track, intervals in labels["tracks"].items():
            for interval in intervals:
                distributions[track][interval["label"]] += 1
                for i in range(interval["start_index"], interval["end_index"]):
                    assert expected_ids[track][i] is None
                    expected_ids[track][i] = interval["id"]
            assert all(expected_ids[track]), (sid, track, "unmapped sample")
        with (directory / "samples.jsonl").open() as handle:
            count = 0
            for i, line in enumerate(handle):
                sample = json.loads(line)
                assert sample["canonical_index"] == i and sample["elapsed_s"] == loaded.elapsed_s[i]
                assert sample["sequence"] == loaded.samples[i]["sequence"]
                assert sample["timestamp_ms"] == loaded.samples[i]["timestamp_ms"]
                assert sample["interval_ids"] == {track: ids[i] for track, ids in expected_ids.items()}
                count += 1
            assert count == len(loaded.samples)
        df = pd.DataFrame({"frame_index": list(range(len(loaded.samples))),
                           "relative_time_ms": [t*1000 for t in loaded.elapsed_s],
                           "is_valid": [s["frame_valid"] for s in loaded.samples],
                           "checksum_valid": [s["checksum_valid"] for s in loaded.samples]})
        production_windows = create_windows(df, 50, 10)
        with (directory / "windows.jsonl").open() as handle:
            research_windows = [json.loads(line) for line in handle]
        assert len(research_windows) == len(production_windows) == (len(loaded.samples)-50)//10+1
        for research, production in zip(research_windows, production_windows, strict=True):
            assert research["row_start"] == production.row_start and research["row_end"] == production.row_end
            for assignment in research["tracks"].values():
                if assignment["status"] in {"ambiguous", "unresolved"}:
                    assert assignment["assigned_label"] is None
        results.append({"session_id": sid, "source_sha256": entry["source_sha256"], "source_frames": len(frames),
                        "canonical_samples": len(loaded.samples), "duration_s": loaded.metadata.duration_s,
                        "mapped_samples": count, "windows": len(research_windows), "macro_micro_trace_pairs": trace_pairs,
                        "stationary_intervals_preserved": sum(s["operating_context"] == "stationary_running" for s in rows),
                        "source_and_live_sidecars_equal_export": True})
    with tempfile.TemporaryDirectory(prefix="ctx25-reproducibility-") as tmp:
        repeated = export_approved(corpus_manifest=manifest_path, output=Path(tmp) / "repeated-export")
        assert repeated["content_sha256"] == export["content_sha256"]
    tests = ET.parse(junit).getroot().find("testsuite")
    assert int(tests.attrib["failures"]) == int(tests.attrib["errors"]) == int(tests.attrib["skipped"]) == 0
    junit_copy = campaign / "exports/ctx25_tests.junit.xml"
    if junit.resolve() != junit_copy.resolve():
        shutil.copyfile(junit, junit_copy)
    status = service.build_campaign_status(service.list_sessions())
    atomic_write_json(campaign / "exports/ctx25_annotation_campaign_status.json", status)
    report = {"schema_version": "ctx25-engineering-verification-v1", "verified_at": utc_now_iso(),
              "engineering_readiness": "approved_handoff_verified", "human_boundary_validation": "not_performed",
              "decision": "approval_recorded_snapshot_blocked", "dataset_path": str(dataset),
              "dataset_content_sha256": export["content_sha256"], "all_artifact_hashes_verified": True,
              "idempotent_retry_files_written": 0, "independent_reexport_content_hash_identical": True,
              "protected_integrity": protected, "test_results": dict(tests.attrib), "junit_path": str(junit_copy), "junit_sha256": file_sha256(junit_copy),
              "label_count_distribution": {k: dict(v) for k, v in distributions.items()},
              "total_samples": sum(s["canonical_samples"] for s in results), "total_windows": sum(s["windows"] for s in results),
              "sessions": results}
    atomic_write_json(campaign / "exports/ctx25_engineering_verification.json", report)
    publication = {"schema_version": "context-approved-export-publication-v1", "dataset_id": export["dataset_id"],
                   "authoritative_approved_export": str(dataset), "content_sha256": export["content_sha256"],
                   "frozen_snapshot": None, "decision": report["decision"], "superseded_exports": []}
    if supersedes is not None:
        old = read_json(supersedes / "export_manifest.json")
        assert old["source_hashes"] == export["source_hashes"] and old["approval_record_sha256"] == export["approval_record_sha256"]
        publication["superseded_exports"].append({"path": str(supersedes), "content_sha256": old["content_sha256"],
            "reason": "Initial non-frozen export incorrectly grouped all named confirmations as individually_confirmed, including shared multi-item UI actions. Superseded before handoff; labels/source bytes unchanged. Use the authoritative export only; never concatenate versions."})
    atomic_write_json(campaign / "exports/ctx25_export_publication.json", publication)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--supersedes", type=Path)
    args = parser.parse_args()
    report = verify(args.corpus_manifest, args.dataset, args.junit, args.supersedes)
    print(json.dumps({k:v for k,v in report.items() if k != "sessions"}, indent=2))


if __name__ == "__main__":
    main()
