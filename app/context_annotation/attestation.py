"""Hash-bound collector declarations and explicitly opt-in evaluation grouping."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.context_annotation.corpus import manifest_sources, read_curated_manifest, repo_root
from app.context_annotation.models import AttestedRawCorpusManifest, RawCorpusManifest
from app.context_annotation.raw_campaign import DATASET_ID, DEFAULT_CAMPAIGN, EXPECTED_IDS, inspect_raw
from app.context_annotation.storage import atomic_write_json, file_sha256


STRICT_POLICY = "strict_machine_verified"
ATTESTED_POLICY = "operator_attested_capture_groups_v1"
LONG_IDS = {f"session_{n:06d}" for n in (16, 18, 23)}
LONG_METADATA = {"capture_type": "road", "trip_context": "out_of_city",
                 "ride_pattern": "continuous_long_ride", "metadata_source": "operator_attestation"}
LIMITATIONS = [
    "Physical independence, varied conditions and out-of-city continuous rides are collector declarations, not machine verification.",
    "SHA-256, frame validation, timestamp continuity and prior cross-file overlap checks establish file integrity, not separate physical runs.",
    "No device capture UUID, absolute acquisition clock, GPS or independently authenticated collector identity is available.",
    "Elapsed clocks restart per file. Distinct hashes and no 16-frame overlap cannot rule out all historical splitting or derivation.",
    "The declaration applies only to these exact 18 source IDs and hashes, never to unrelated, synthetic or newly discovered files.",
]


def _path(reference: str) -> Path:
    path = Path(reference)
    return path if path.is_absolute() else repo_root() / path


def _immutable_json(path: Path, payload: dict) -> None:
    if path.exists():
        if json.loads(path.read_text()) != payload:
            raise ValueError(f"versioned provenance already exists with different contents: {path}")
    else:
        atomic_write_json(path, payload)


def verify_inventory(manifest_path: Path, *, expected_ids=None) -> tuple[RawCorpusManifest, list[dict]]:
    expected_ids = EXPECTED_IDS if expected_ids is None else expected_ids
    manifest = read_curated_manifest(manifest_path)
    if not isinstance(manifest, RawCorpusManifest) or manifest.dataset_id != DATASET_ID:
        raise ValueError("attestation requires the selected RAW campaign")
    if {entry.session_id for entry in manifest.sessions} != expected_ids:
        raise ValueError("attestation source IDs differ from the exact pinned inventory")
    rows = []
    for source, entry in zip(manifest_sources(manifest_path), manifest.sessions, strict=True):
        if source.sha256 != entry.source_sha256 or not Path(source.path).is_file():
            raise ValueError(f"attested source missing or SHA mismatch: {entry.session_id}")
        frames, times = inspect_raw(Path(source.path).read_bytes())
        duration = (times[-1] - times[0]) / 1000
        if len(frames) != entry.origin["sample_count"] or abs(duration - entry.origin["duration_s"]) > 1e-6:
            raise ValueError(f"pinned count/duration mismatch: {entry.session_id}")
        rows.append({"session_id": entry.session_id, "source_path": entry.source_path,
                     "source_sha256": source.sha256, "sample_count": len(frames), "duration_s": duration,
                     "timestamp_order": "strictly_increasing", "max_interval_ms": max((b-a for a,b in zip(times,times[1:])), default=0),
                     "physical_capture_group_id": "operator-capture:" + source.sha256})
    return manifest, rows


def record_attestation(campaign: Path, attestation_date: str, *, expected_ids=None) -> AttestedRawCorpusManifest:
    expected_ids = EXPECTED_IDS if expected_ids is None else expected_ids
    date.fromisoformat(attestation_date)
    manifest_path = campaign / "corpus_manifest.json"
    manifest, sources = verify_inventory(manifest_path, expected_ids=expected_ids)
    provenance = campaign / "provenance"
    backup = provenance / "corpus_manifest.pre_ctx23.json"
    attestation_path = provenance / "operator_attestation.v1.json"
    lineage_path = campaign / "exports/old_to_raw_lineage.json"
    lineage = json.loads(lineage_path.read_text())
    if isinstance(manifest, AttestedRawCorpusManifest):
        verify_attestation(manifest_path, expected_ids=expected_ids)
        if json.loads(attestation_path.read_text())["attestation_date"] != attestation_date:
            raise ValueError("existing attestation date differs; create a new reviewed version")
        return manifest
    original_bytes = manifest_path.read_bytes()
    import hashlib
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    if lineage["new_manifest_sha256"] != original_sha:
        raise ValueError("historical lineage is not bound to the pre-attestation registry")
    old_manifest = repo_root() / "data/context_annotation/curated_corpus.json"
    if lineage["old_manifest_sha256"] != file_sha256(old_manifest):
        raise ValueError("historical source registry changed")
    integrity_path = campaign / "exports/input_integrity.json"
    integrity = json.loads(integrity_path.read_text())
    if ({r["session_id"]: r["source_sha256"] for r in integrity["sessions"]}
            != {r["session_id"]: r["source_sha256"] for r in sources}):
        raise ValueError("technical integrity inventory differs from attested sources")
    if backup.exists() and backup.read_bytes() != original_bytes:
        raise ValueError("pre-attestation registry backup differs")
    provenance.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        with backup.open("xb") as handle:
            handle.write(original_bytes)
    previous = json.loads(attestation_path.read_text()) if attestation_path.exists() else None
    record = {"schema_version": "capture-attestation-v1", "dataset_id": manifest.dataset_id,
              "attestation_id": DATASET_ID + ":operator-independence:v1",
              "attestation_source": "data_collector", "attestation_type": "operator_declared_independence",
              "attestation_date": attestation_date, "attestation_timezone": "Asia/Saigon",
              "recorded_at": previous["recorded_at"] if previous else datetime.now(ZoneInfo("Asia/Saigon")).isoformat(),
              "declaration_source": "Explicit collector confirmation in the CTX2.3 user request; no independently authenticated identity asserted.",
              "assertions": {"separate_physical_runs": len(sources), "varied_motorcycle_operating_conditions": True,
                             "no_selected_file_intentionally_split_from_another_selected_file": True,
                             "continuous_out_of_city_rides": sorted(LONG_IDS & expected_ids)},
              "operator_attested_independent_captures": len(sources), "machine_verified_independent_captures": None,
              "pre_attestation_manifest_sha256": original_sha, "historical_lineage_sha256": file_sha256(lineage_path),
              "historical_manifest_sha256": file_sha256(old_manifest), "evidence_limitations": LIMITATIONS,
              "technical_evidence": {"input_integrity_sha256": file_sha256(integrity_path),
                                     "unique_source_hashes": integrity["unique_source_hashes"],
                                     "exact_16_frame_cross_file_overlaps": integrity["exact_16_frame_cross_file_overlaps"],
                                     "invalid_frames": integrity["invalid_frames"],
                                     "continuity_checked_again_at_attestation": True},
              "sources": [{**row, "confirmed_session_metadata": LONG_METADATA if row["session_id"] in LONG_IDS else {}} for row in sources]}
    _immutable_json(attestation_path, record)
    by_id = {row["session_id"]: row for row in sources}
    overlay = []
    for row in lineage["sessions"]:
        linked = row["source_link_verified"] and row["classification"] in {"full", "split", "alias", "replay"}
        parent = by_id.get(row["new_session_id"]) if linked else None
        if parent and (len(row["matches"]) != 1 or row["matches"][0]["new_source_sha256"] != parent["source_sha256"]):
            raise ValueError("historical parent SHA ambiguity")
        old_source = repo_root() / "data/telemetry" / row["old_session_id"]
        from app.context_annotation.storage import source_tree_sha256
        if source_tree_sha256(old_source) != row["old_source_sha256"]:
            raise ValueError(f"historical source changed: {row['old_session_id']}")
        overlay.append({"session_id": row["old_session_id"], "source_sha256": row["old_source_sha256"],
                        "source_path": str(old_source), "classification": row["classification"],
                        "parent_session_id": parent["session_id"] if parent else None,
                        "parent_source_sha256": parent["source_sha256"] if parent else None,
                        "physical_capture_group_id": parent["physical_capture_group_id"] if parent else None,
                        "lineage": row["matches"][0] if parent else None,
                        "operator_evaluation_eligible": bool(parent and row["decoder_contract_equivalent"]),
                        "exclusion": None if parent and row["decoder_contract_equivalent"] else "decoder_conflict" if parent else row["classification"]})
    group_path = provenance / "capture_groups.v1.json"
    _immutable_json(group_path, {"schema_version": "attested-capture-groups-v1", "dataset_id": manifest.dataset_id,
                               "policy": ATTESTED_POLICY, "attestation_sha256": file_sha256(attestation_path),
                               "lineage_sha256": file_sha256(lineage_path), "historical_entries": overlay})
    payload = manifest.model_dump(mode="json")
    payload.update(schema_version="context-raw-corpus-v2", operator_attested_independent_captures=len(sources),
                   machine_verified_independent_captures=None, operator_evaluation_policy=ATTESTED_POLICY,
                   operator_attestation={"path": str(attestation_path.resolve()), "sha256": file_sha256(attestation_path),
                                         "capture_groups_path": str(group_path.resolve()), "capture_groups_sha256": file_sha256(group_path)})
    payload["capture_independence"] = {**payload["capture_independence"], "status": "operator_attested_not_machine_verified",
                                      "operator_attested_independent_captures": len(sources), "machine_verified_independent_captures": None}
    for entry in payload["sessions"]:
        row = by_id[entry["session_id"]]
        entry["origin"].update(physical_capture_group_id=row["physical_capture_group_id"],
                               capture_group_basis="operator_attestation", operator_evaluation_eligible=True,
                               confirmed_session_metadata=LONG_METADATA if entry["session_id"] in LONG_IDS else {})
    attested = AttestedRawCorpusManifest.model_validate(payload)
    atomic_write_json(manifest_path, attested.model_dump(mode="json"))
    verify_attestation(manifest_path, expected_ids=expected_ids)
    return attested


def verify_attestation(manifest_path: Path, *, expected_ids=None) -> tuple[AttestedRawCorpusManifest, dict, dict]:
    expected_ids = EXPECTED_IDS if expected_ids is None else expected_ids
    manifest, sources = verify_inventory(manifest_path, expected_ids=expected_ids)
    if not isinstance(manifest, AttestedRawCorpusManifest):
        raise ValueError("explicit operator attestation required")
    references = manifest.operator_attestation
    for key in ("path", "capture_groups_path"):
        digest_key = "sha256" if key == "path" else "capture_groups_sha256"
        if not _path(references[key]).is_file() or file_sha256(_path(references[key])) != references[digest_key]:
            raise ValueError("attestation/group registry SHA mismatch")
    record = json.loads(_path(references["path"]).read_text())
    groups = json.loads(_path(references["capture_groups_path"]).read_text())
    backup = manifest_path.parent / "provenance/corpus_manifest.pre_ctx23.json"
    if file_sha256(backup) != record["pre_attestation_manifest_sha256"]:
        raise ValueError("pre-attestation registry SHA mismatch")
    if (file_sha256(manifest_path.parent / "exports/old_to_raw_lineage.json") != record["historical_lineage_sha256"]
            or groups["lineage_sha256"] != record["historical_lineage_sha256"]
            or file_sha256(manifest_path.parent / "exports/input_integrity.json") != record["technical_evidence"]["input_integrity_sha256"]
            or file_sha256(repo_root() / "data/context_annotation/curated_corpus.json") != record["historical_manifest_sha256"]):
        raise ValueError("historical evidence hash mismatch")
    if (record["dataset_id"] != manifest.dataset_id or record["attestation_source"] != "data_collector"
            or record["attestation_type"] != "operator_declared_independence"
            or record["machine_verified_independent_captures"] is not None
            or record["operator_attested_independent_captures"] != len(sources)
            or groups["attestation_sha256"] != references["sha256"]):
        raise ValueError("attestation identity/count/basis mismatch")
    declared = {row["session_id"]: row for row in record["sources"]}
    if len(record["sources"]) != len(sources) or set(declared) != expected_ids:
        raise ValueError("attestation source set mismatch")
    for entry, row in zip(manifest.sessions, sources, strict=True):
        recorded = declared[row["session_id"]]
        if any(recorded[key] != row[key] for key in ("source_sha256", "physical_capture_group_id", "sample_count", "duration_s")):
            raise ValueError("source outside exact attestation")
        if entry.origin["physical_capture_group_id"] != row["physical_capture_group_id"] or not entry.origin.get("operator_evaluation_eligible"):
            raise ValueError("attested group identity mismatch")
        expected_metadata = LONG_METADATA if entry.session_id in LONG_IDS else {}
        if recorded["confirmed_session_metadata"] != expected_metadata or entry.origin.get("confirmed_session_metadata") != expected_metadata:
            raise ValueError("collector metadata differs from exact declaration")
    return manifest, record, groups


def inherited_group(parent: dict, *, parent_source_sha256: str, synthetic: bool = False) -> dict:
    """Carry provenance into a future derived record; never create augmented data."""
    if synthetic or not parent.get("operator_evaluation_eligible") or not parent.get("physical_capture_group_id"):
        raise ValueError("synthetic or unresolved parent excluded from real-capture evaluation")
    if parent["source_sha256"] != parent_source_sha256:
        raise ValueError("derived parent source SHA mismatch")
    return {"parent_session_id": parent["session_id"], "parent_source_sha256": parent_source_sha256,
            "physical_capture_group_id": parent["physical_capture_group_id"],
            "capture_group_basis": "operator_attestation", "operator_evaluation_eligible": True,
            "evaluation_eligible": False}


def session_provenance(manifest_path: Path, source_id: str, source_sha256: str) -> dict | None:
    manifest = read_curated_manifest(manifest_path)
    if not isinstance(manifest, AttestedRawCorpusManifest):
        return None
    reference = manifest.operator_attestation
    path = _path(reference["path"])
    if not path.is_file() or file_sha256(path) != reference["sha256"]:
        return {"error": "Operator attestation SHA mismatch; collector metadata unavailable"}
    record = json.loads(path.read_text())
    entry = next((r for r in record["sources"] if r["session_id"] == source_id and r["source_sha256"] == source_sha256), None)
    if entry is None:
        return {"error": "Source is outside the exact collector attestation"}
    return {"basis": "operator_attestation", "machine_verified": False, "attestation_date": record["attestation_date"],
            "physical_capture_group_id": entry["physical_capture_group_id"], "attestation_sha256": reference["sha256"],
            "confirmed_session_metadata": entry["confirmed_session_metadata"]}


def validate_attested_split(manifest_path: Path, split: dict) -> dict:
    manifest, _, overlay = verify_attestation(manifest_path)
    records = {e.session_id: {"session_id": e.session_id, "source_sha256": e.source_sha256, **e.origin} for e in manifest.sessions}
    for entry in overlay["historical_entries"]:
        if entry["session_id"] in records:
            raise ValueError("ambiguous historical source identity")
        records[entry["session_id"]] = entry
    seen, groups = set(), {}
    for partition, ids in split.items():
        if not isinstance(ids, list):
            raise ValueError("partition must contain a list of source session IDs")
        for sid in ids:
            if sid not in records or sid in seen:
                raise ValueError(f"unknown or repeated source: {sid}")
            entry = records[sid]
            if not entry.get("operator_evaluation_eligible") or not entry.get("physical_capture_group_id"):
                raise ValueError(f"unresolved, synthetic or incompatible source excluded: {sid}")
            if sid not in {e.session_id for e in manifest.sessions}:
                from app.context_annotation.storage import source_tree_sha256
                if source_tree_sha256(_path(entry["source_path"])) != entry["source_sha256"]:
                    raise ValueError(f"historical source SHA mismatch: {sid}")
            group = entry["physical_capture_group_id"]
            if group in groups and groups[group] != partition:
                raise ValueError(f"physical capture group crosses partitions: {sid}")
            groups[group] = partition
            seen.add(sid)
    if not seen:
        raise ValueError("empty split is not an evaluation partition")
    return {"valid": True, "policy": ATTESTED_POLICY, "sources": len(seen), "capture_groups": len(groups),
            "basis": "operator attestation; not independently verified physical capture metadata",
            "attestation_sha256": manifest.operator_attestation["sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--attestation-date", required=True, help="Collector declaration date, ISO YYYY-MM-DD")
    args = parser.parse_args()
    manifest = record_attestation(args.campaign_dir, args.attestation_date)
    print(json.dumps({"dataset_id": manifest.dataset_id, "operator_attested_independent_captures": manifest.operator_attested_independent_captures,
                      "machine_verified_independent_captures": None, "attestation": manifest.operator_attestation}, indent=2))


if __name__ == "__main__":
    main()
