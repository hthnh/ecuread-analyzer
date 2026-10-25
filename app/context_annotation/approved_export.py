"""Audit and export endorsed annotations without weakening the individual-review freeze gate."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
import tempfile

from app.context_annotation.annotation_snapshot import annotation_snapshot
from app.context_annotation.attestation import verify_attestation
from app.context_annotation.auto_labeler import stable_hash
from app.context_annotation.bulk_endorsement import check_traceability, digest, json_sha, provenance, provenance_context, read_json
from app.context_annotation.freeze_dataset import FreezeValidationError
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso
from app.context_annotation.validation import covered_duration, validate_document


MAPPING_SCHEMA = {
    "schema_version": "context-research-mapping-v1",
    "sample_identity": "session_id + zero-based canonical_index, original sequence and timestamp_ms",
    "time_basis": "elapsed seconds from canonical source start; no resampling or record removal",
    "interval_semantics": "[start_s, end_s); include last canonical sample when end_s equals session duration",
    "sample_ranges": "zero-based start_index inclusive, end_index exclusive; empty ranges retained",
    "window_size_samples": 50, "window_step_samples": 10,
    "window_identity": "session_id + canonical row_start; complete 50-row windows only, trailing remainder not padded",
    "window_policy": "Any interval boundary within (first_sample_time, last_sample_time] makes the axis ambiguous, even for identical labels or intervals containing no samples. Return all intersecting interval IDs; never majority-vote. Gaps remain unresolved; homogeneous unknown is abstention, not missing annotation.",
    "tracks": ["micro_operating", "micro_thermal", "macro_operating", "macro_thermal"],
    "events": "Separate overlapping markers; never an operating/thermal class",
    "ml_eligibility": "Canonical-index research windows, not detector predictions. ML feature/quality eligibility must be checked independently; no invalid row removal or concatenation across capture identities.",
    "ground_truth_limit": "Operator bulk endorsement is not individual boundary validation or detector ground truth",
}


def sample_range(times: list[float], start: float, end: float, duration: float) -> tuple[int, int]:
    return bisect_left(times, start), (bisect_right(times, end) if end == duration else bisect_left(times, end))


def window_assignment(rows: list[dict], start: float, end: float, duration: float) -> dict:
    selected = [r for r in rows if r["start_s"] <= end and (r["end_s"] > start or r["end_s"] == start == duration)]
    covered = bool(selected) and selected[0]["start_s"] <= start and selected[-1]["end_s"] >= end
    covered &= all(a["end_s"] >= b["start_s"] for a, b in zip(selected, selected[1:]))
    boundaries = sorted({t for r in selected for t in (r["start_s"], r["end_s"]) if start < t <= end and t != duration})
    status = "unresolved" if not covered else "ambiguous" if len(selected) != 1 or boundaries else "abstained" if selected[0]["label"] == "unknown" else "homogeneous"
    return {"status": status, "assigned_label": selected[0]["label"] if status in {"homogeneous", "abstained"} else None,
            "interval_ids": [r["id"] for r in selected], "labels_present": sorted({r["label"] for r in selected}),
            "boundary_times_s": boundaries, "review_provenance": sorted({r["review_provenance"] for r in selected})}


def mapping_rows(annotation: dict, macro: dict, times: list[float], duration: float, *, review_context: dict | None = None) -> tuple[dict, list[dict]]:
    review_context = review_context or {}
    tracks = {key: [] for key in MAPPING_SCHEMA["tracks"]}
    for s in annotation["segments"]:
        for axis, field in (("operating", "operating_context"), ("thermal", "thermal_phase")):
            tracks["micro_" + axis].append({"id": s["id"], "suggestion_id": s.get("suggestion_id"), "label": s["labels"][field],
                                             "start_s": s["start_s"], "end_s": s["end_s"], "review_provenance": provenance(s, **review_context)})
    for e in macro["episodes"]:
        if e["review_status"] == "accepted":
            tracks["macro_" + e["axis"]].append({"id": e["id"], "label": e["label"], "start_s": e["start_s"],
                                                "end_s": e["end_s"], "child_ids": e["child_ids"], "event_ids": e["event_ids"],
                                                "review_provenance": provenance(e, **review_context)})
    events = [{"id": e["id"], "event": e["event"], "start_s": e["start_s"], "end_s": e["end_s"],
               "review_provenance": provenance(e, **review_context)} for e in annotation["events"]]
    for rows in [*tracks.values(), events]:
        rows.sort(key=lambda r: (r["start_s"], r["end_s"], r["id"]))
        for row in rows:
            a, b = sample_range(times, row["start_s"], row["end_s"], duration)
            row.update(start_index=a, end_index=b)
    return tracks, events


def write_mapping(directory: Path, loaded, tracks: dict, events: list[dict]) -> dict:
    times, duration = loaded.elapsed_s, loaded.metadata.duration_s
    sample_ids = {name: [None] * len(times) for name in tracks}
    for name, rows in tracks.items():
        for r in rows:
            for i in range(r["start_index"], r["end_index"]):
                if sample_ids[name][i] is not None:
                    raise ValueError("contradictory accepted sample labels")
                sample_ids[name][i] = r["id"]
    event_ids = [[] for _ in times]
    for e in events:
        for i in range(e["start_index"], e["end_index"]):
            event_ids[i].append(e["id"])
    with (directory / "samples.jsonl").open("x") as handle:
        for i, (time, sample) in enumerate(zip(times, loaded.samples, strict=True)):
            handle.write(json.dumps({"canonical_index": i, "elapsed_s": time, "sequence": sample.get("sequence"),
                                     "timestamp_ms": sample.get("timestamp_ms"), "interval_ids": {n: ids[i] for n, ids in sample_ids.items()},
                                     "event_ids": event_ids[i]}, sort_keys=True) + "\n")
    counts = {name: Counter() for name in tracks}
    windows = 0
    with (directory / "windows.jsonl").open("x") as handle:
        for start in range(0, len(times) - 50 + 1, 10):
            end = start + 49
            axes = {name: window_assignment(rows, times[start], times[end], duration) for name, rows in tracks.items()}
            for name, assignment in axes.items():
                counts[name][assignment["status"]] += 1
            handle.write(json.dumps({"window_index": windows, "row_start": start, "row_end": end, "sample_count": 50,
                                     "start_s": times[start], "end_s": times[end], "tracks": axes,
                                     "event_ids": [e["id"] for e in events if e["start_s"] <= times[end] and e["end_s"] > times[start]]}, sort_keys=True) + "\n")
            windows += 1
    return {"samples": len(times), "windows": windows, "window_status_by_axis": {n: dict(c) for n, c in counts.items()},
            "trailing_samples_after_last_window": len(times) - ((windows - 1) * 10 + 50) if windows else len(times)}


def track_stats(rows: list[dict], duration: float) -> dict:
    distribution, by_provenance = defaultdict(float), defaultdict(float)
    for r in rows:
        length = r["end_s"] - r["start_s"]
        distribution[r["label"]] += length
        by_provenance[r["review_provenance"]] += length
    from types import SimpleNamespace
    coverage = covered_duration([SimpleNamespace(**r) for r in rows], duration)
    return {"accepted_count": len(rows), "accepted_duration_s": coverage, "duration_by_context_s": dict(distribution),
            "duration_by_provenance_s": dict(by_provenance), "explicit_unknown_duration_s": distribution.get("unknown", 0),
            "truly_unannotated_duration_s": max(0, duration - coverage)}


def verify_preservation(service, sid: str, work: Path, fine: dict, macro: dict, annotation: dict) -> dict:
    before_fine = read_json(work / "before" / f"{sid}.suggestions.json")
    before_macro = read_json(work / "before" / f"{sid}.macro.json")
    before_annotation = read_json(work / "before" / f"{sid}.context.json")
    preserved = 0
    for key in ("segments", "events"):
        after = {s["id"]: s for s in fine[key]}
        if set(after) != {s["id"] for s in before_fine[key]}:
            raise ValueError("original proposals lost/added")
        for old in before_fine[key]:
            new = after[old["id"]]
            immutable = set(old) - {"review_status", "review_history", "bulk_endorsement"}
            if any(old[k] != new[k] for k in immutable):
                raise ValueError("original detailed evidence/labels changed")
            if new["review_history"][:len(old["review_history"])] != old["review_history"]:
                raise ValueError("original review history changed")
        if before_annotation:
            accepted = {s["id"]: s for s in annotation[key]}
            for old in before_annotation.get(key, []):
                if accepted.get(old["id"]) != old:
                    raise ValueError("existing accepted annotation changed")
                preserved += 1
    after = {e["id"]: e for e in macro["episodes"]}
    if set(after) != {e["id"] for e in before_macro["episodes"]} or macro.get("retained_reviews", []) != before_macro.get("retained_reviews", []):
        raise ValueError("original macro identities/retained reviews changed")
    for old in before_macro["episodes"]:
        new = after[old["id"]]
        if any(old[k] != new[k] for k in set(old) - {"review_status", "review_history", "bulk_endorsement"}):
            raise ValueError("original macro boundaries/evidence/links changed")
        if old["review_status"] != "pending" or old["review_history"]:
            if old != new:
                raise ValueError("existing macro human decision changed")
            preserved += 1
    return {"all_original_proposals_preserved": True, "all_macro_links_and_boundaries_preserved": True,
            "existing_human_records_preserved": preserved, "stationary_proposals_preserved": sum(
                s["operating_context"] == "stationary_running" for s in before_fine["segments"])}


def export_approved(*, corpus_manifest: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("approved exports are versioned and never overwritten")
    manifest, attestation, groups = verify_attestation(corpus_manifest)
    service = ContextAnnotationService(corpus_manifest=corpus_manifest)
    campaign = corpus_manifest.resolve().parent
    approval_path = campaign / "provenance/operator_context_approval.v1.json"
    approval = read_json(approval_path)
    if not approval:
        raise ValueError("no operator approval record")
    work = Path(approval["recovery_directory"])
    transaction = read_json(work / "transaction.json")
    if not transaction or transaction["approval"] != approval:
        raise ValueError("approval/recovery transaction mismatch")
    if approval["approval_id"] != manifest.dataset_id + ":bulk-endorsement:" + stable_hash({k: v for k, v in approval.items() if k != "approval_id"}):
        raise ValueError("approval identity changed")
    if approval["corpus_manifest_sha256"] != file_sha256(corpus_manifest) or approval["input_source_hashes"] != {
        e.session_id: e.source_sha256 for e in manifest.sessions}:
        raise ValueError("approval source scope mismatch")
    watched = [corpus_manifest, approval_path, Path(manifest.operator_attestation["path"]), Path(manifest.operator_attestation["capture_groups_path"])]
    watched += [p for e in manifest.sessions for p in (service.storage.annotation_path(e.session_id),
                service.storage.suggestion_path(e.session_id), service.macro_review.path(e.session_id))]
    before = {str(p): digest(p) for p in watched}
    for b in transaction["backups"]:
        if digest(work / b["path"]) != b["sha256"]:
            raise ValueError("approved recovery input SHA mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ctx25-export-", dir=output.parent))
    try:
        sessions = []
        for entry in manifest.sessions:
            sid = entry.session_id
            loaded = service.load_session(sid)
            if loaded.metadata.status != "ok":
                raise ValueError(f"source unavailable: {sid}")
            document = service.storage.read_annotation(sid)
            if document is None:
                raise ValueError(f"annotation missing: {sid}")
            validate_document(document, duration_s=loaded.metadata.duration_s, source_sha256=entry.source_sha256)
            fine = read_json(service.storage.suggestion_path(sid))
            macro = read_json(service.macro_review.path(sid))
            annotation = read_json(service.storage.annotation_path(sid))
            if fine["source"]["sha256"] != entry.source_sha256 or fine["signal_provenance"].get("raw_source_sha256") != loaded.metadata.research_signal_provenance.get("raw_source_sha256"):
                raise ValueError("suggestion/source evidence SHA mismatch")
            for item in annotation["segments"] + annotation["events"] + fine["segments"] + fine["events"] + macro["episodes"]:
                bulk = item.get("bulk_endorsement")
                if bulk and (bulk["approval_id"] != approval["approval_id"] or bulk["approval_record_sha256"] != json_sha(approval)):
                    raise ValueError("accepted bulk provenance does not match operator approval")
            check_traceability(fine, macro, loaded.metadata.duration_s)
            integrity = verify_preservation(service, sid, work, fine, macro, annotation)
            review_context = provenance_context(fine, macro)
            tracks, events = mapping_rows(annotation, macro, loaded.elapsed_s, loaded.metadata.duration_s, review_context=review_context)
            directory = stage / "sessions" / sid
            directory.mkdir(parents=True)
            mapping = write_mapping(directory, loaded, tracks, events)
            stats = {name: track_stats(rows, loaded.metadata.duration_s) for name, rows in tracks.items()}
            accepted = annotation["segments"] + annotation["events"] + [e for e in macro["episodes"] if e["review_status"] == "accepted"]
            record = {"session_id": sid, "source_path": entry.source_path, "source_sha256": entry.source_sha256,
                      "physical_capture_group_id": entry.origin["physical_capture_group_id"],
                      "confirmed_session_metadata": entry.origin.get("confirmed_session_metadata", {}),
                      "canonical_sample_count": len(loaded.samples), "duration_s": loaded.metadata.duration_s,
                      "decoder_provenance": loaded.metadata.decoder_provenance, "signal_provenance": fine["signal_provenance"],
                      "rule_version": fine["rule_version"], "rule_config_sha256": fine["config_sha256"],
                      "taxonomy_version": fine["taxonomy_version"], "macro_rule_version": macro["rule_version"],
                      "approved_suggestion_hashes": approval["approved_suggestion_hashes"][sid],
                      "annotation_sha256": file_sha256(service.storage.annotation_path(sid)),
                      "suggestions_sha256": file_sha256(service.storage.suggestion_path(sid)),
                      "macro_sha256": file_sha256(service.macro_review.path(sid)),
                      "accepted_counts_by_provenance": dict(Counter(provenance(i, **review_context) for i in accepted)),
                      "proposal_status_counts": dict(Counter(s["review_status"] for s in fine["segments"] + fine["events"])),
                      "macro_status_counts": dict(Counter(e["review_status"] for e in macro["episodes"])),
                      "micro_interval_count": len(fine["segments"]), "event_count": len(fine["events"]),
                      "macro_count": len(macro["episodes"]), "event_distribution": dict(Counter(e["event"] for e in annotation["events"])),
                      "tracks": stats, "mapping": mapping, "integrity": integrity}
            atomic_write_json(directory / "labels.json", {"annotation": annotation, "detailed_proposals": fine,
                                                         "macro_review": macro, "tracks": tracks, "event_markers": events})
            sessions.append(record)
        try:
            if transaction["unresolved"] or any(n for s in sessions for k in ("proposal_status_counts", "macro_status_counts") for status, n in s[k].items() if status != "accepted"):
                raise FreezeValidationError("preserved unresolved decisions/conflicts prevent freezing")
            frozen = annotation_snapshot(corpus_manifest=corpus_manifest, output=stage / "individual_review_snapshot")
            freeze = {"status": "frozen", "content_sha256": frozen["content_sha256"]}
        except FreezeValidationError as exc:
            freeze = {"status": "blocked", "policy": "unchanged individual-review snapshot-v1", "reason": str(exc),
                      "bulk_endorsement_is_not_individual_confirmation": True}
        total_tracks = {}
        for name in MAPPING_SCHEMA["tracks"]:
            total = {"accepted_count": 0, "accepted_duration_s": 0., "explicit_unknown_duration_s": 0., "truly_unannotated_duration_s": 0.,
                     "duration_by_context_s": defaultdict(float), "duration_by_provenance_s": defaultdict(float)}
            for session in sessions:
                for k, value in session["tracks"][name].items():
                    if isinstance(value, dict):
                        for label, length in value.items():
                            total[k][label] += length
                    else:
                        total[k] += value
            total_tracks[name] = total
        totals = {key: sum(s[key] for s in sessions) for key in ("canonical_sample_count", "duration_s", "micro_interval_count", "event_count", "macro_count")}
        accepted_by_provenance, event_distribution = Counter(), Counter()
        for s in sessions:
            accepted_by_provenance.update(s["accepted_counts_by_provenance"])
            event_distribution.update(s["event_distribution"])
        audit = {"schema_version": "context-approved-dataset-audit-v1", "session_count": len(sessions), **totals,
                 "accepted_counts_by_provenance": dict(accepted_by_provenance), "event_distribution": dict(event_distribution),
                 "total_accepted_annotations": sum(accepted_by_provenance.values()), "tracks": total_tracks,
                 "unresolved_conflicts": transaction["unresolved"],
                 "unresolved_annotation_count": sum(n for s in sessions for k in ("proposal_status_counts", "macro_status_counts") for status, n in s[k].items() if status != "accepted"),
                 "source_sha_unique": len({s["source_sha256"] for s in sessions}) == len(sessions),
                 "capture_groups_unique": len({s["physical_capture_group_id"] for s in sessions}) == len(sessions),
                 "accepted_micro_overlap_count": 0, "accepted_macro_within_axis_overlap_count": 0,
                 "integrity_validation": "All source SHAs, annotation bounds, non-overlap and macro references checked; validation failures abort publication",
                 "operator_attested_independent_captures": manifest.operator_attested_independent_captures,
                 "machine_verified_independent_captures": None, "snapshot_attempt": freeze,
                 "human_boundary_accuracy": "not_measured", "manual_interval_review_time": "not_measured_by_this_task",
                 "sessions": sessions}
        atomic_write_json(stage / "quality_audit.json", audit)
        atomic_write_json(stage / "mapping_schema.json", MAPPING_SCHEMA)
        atomic_write_json(stage / "operator_approval.json", approval)
        atomic_write_json(stage / "operator_attestation.json", attestation)
        atomic_write_json(stage / "capture_groups.json", groups)
        files = {str(p.relative_to(stage)): file_sha256(p) for p in sorted(stage.rglob("*")) if p.is_file()}
        stable = {"schema_version": "context-approved-research-export-v1", "dataset_id": manifest.dataset_id,
                  "frozen": freeze["status"] == "frozen", "snapshot_attempt": freeze,
                  "purpose": "Annotation-only research handoff; no models trained or evaluated",
                  "approval_type": "bulk_operator_endorsement", "individual_interval_review": False,
                  "corpus_manifest_sha256": file_sha256(corpus_manifest), "approval_record_sha256": file_sha256(approval_path),
                  "source_hashes": approval["input_source_hashes"], "files": files,
                  "operator_attested_independent_captures": manifest.operator_attested_independent_captures,
                  "machine_verified_independent_captures": None,
                  "code_sha256": {p.name: file_sha256(p) for p in [Path(__file__), Path(__file__).with_name("bulk_endorsement.py")]}}
        payload = {**stable, "content_sha256": stable_hash(stable), "created_at": utc_now_iso()}
        atomic_write_json(stage / "export_manifest.json", payload)
        if any(digest(Path(p)) != sha for p, sha in before.items()):
            raise ValueError("review inputs changed during export; no handoff published")
        verify_attestation(corpus_manifest)
        output.mkdir(exist_ok=False)
        # Manifest is published last; absence of it denotes an incomplete export.
        for child in sorted(stage.iterdir(), key=lambda p: p.name == "export_manifest.json"):
            shutil.move(str(child), output / child.name)
        return {"output": str(output), "content_sha256": payload["content_sha256"], "frozen": payload["frozen"],
                "snapshot_attempt": freeze, "accepted": audit["total_accepted_annotations"],
                "unresolved": audit["unresolved_annotation_count"],
                "decision": "context_snapshot_ready" if payload["frozen"] else "approval_recorded_snapshot_blocked"}
    finally:
        shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(export_approved(corpus_manifest=args.corpus_manifest, output=args.output), indent=2))
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
