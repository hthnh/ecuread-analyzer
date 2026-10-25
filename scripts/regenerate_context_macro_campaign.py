"""Regenerate the pinned originals and audit the lossless macro review projection."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
from pathlib import Path
from time import perf_counter

from app.context_annotation.attestation import verify_attestation
from app.context_annotation.corpus import repo_root
from app.context_annotation.macro import MACRO_RULE_VERSION, detail_hash, generate_macros
from app.context_annotation.pilot_quality import coalesce, distribution
from app.context_annotation.raw_campaign import DEFAULT_CAMPAIGN, verify_snapshot
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso
from scripts.reset_context_raw_campaign import backup_paths, verify_inputs


PILOT_IDS = [f"session_{n:06d}" for n in (2, 4, 5, 7, 18)]


def audit_grouping(fine, macro, loaded) -> dict:
    segments = [s.model_dump(mode="json") for s in fine.segments]
    episodes = [e.model_dump(mode="json") for e in macro.episodes]
    ids = {s.id for s in fine.segments}
    event_ids = {e.id for e in fine.events}
    result = {}
    for axis, field in (("operating", "operating_context"), ("thermal", "thermal_phase")):
        items = [e for e in episodes if e["axis"] == axis]
        children = {child for e in items for child in e["child_ids"]}
        assert children == ids, f"lost or invented {axis} child"
        assert {event for e in items for event in e["event_ids"]} == event_ids, "lost event reference"
        assert all(e["start_s"] < e["end_s"] for e in items)
        assert all(a["end_s"] <= b["start_s"] for a, b in zip(items, items[1:])), "overlapping macros"
        coverage = sum(e["end_s"]-e["start_s"] for e in items)
        assert abs(coverage - sum(s.end_s-s.start_s for s in fine.segments)) < 1e-6, "coverage changed"
        result[axis] = {"count": len(items), "duration_distribution": distribution([e["end_s"]-e["start_s"] for e in items]),
                        "coverage_s": coverage, "all_detailed_children_traceable": True,
                        "all_event_markers_traceable": True}
    op = [e for e in episodes if e["axis"] == "operating"]
    original_runs = coalesce(segments, "operating_context")
    macro_runs = coalesce([{**e, "operating_context": e["label"]} for e in op], "operating_context")
    states = ("stationary_running", "engine_off", "unknown", "moving_steady")
    preserved = {}
    for state in states:
        before = [(s["start_s"], s["end_s"]) for s in original_runs if s["label"] == state]
        after = [(s["start_s"], s["end_s"]) for s in macro_runs if s["label"] == state]
        assert before == after, f"{state} boundaries changed"
        preserved[state] = {"before": len(before), "after": len(after), "boundaries_identical": True,
                            "intervals_s": before}
    boundaries = {v for s in fine.segments for v in (s.start_s, s.end_s)}
    boundaries.update(e.start_s for e in fine.events if e.event in ("engine_start", "engine_stop"))
    boundaries.update(t for g in fine.timestamp_integrity.get("capture_gaps", []) for t in (g["start_s"], g["end_s"]))
    assert all(e.start_s in boundaries and e.end_s in boundaries for e in macro.episodes), "invented boundary"
    errors = []
    for e in macro.episodes:
        for t in (e.start_s, e.end_s):
            i = min(bisect_left(loaded.elapsed_s, t), len(loaded.elapsed_s)-1)
            errors.append(min(abs(loaded.elapsed_s[j]-t) for j in {i, max(0,i-1)}))
    result.update({"detailed_intervals_before": len(fine.segments), "operating_runs_before": len(original_runs),
                   "event_markers_before": len(fine.events), "event_markers_after": len(fine.events),
                   "event_types": dict(Counter(e.event for e in fine.events)),
                   "default_queue_before": len(fine.segments)+len(fine.events),
                   "default_operating_queue_after": result["operating"]["count"],
                   "operating_plus_thermal_decisions_after": len(macro.episodes),
                   "preserved_states": preserved, "automated_boundary_lineage_check": "pass",
                   "max_boundary_nearest_sample_distance_s": max(errors, default=0),
                   "manual_boundary_quality": "pending", "actual_human_review_time_before_s": None,
                   "actual_human_review_time_after_s": None, "time_savings_verified": False,
                   "important_context_loss_human_check": "pending", "lost_original_intervals": 0,
                   "lost_event_markers": 0, "hidden_by_default": ["event-marker queue", "fine transition queue"],
                   "hidden_evidence_access": "expand episode or open Detailed Intervals & Events"})
    return result


def regenerate(manifest_path: Path, backup_dir: Path) -> dict:
    root = repo_root()
    manifest, _, _ = verify_attestation(manifest_path)
    input_dir = root / "real_data/fulldata_afterclean"
    inputs = verify_inputs(input_dir, manifest)
    service = ContextAnnotationService(corpus_manifest=manifest_path)
    if any(Path(s.path).resolve().parent != input_dir.resolve() for s in service._sources()):
        raise ValueError("active manifest must reference the requested original RAW folder")
    protected = {str(p.relative_to(root)): file_sha256(p)
                 for d in ("data/models", "data/analyses", "data/evaluation", "real_data")
                 for p in (root/d).rglob("*") if p.is_file()}
    rule_path = root / "app/context_annotation/auto_labeler.py"
    protected[str(rule_path.relative_to(root))] = file_sha256(rule_path)
    protected[str(manifest_path.relative_to(root))] = file_sha256(manifest_path)
    for p in (manifest_path.parent / "provenance").glob("*.json"):
        protected[str(p.relative_to(root))] = file_sha256(p)
    annotations_before = {p.name: file_sha256(p) for p in service.storage.annotations_dir.glob("*.json")}
    stamp = utc_now_iso().replace(":", "").replace("+", "_")
    backup = backup_paths([service.storage.suggestions_dir, service.macro_review.path("unused").parent],
                          root=root, output=backup_dir / f"ctx24-before-regeneration-{stamp}.tar.gz")
    rows = []
    started = perf_counter()
    for row in inputs:
        tick = perf_counter()
        sid = row["session_id"]
        old = service.storage.read_suggestions(sid)
        service.review.generate(sid)
        fine = service.storage.read_suggestions(sid)
        if old and (old.segments != fine.segments or old.events != fine.events or detail_hash(old) != detail_hash(fine)):
            raise ValueError(f"detailed proposals/evidence changed during unchanged-rule regeneration: {sid}")
        loaded = service.load_session(sid)
        assert loaded.metadata.sample_count == row["input_frames"] == len(loaded.samples)
        assert loaded.metadata.duration_s == row["duration_s"]
        assert fine.signal_provenance["available_count"] == row["input_frames"]
        service.macro_review.generate(sid, loaded=loaded)
        # Audit generated grouping independently of any human boundary corrections.
        generated = generate_macros(fine, loaded)
        comparison = audit_grouping(fine, generated, loaded)
        rows.append({**row, "canonical_samples": len(loaded.samples), "rule_version": fine.rule_version,
                     "macro_rule_version": MACRO_RULE_VERSION, "detailed_content_sha256": detail_hash(fine),
                     "detailed_proposals_unchanged": True, "comparison": comparison,
                     "macro_progress": service.macro_review.read(sid, loaded=loaded)["progress"],
                     "processing_time_s": perf_counter()-tick})
        print(f"{sid}: {len(loaded.samples)} samples; {len(fine.segments)} detailed -> {comparison['operating']['count']} operating + {comparison['thermal']['count']} thermal; {len(fine.events)} markers retained", flush=True)
    assert annotations_before == {p.name: file_sha256(p) for p in service.storage.annotations_dir.glob("*.json")}
    protected_check = verify_snapshot(protected)
    assert protected_check["unchanged"], protected_check
    verify_inputs(input_dir, manifest)
    verify_attestation(manifest_path)
    report = {"schema_version": "ctx24-engineering-audit-v1", "created_at": utc_now_iso(), "dataset_id": manifest.dataset_id,
              "engineering_readiness": "automated_data_checks_passed_pending_browser_and_regression",
              "human_validation_readiness": "pending", "final_decision": "pending_human_validation",
              "manual_gates": {"timed_human_review": "pending", "manual_boundary_quality": "pending",
                               "important_context_loss": "pending", "large_scale_labeling_approval": "pending"},
              "recovery_backup": backup, "source_count": len(rows), "input_frames": sum(r["input_frames"] for r in rows),
              "canonical_samples": sum(r["canonical_samples"] for r in rows), "protected_files": protected_check,
              "accepted_annotations_unchanged": True, "annotation_file_count": len(annotations_before),
              "accepted_annotation_sha256": annotations_before,
              "protected_sha256": protected,
              "auto_acceptance_performed": False, "historical57_already_retired": True,
              "processing_time_s": perf_counter()-started, "sessions": rows,
              "pilots": [r for r in rows if r["session_id"] in PILOT_IDS]}
    atomic_write_json(service.storage.exports_dir / "ctx24_macro_quality.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, default=DEFAULT_CAMPAIGN / "corpus_manifest.json")
    parser.add_argument("--backup-dir", type=Path, default=Path("/home/hthnh/ecuread-analyzer-backups"))
    args = parser.parse_args()
    regenerate(args.corpus_manifest.resolve(), args.backup_dir.resolve())
