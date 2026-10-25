"""Versioned RAW-first campaign import and conservative old-corpus lineage."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from app.context_annotation.corpus import manifest_sources, read_curated_manifest, repo_root
from app.context_annotation.models import AttestedRawCorpusManifest, CuratedCorpusEntry, RawCorpusManifest
from app.context_annotation.pilot_quality import content_sequences
from app.context_annotation.session_loader import load_source
from app.context_annotation.speed import read_raw_capture
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso
from app.ecu.profiles.honda_keihin_71_17 import HEADER, PRODUCTION_DECODER_ID, PRODUCTION_DECODER_VERSION, validate_normalized_frame


DATASET_ID = "ecuread-filtered-raw-18-v1"
EXPECTED_IDS = {f"session_{n:06d}" for n in [*range(1, 8), *range(14, 25)]}
DEFAULT_CAMPAIGN = Path("data/context_annotation/campaigns") / DATASET_ID


def protected_snapshot(campaign: Path) -> dict:
    root = repo_root()
    paths = [p for directory in ("data/context_annotation", "data/telemetry", "data/entities", "data/models", "data/evaluation", "real_data")
             for p in (root / directory).rglob("*") if p.is_file() and not p.resolve().is_relative_to(campaign.resolve())]
    paths.extend(root / name for name in ("ecuread_raw_18_corpus_bundle.zip", "ecu_raw_18_manifest.json", "data/calibration/session_labels.csv"))
    return {str(p.relative_to(root)): file_sha256(p) for p in paths if p.is_file()}


def verify_snapshot(before: dict) -> dict:
    root = repo_root()
    changed = [path for path, digest in before.items() if not (root / path).is_file() or file_sha256(root / path) != digest]
    return {"protected_file_count": len(before), "unchanged": not changed, "changed": changed}


def inspect_raw(payload: bytes) -> tuple[list[bytes], list[float]]:
    frames, times = [], []
    for number, line in enumerate(payload.splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        frame = bytes.fromhex(row["raw_hex"])
        if len(frame) != 24 or list(frame[:4]) != HEADER or not validate_normalized_frame(list(frame)).parse_valid:
            raise ValueError(f"invalid native frame at line {number}")
        time = row["elapsed_ms"]
        if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(time) or time < 0:
            raise ValueError(f"invalid timestamp at line {number}")
        if times and not 0 < time - times[-1] <= 1000:
            raise ValueError(f"nonmonotonic timestamp or gap >1s at line {number}")
        frames.append(frame)
        times.append(time)
    if not frames:
        raise ValueError("empty original capture")
    return frames, times


def import_archive(archive: Path, local_input: Path, campaign: Path, *, expected_ids=EXPECTED_IDS) -> tuple[RawCorpusManifest, dict]:
    existing_path = campaign / "corpus_manifest.json"
    existing = read_curated_manifest(existing_path) if existing_path.exists() else None
    if isinstance(existing, AttestedRawCorpusManifest):
        from app.context_annotation.attestation import verify_attestation
        verify_attestation(existing_path, expected_ids=expected_ids)
    with zipfile.ZipFile(archive) as bundle:
        supplied = json.loads(bundle.read("manifest.json"))
        entries = supplied["source_entries"]
        if len(entries) != supplied["source_count"] or {e["source_session_id"] for e in entries} != expected_ids:
            raise ValueError("archive source identities/count differ from the explicitly intended set")
        members = [e["relative_path"] for e in entries]
        raw_members = [name for name in bundle.namelist() if name.startswith("raw/") and not name.endswith("/")]
        if (len(members) != len(set(members)) or len(raw_members) != len(set(raw_members))
                or set(members) != set(raw_members)):
            raise ValueError("archive has duplicate or unlisted RAW members")
        rows, sequences, digests = [], {}, set()
        payloads = {}
        for entry in entries:
            sid, member = entry["source_session_id"], entry["relative_path"]
            if member != f"raw/{sid}.jsonl" or not sid.startswith("session_") or not sid[8:].isdigit():
                raise ValueError("unsafe or renamed source identity in archive")
            payload = bundle.read(member)
            digest = hashlib.sha256(payload).hexdigest()
            if digest != entry["sha256"] or len(payload) != entry["file_size_bytes"] or digest in digests:
                raise ValueError(f"source SHA/size mismatch or duplicate: {sid}")
            digests.add(digest)
            local = local_input / Path(member).name
            if not local.is_file() or file_sha256(local) != digest:
                raise ValueError(f"local staged source does not match archive: {sid}")
            frames, times = inspect_raw(payload)
            duration = (times[-1] - times[0]) / 1000
            if len(frames) != entry["frame_count"] or abs(duration - entry["duration_s"]) > 1e-6:
                raise ValueError(f"source count/duration mismatch: {sid}")
            destination = campaign / member
            if destination.exists() and file_sha256(destination) != digest:
                raise ValueError(f"existing imported source SHA mismatch: {sid}; never overwritten")
            rows.append({"session_id": sid, "source_sha256": digest, "input_frame_count": len(frames),
                         "duration_s": duration, "invalid_frames": 0, "timestamp_order": "strictly_increasing",
                         "max_interval_ms": max((b - a for a, b in zip(times, times[1:])), default=0),
                         "speed_candidate_available_count": len(frames), "archive_member": member,
                         "local_staging_path": str(local), "imported_path": str(destination),
                         "import_status": "reused_identical" if destination.exists() else "imported"})
            historical = repo_root() / "real_data/real_run" / Path(member).name
            if sid in {f"session_{i:06d}" for i in range(1, 8)}:
                rows[-1]["historical_original_sha_match"] = historical.is_file() and file_sha256(historical) == digest
                if not rows[-1]["historical_original_sha_match"]:
                    raise ValueError(f"historical 001-007 source hash mismatch: {sid}")
            sequences[sid] = frames
            payloads[member] = payload
        # Verify complete 16-frame blocks at any offset, not just file prefixes.
        index, overlaps = {}, {}
        for sid, frames in sequences.items():
            for offset in range(max(0, len(frames) - 15)):
                block = b"".join(frames[offset:offset + 16])
                digest = hashlib.sha256(block).digest()
                previous = index.get(digest)
                if previous and previous[0] != sid:
                    other, start = previous
                    if sequences[other][start:start + 16] == frames[offset:offset + 16]:
                        overlaps.setdefault(tuple(sorted((sid, other))), {"left": other, "right": sid, "left_offset": start, "right_offset": offset, "frames": 16})
                else:
                    index[digest] = (sid, offset)
        for member, payload in payloads.items():
            destination = campaign / member
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                with bundle.open(member) as src, destination.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                destination.chmod(0o444)
        archive_sha = file_sha256(archive)
        manifest_path = campaign / "corpus_manifest.json"
        created_at = read_curated_manifest(manifest_path).created_at if manifest_path.exists() else utc_now_iso()
        root = repo_root()
        manifest = RawCorpusManifest(dataset_id=DATASET_ID, source_count=len(rows), archive_sha256=archive_sha,
            created_at=created_at, source_root=str(campaign / "raw"),
            selection_rule="Exactly the 18 researcher-supplied SHA-pinned original RAW files; absent 008-013 are intentional. No augmentation or automatic discovery.",
            capture_independence={"verified_independent_physical_captures": None, "status": "unresolved",
                                  "user_attestation": supplied["capture_independence"].get("user_attestation"),
                                  "technical_verification": "Distinct file hashes and full-frame overlap checks do not establish physical capture independence."},
            sessions=[CuratedCorpusEntry(session_id=row["session_id"], source_path=str(Path(row["imported_path"]).resolve().relative_to(root)) if Path(row["imported_path"]).resolve().is_relative_to(root) else str(Path(row["imported_path"]).resolve()),
                source_sha256=row["source_sha256"], reason_included="Explicit researcher-selected original RAW; archive, staging and imported hashes match.",
                origin={"recording_type": "user_attested_captured", "technical_capture_identity_verified": False,
                        "capture_group_id": "raw-source:" + row["source_sha256"], "physical_capture_group_id": None,
                        "evaluation_eligible": False, "evaluation_exclusion": "physical_capture_group_unverified",
                        "sample_count": row["input_frame_count"], "duration_s": row["duration_s"],
                        "archive_member": row["archive_member"], "source_origin": row["local_staging_path"]}) for row in rows])
        if isinstance(existing, AttestedRawCorpusManifest):
            if (existing.archive_sha256 != manifest.archive_sha256 or
                    {e.session_id: e.source_sha256 for e in existing.sessions} != {e.session_id: e.source_sha256 for e in manifest.sessions}):
                raise ValueError("re-import would change the exact attested source inventory")
            return existing, json.loads((campaign / "exports/input_integrity.json").read_text())
        atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
        audit = {"schema_version": "ctx22-raw-input-integrity-v1", "generated_at": utc_now_iso(),
                 "archive_path": str(archive), "archive_sha256": archive_sha, "source_count": len(rows),
                 "total_frames": sum(r["input_frame_count"] for r in rows), "total_duration_s": sum(r["duration_s"] for r in rows),
                 "unique_source_hashes": len(digests), "invalid_frames": 0, "backwards_timestamps": 0, "gaps_over_1s": 0,
                 "exact_16_frame_cross_file_overlaps": list(overlaps.values()), "independent_physical_captures": None, "sessions": rows}
        atomic_write_json(campaign / "exports/input_integrity.json", audit)
        return manifest, audit


def reconcile_old(campaign: Path) -> dict:
    root = repo_root()
    old_manifest = root / "data/context_annotation/curated_corpus.json"
    previous_path = root / "data/context_annotation/exports/ctx2_provenance_reconciliation.json"
    previous = json.loads(previous_path.read_text())
    if previous["corpus_manifest_sha256"] != file_sha256(old_manifest):
        raise ValueError("old lineage report does not match the pinned old manifest")
    old = {}
    for source in manifest_sources(old_manifest):
        if source.sha256 != source.expected_sha256:
            raise ValueError(f"old pinned source SHA mismatch: {source.source_id}")
        old[source.source_id] = load_source(source)
    signatures, embedded = content_sequences(old)
    originals, index = {}, defaultdict(list)
    for source in manifest_sources(campaign / "corpus_manifest.json"):
        capture = read_raw_capture(source.path, source.sha256)
        originals[source.source_id] = (source, capture)
        for offset, signature in enumerate(capture.signatures):
            index[signature].append((source.source_id, offset))
    rows = []
    for old_record in previous["sessions"]:
        sid = old_record["session_id"]
        sequence = signatures[sid]
        matches = []
        for parent, offset in index.get(sequence[0], []) if sequence else []:
            source, capture = originals[parent]
            if capture.signatures[offset:offset + len(sequence)] != sequence:
                continue
            canonical_times = old[sid].elapsed_s
            raw_times = capture.timestamps_ms[offset:offset + len(sequence)]
            if len(raw_times) != len(sequence) or any(t is None for t in raw_times):
                continue
            error = max(abs((t - raw_times[0]) / 1000 - c) for t, c in zip(raw_times, canonical_times, strict=True))
            if error > .005:
                continue
            # If RAW was embedded, require all original frame bytes too.
            raw_samples = [(s.get("quality_flags") or {}).get("raw_hex") for s in old[sid].samples]
            byte_match = None
            if any(raw_samples):
                original_rows = [json.loads(line) for line in Path(source.path).read_text().splitlines() if line.strip()]
                from app.audit.loading import parse_raw_hex
                from app.ecu.profiles.honda_keihin_71_17 import normalize_frame
                byte_match = all(value and bytes(normalize_frame(parse_raw_hex(value))[0]) == bytes.fromhex(record["raw_hex"])
                                 for value, record in zip(raw_samples, original_rows[offset:offset + len(sequence)], strict=True))
                if not byte_match:
                    continue
            matches.append({"new_session_id": parent, "new_source_sha256": source.sha256, "sample_offset": offset,
                            "sample_count": len(sequence), "time_offset_s": (raw_times[0] - capture.timestamps_ms[0]) / 1000,
                            "end_time_offset_s": (raw_times[-1] - capture.timestamps_ms[0]) / 1000,
                            "relative_timestamp_max_error_s": error, "complete_embedded_raw_byte_match": byte_match,
                            "basis": "Complete six synchronized production signals, four reserved bytes, and every relative timestamp; embedded RAW independently redecoded and all 24 bytes matched where available."})
        synthetic = old_record["classification"] == "synthetic/derived"
        unique = len(matches) == 1 and not synthetic
        mismatch = (embedded.get(sid) or {}).get("canonical_vs_redecoded_raw_mismatch_samples_by_signal", {})
        new_id = matches[0]["new_session_id"] if unique else None
        if synthetic:
            classification = "synthetic/demo"
        elif unique:
            full = len(sequence) == len(originals[new_id][1].signatures)
            classification = "replay" if full and embedded.get(sid) else "alias" if full and old_record["classification"] == "alias" else "full" if full else "split"
        else:
            classification = "outside_selected_corpus" if old_record["evaluation_eligible"] else "unresolved"
        annotation = root / "data/context_annotation/annotations" / f"{sid}.context.json"
        decoder = old[sid].metadata.decoder_provenance
        decoder_equivalent = (decoder.get("decoder_id") == PRODUCTION_DECODER_ID
                              and decoder.get("decoder_version") == PRODUCTION_DECODER_VERSION
                              and decoder.get("telemetry_schema_version") == "canonical-telemetry-v2" and not mismatch)
        rows.append({"old_session_id": sid, "old_source_sha256": old[sid].metadata.source.sha256,
                     "previous_classification": old_record["classification"], "previous_evaluation_eligible": old_record["evaluation_eligible"],
                     "classification": classification, "source_link_verified": unique, "new_session_id": new_id,
                     "matches": matches, "decoder_contract_mismatch_samples": mismatch,
                     "old_decoder_provenance": decoder, "decoder_contract_equivalent": decoder_equivalent,
                     "new_decoder_provenance": {"decoder_id": PRODUCTION_DECODER_ID, "decoder_version": PRODUCTION_DECODER_VERSION,
                                                "telemetry_schema_version": "canonical-telemetry-v2"},
                     "annotation_present": annotation.is_file(), "annotation_sha256": file_sha256(annotation) if annotation.is_file() else None,
                     "annotation_migration_candidate": bool(unique and decoder_equivalent and annotation.is_file()),
                     "annotations_migrated": False,
                     "new_campaign_evaluation_eligible": False,
                     "correlation_group_id": "raw-source:" + matches[0]["new_source_sha256"] if unique else old_record["correlation_group_id"],
                     "reason": "Technical source linkage proved; capture independence remains unverified. Legacy values/outputs remain unchanged." if unique else "Synthetic/demo excluded." if synthetic else "Not uniquely linked to one of the selected 18 originals; prior quarantine/selection retained."})
    report = {"schema_version": "ctx22-old-to-raw-lineage-v1", "generated_at": utc_now_iso(),
              "old_manifest_sha256": file_sha256(old_manifest), "prior_lineage_sha256": file_sha256(previous_path),
              "new_manifest_sha256": file_sha256(campaign / "corpus_manifest.json"), "old_entry_count": len(rows),
              "classification_counts": dict(Counter(row["classification"] for row in rows)),
              "linked_entries": sum(row["source_link_verified"] for row in rows),
              "excluded_old_entry_ids": [row["old_session_id"] for row in rows if not row["source_link_verified"]],
              "unresolved_old_entry_ids": [row["old_session_id"] for row in rows if row["classification"] == "unresolved"],
              "verified_independent_physical_captures": None, "sessions": rows}
    atomic_write_json(campaign / "exports/old_to_raw_lineage.json", report)
    with (campaign / "exports/old_to_raw_lineage.csv").open("w", newline="") as handle:
        columns = ["old_session_id", "classification", "new_session_id", "source_link_verified", "correlation_group_id", "annotation_present", "annotation_migration_candidate"]
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return report


def validate_campaign_split(manifest_path: Path, split: dict, *, policy: str = "strict_machine_verified") -> dict:
    if policy == "operator_attested_capture_groups_v1":
        from app.context_annotation.attestation import validate_attested_split
        return validate_attested_split(manifest_path, split)
    if policy != "strict_machine_verified":
        raise ValueError("unknown evaluation policy")
    manifest = read_curated_manifest(manifest_path)
    records = {entry.session_id: entry for entry in manifest.sessions}
    groups, seen = {}, set()
    for partition, ids in split.items():
        if not isinstance(ids, list):
            raise ValueError("partition must contain a list of source session IDs")
        for sid in ids:
            if sid not in records or sid in seen:
                raise ValueError(f"unknown or repeated source: {sid}")
            entry = records[sid]
            if not entry.origin.get("evaluation_eligible") or not entry.origin.get("physical_capture_group_id"):
                raise ValueError(f"quarantined physical capture provenance: {sid}")
            group = entry.origin["physical_capture_group_id"]
            if group in groups and groups[group] != partition:
                raise ValueError(f"physical capture group crosses partitions: {sid}")
            groups[group] = partition
            seen.add(sid)
    if not seen:
        raise ValueError("empty split is not an evaluation partition")
    return {"valid": True, "sources": len(seen), "capture_groups": len(groups)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path("ecuread_raw_18_corpus_bundle.zip"))
    parser.add_argument("--local-input", type=Path, default=Path("real_data/fulldata_afterclean"))
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--validate-split", type=Path, help="Validate a proposed partition without importing or changing the campaign")
    parser.add_argument("--evaluation-policy", choices=["strict_machine_verified", "operator_attested_capture_groups_v1"], default="strict_machine_verified")
    parser.add_argument("--generate-suggestions", action="store_true")
    args = parser.parse_args()
    if args.validate_split:
        print(json.dumps(validate_campaign_split(args.campaign_dir / "corpus_manifest.json", json.loads(args.validate_split.read_text()), policy=args.evaluation_policy), indent=2))
        return
    campaign = args.campaign_dir
    baseline_path = campaign / "exports/read_only_baseline.json"
    before = json.loads(baseline_path.read_text())["sha256"] if baseline_path.exists() else protected_snapshot(campaign)
    atomic_write_json(baseline_path, {"generated_at": utc_now_iso(), "sha256": before})
    manifest, audit = import_archive(args.archive, args.local_input, campaign)
    lineage = (json.loads((campaign / "exports/old_to_raw_lineage.json").read_text())
               if isinstance(manifest, AttestedRawCorpusManifest) else reconcile_old(campaign))
    if args.generate_suggestions:
        from app.context_annotation.service import ContextAnnotationService
        service = ContextAnnotationService(corpus_manifest=campaign / "corpus_manifest.json")
        for entry in manifest.sessions:
            service.review.generate(entry.session_id)
        service.generate_campaign_status()
    invariance = verify_snapshot(before)
    atomic_write_json(campaign / "exports/source_invariance.json", invariance)
    if not invariance["unchanged"]:
        raise ValueError(f"protected sources changed: {invariance['changed']}")
    print(json.dumps({"dataset_id": manifest.dataset_id, "source_count": audit["source_count"], "frames": audit["total_frames"],
                      "lineage": lineage["classification_counts"], "unresolved_old_entries": lineage["unresolved_old_entry_ids"], "invariance": invariance}, indent=2))


if __name__ == "__main__":
    main()
