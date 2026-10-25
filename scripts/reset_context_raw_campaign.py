"""Explicitly retire the legacy 57 and regenerate proposals from original RAW."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path
from time import perf_counter

from app.context_annotation.attestation import verify_attestation
from app.context_annotation.corpus import read_curated_manifest, repo_root
from app.context_annotation.raw_campaign import DEFAULT_CAMPAIGN, EXPECTED_IDS, inspect_raw
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso


def legacy_paths(root: Path, manifest_path: Path) -> tuple[list[str], list[Path]]:
    manifest = read_curated_manifest(manifest_path)
    ids = [entry.session_id for entry in manifest.sessions]
    if len(ids) != 57 or len(set(ids)) != 57:
        raise ValueError("legacy retirement requires exactly 57 explicit, unique IDs")
    paths = []
    for entry in manifest.sessions:
        telemetry = root / "data/telemetry" / entry.session_id
        if (Path(entry.session_id).name != entry.session_id or telemetry.resolve().parent != (root / "data/telemetry").resolve()
                or (root / entry.source_path).resolve() != telemetry.resolve() or telemetry.is_symlink()):
            raise ValueError(f"legacy source is not its expected telemetry directory: {entry.session_id}")
        paths.extend([telemetry, root / "data/entities/sessions" / f"{entry.session_id}.json",
                      root / "data/context_annotation/annotations" / f"{entry.session_id}.context.json",
                      root / "data/context_annotation/suggestions" / f"{entry.session_id}.suggestions.json"])
    return ids, paths


def verify_inputs(input_dir: Path, manifest) -> list[dict]:
    found = {path.stem: path for path in input_dir.glob("*.jsonl")}
    if set(found) != EXPECTED_IDS or {e.session_id for e in manifest.sessions} != EXPECTED_IDS:
        raise ValueError("RAW folder must match all 18 exact pinned source IDs; no silent additions")
    records = []
    for entry in manifest.sessions:
        source = found[entry.session_id]
        if not source.is_file() or source.is_symlink() or file_sha256(source) != entry.source_sha256:
            raise ValueError(f"original RAW SHA mismatch: {entry.session_id}")
        frames, times = inspect_raw(source.read_bytes())
        duration = (times[-1]-times[0])/1000
        if len(frames) != entry.origin["sample_count"] or abs(duration-entry.origin["duration_s"]) > 1e-6:
            raise ValueError(f"original RAW count/duration mismatch: {entry.session_id}")
        records.append({"session_id": entry.session_id, "source_path": str(source.resolve()), "source_sha256": entry.source_sha256,
                        "input_frames": len(frames), "duration_s": duration})
    return records


def backup_paths(paths: list[Path], *, root: Path, output: Path) -> dict:
    files = {p.relative_to(root).as_posix(): p for path in paths if path.exists()
             for p in (path.rglob("*") if path.is_dir() else [path]) if p.is_file()}
    if any(p.is_symlink() for p in files.values()):
        raise ValueError("backup refuses symbolic links")
    hashes = {name: file_sha256(path) for name, path in files.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "x:gz") as archive:
        for name, path in files.items():
            archive.add(path, arcname=name, recursive=False)
    with tarfile.open(output, "r:gz") as archive:
        if {member.name for member in archive.getmembers()} != set(hashes):
            raise ValueError("backup member set differs from selected files")
        for member in archive.getmembers():
            stream = archive.extractfile(member)
            if stream is None or hashlib.file_digest(stream, "sha256").hexdigest() != hashes[member.name]:
                raise ValueError(f"backup verification failed: {member.name}")
    if any(file_sha256(path) != hashes[name] for name, path in files.items()):
        raise ValueError("source changed while making recovery backup")
    return {"path": str(output), "sha256": file_sha256(output), "file_count": len(files), "file_sha256": hashes}


def reset_campaign(input_dir: Path, backup_dir: Path, *, delete_legacy: bool) -> dict:
    if not delete_legacy:
        raise ValueError("explicit --delete-legacy-57 required")
    root = repo_root()
    campaign = root / DEFAULT_CAMPAIGN
    manifest_path = campaign / "corpus_manifest.json"
    manifest, _, _ = verify_attestation(manifest_path)
    inputs = verify_inputs(input_dir, manifest)
    old_manifest_path = root / "data/context_annotation/curated_corpus.json"
    old_ids, remove = legacy_paths(root, old_manifest_path)
    # Keep immutable historical reports and the old inventory needed by the attestation.
    extra_remove = root / "data/context_annotation/suggestions/batch_preview.json"
    remove.append(extra_remove)
    retirement_path = campaign / "provenance/legacy57_retirement.json"
    if retirement_path.exists():
        previous = json.loads(retirement_path.read_text())
        if previous["retired_session_ids"] != old_ids or any(p.exists() for p in remove):
            raise ValueError("retired legacy data reappeared; inspect before deleting again")
        recovery = previous["recovery_backup"]
    else:
        if any(not (root / "data/telemetry" / sid).is_dir() for sid in old_ids):
            raise ValueError("legacy inventory already missing source data; retirement cannot be assumed")
        stamp = utc_now_iso().replace(":", "").replace("+", "_")
        backup = backup_dir / f"legacy57-before-raw-reset-{stamp}.tar.gz"
        recovery = backup_paths(remove + [old_manifest_path, manifest_path,
                                         root / "data/context_annotation/raw_signal_sources.json", campaign / "suggestions"], root=root, output=backup)
    before_annotations = {p.name: file_sha256(p) for p in (campaign / "annotations").glob("*.json")}
    before_rule = file_sha256(root / "app/context_annotation/auto_labeler.py")
    started = perf_counter()
    with tempfile.TemporaryDirectory(prefix="raw-regeneration-", dir=campaign) as tmp:
        staging = Path(tmp)
        payload = manifest.model_dump(mode="json")
        payload["source_root"] = str(input_dir.resolve())
        for entry in payload["sessions"]:
            entry["source_path"] = str((input_dir / f"{entry['session_id']}.jsonl").resolve().relative_to(root)) if input_dir.resolve().is_relative_to(root) else str((input_dir / f"{entry['session_id']}.jsonl").resolve())
        staged_manifest = staging / "corpus_manifest.json"
        atomic_write_json(staged_manifest, payload)
        proposals_dir = staging / "suggestions"
        proposals_dir.mkdir()
        for path in (campaign / "suggestions").glob("*.suggestions.json"):
            if path.name.removesuffix(".suggestions.json") not in EXPECTED_IDS:
                raise ValueError("unexpected source sidecar in RAW campaign")
            shutil.copy2(path, proposals_dir / path.name)
        service = ContextAnnotationService(corpus_manifest=staged_manifest, annotations_dir=campaign / "annotations",
                                           suggestions_dir=proposals_dir, exports_dir=staging / "exports")
        records = []
        for row in inputs:
            sid = row["session_id"]
            tick = perf_counter()
            proposals = service.review.generate(sid)
            loaded = service.load_session(sid)
            if loaded.metadata.status != "ok" or loaded.metadata.sample_count != row["input_frames"]:
                raise ValueError(f"canonical count mismatch or parse failure: {sid}")
            if proposals["signal_provenance"]["available_count"] != row["input_frames"]:
                raise ValueError(f"speed evidence did not cover entire source: {sid}")
            records.append({**row, "canonical_samples": loaded.metadata.sample_count,
                            "operating_proposals": len(proposals["segments"]), "event_markers": len(proposals["events"]),
                            "pending": proposals["pending_count"], "uncertain": proposals["uncertain_count"],
                            "accepted": sum(s["review_status"] == "accepted" for s in proposals["segments"]+proposals["events"]),
                            "decoder_provenance": loaded.metadata.decoder_provenance,
                            "speed_provenance": proposals["signal_provenance"], "rule_version": proposals["rule_version"],
                            "config_sha256": proposals["config_sha256"], "processing_time_s": perf_counter()-tick})
            print(f"{sid}: {loaded.metadata.sample_count} samples, {len(proposals['segments'])} segments, {len(proposals['events'])} events", flush=True)
        verify_inputs(input_dir, manifest)
        if {p.name: file_sha256(p) for p in (campaign / "annotations").glob("*.json")} != before_annotations:
            raise ValueError("annotations changed during regeneration; no legacy data deleted")
        if file_sha256(manifest_path) != recovery["file_sha256"][str(manifest_path.relative_to(root))] and not retirement_path.exists():
            raise ValueError("campaign manifest changed during regeneration")
        # Commit only after every original has generated full-session proposals successfully.
        atomic_write_json(manifest_path, payload)
        for path in proposals_dir.glob("*.suggestions.json"):
            path.replace(campaign / "suggestions" / path.name)
        removed_paths = []
        for path in remove:
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed_paths.append(str(path.relative_to(root)))
    verify_attestation(manifest_path)
    assert file_sha256(root / "app/context_annotation/auto_labeler.py") == before_rule
    assert {p.name: file_sha256(p) for p in (campaign / "annotations").glob("*.json")} == before_annotations
    active = {"schema_version": "context-active-campaign-v1", "dataset_id": manifest.dataset_id,
              "corpus_manifest": str(manifest_path.relative_to(root)), "corpus_manifest_sha256": file_sha256(manifest_path),
              "input_dir": str(input_dir.resolve()), "source_count": 18, "legacy_campaign_retired": True}
    atomic_write_json(root / "data/context_annotation/active_campaign.json", active)
    service = ContextAnnotationService(corpus_manifest=manifest_path)
    status = service.generate_campaign_status()
    report = {"schema_version": "context-raw-reset-v1", "completed_at": utc_now_iso(), "dataset_id": manifest.dataset_id,
              "retired_session_ids": old_ids, "retired_session_count": 57, "removed_paths_this_run": removed_paths,
              "recovery_backup": recovery, "active_campaign": active, "historical_inventory_retained_for_provenance": str(old_manifest_path),
              "raw_source_hashes_preserved": True, "human_annotations_preserved": True,
              "rule_engine_sha256": before_rule, "source_count": len(records),
              "total_input_frames": sum(r["input_frames"] for r in records), "total_canonical_samples": sum(r["canonical_samples"] for r in records),
              "operating_proposals": sum(r["operating_proposals"] for r in records), "event_markers": sum(r["event_markers"] for r in records),
              "pending_human_review": sum(r["pending"] for r in records), "accepted": sum(r["accepted"] for r in records),
              "processing_time_s": perf_counter()-started, "campaign_status": status, "sessions": records}
    if not retirement_path.exists():
        atomic_write_json(retirement_path, {key: report[key] for key in ("completed_at", "retired_session_ids", "retired_session_count", "recovery_backup", "historical_inventory_retained_for_provenance")})
    atomic_write_json(campaign / "exports/raw_reset_autolabel.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=repo_root() / "real_data/fulldata_afterclean")
    parser.add_argument("--backup-dir", type=Path, default=repo_root().parent / "ecuread-analyzer-backups")
    parser.add_argument("--delete-legacy-57", action="store_true")
    args = parser.parse_args()
    result = reset_campaign(args.input_dir, args.backup_dir, delete_legacy=args.delete_legacy_57)
    print(json.dumps({key: result[key] for key in ("retired_session_count", "source_count", "total_input_frames", "total_canonical_samples", "operating_proposals", "event_markers", "pending_human_review", "accepted", "processing_time_s")}, indent=2))


if __name__ == "__main__":
    main()
