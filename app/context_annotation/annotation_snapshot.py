"""Create an annotation-only research snapshot after complete explicit human review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.context_annotation.attestation import verify_attestation
from app.context_annotation.freeze_dataset import FreezeValidationError, _stable_hash
from app.context_annotation.models import ANNOTATION_SCHEMA_VERSION_V2, TAXONOMY_VERSION_V2
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso
from app.context_annotation.validation import covered_duration, validate_document


def annotation_snapshot(*, corpus_manifest: Path, output: Path, created_at: str | None = None) -> dict:
    manifest, attestation, groups = verify_attestation(corpus_manifest)
    service = ContextAnnotationService(corpus_manifest=corpus_manifest)
    watched = [corpus_manifest, Path(manifest.operator_attestation["path"]), Path(manifest.operator_attestation["capture_groups_path"])]
    watched += [path for e in manifest.sessions for path in
                (service.storage.annotation_path(e.session_id), service.storage.suggestion_path(e.session_id))]
    before = {path: file_sha256(path) if path.is_file() else None for path in watched}
    sessions, remaining = [], []
    for entry in manifest.sessions:
        document = service.storage.read_annotation(entry.session_id)
        if document is None:
            remaining.append(entry.session_id)
            continue
        loaded = service.load_session(entry.session_id)
        proposals = service.review.read(entry.session_id, loaded=loaded)
        duration = loaded.metadata.duration_s
        if (loaded.metadata.status != "ok" or document is None or not proposals
                or proposals["source_changed"] or proposals["raw_evidence_changed"]
                or covered_duration(document.segments, duration) < duration - 1e-6
                or any(p["review_status"] in {"pending", "uncertain"} for p in proposals["segments"] + proposals["events"])):
            remaining.append(entry.session_id)
            continue
        validate_document(document, duration_s=duration, source_sha256=entry.source_sha256)
        if document.schema_version != ANNOTATION_SCHEMA_VERSION_V2:
            raise FreezeValidationError("annotation snapshot requires the unchanged CTX2 schema")
        labels = document.segments + document.events
        if any(item.human_confirmation is None or not item.human_confirmation.reviewer.strip() for item in labels):
            raise FreezeValidationError(f"missing named human review provenance: {entry.session_id}")
        accepted = {item.suggestion_id for item in labels if item.suggestion_id}
        for proposal in proposals["segments"] + proposals["events"]:
            if not proposal["human_confirmation"] or not any(h["action"] in {"accept", "reject"} and h.get("reviewer", "").strip() for h in proposal["review_history"]):
                raise FreezeValidationError(f"suggestion lacks explicit human decision: {entry.session_id}")
            if proposal["review_status"] == "accepted" and proposal["id"] not in accepted:
                raise FreezeValidationError("accepted suggestion is missing its authoritative annotation")
        annotation_path = service.storage.annotation_path(entry.session_id)
        suggestion_path = service.storage.suggestion_path(entry.session_id)
        sessions.append({"session_id": entry.session_id, "source_path": entry.source_path, "source_sha256": entry.source_sha256,
                         "physical_capture_group_id": entry.origin["physical_capture_group_id"],
                         "confirmed_session_metadata": entry.origin.get("confirmed_session_metadata", {}),
                         "decoder_provenance": loaded.metadata.decoder_provenance, "duration_s": duration,
                         "annotation_sha256": file_sha256(annotation_path), "suggestions_sha256": file_sha256(suggestion_path),
                         "rule_version": proposals["rule_version"], "rule_config_sha256": proposals["config_sha256"],
                         "annotation": document.model_dump(mode="json"),
                         "review_decisions": [{key: p[key] for key in ("id", "review_status", "review_history", "human_confirmation")} for p in proposals["segments"] + proposals["events"]],
                         "coverage": {"annotated_duration_s": covered_duration(document.segments, duration),
                                      "explicitly_unknown_duration_s": service._explicit_unknown_duration(document.segments, duration),
                                      "truly_unannotated_duration_s": 0.0}})
    if remaining:
        raise FreezeValidationError(f"human review incomplete or uncertain: {len(remaining)} sessions remaining: {', '.join(remaining)}")
    stable = {"schema_version": "context-annotation-only-snapshot-v1", "dataset_id": manifest.dataset_id,
              "purpose": "annotation-only research; no training or detector accuracy evaluation performed",
              "operator_attested_independent_captures": manifest.operator_attested_independent_captures,
              "machine_verified_independent_captures": None, "context_schema_version": ANNOTATION_SCHEMA_VERSION_V2,
              "taxonomy_version": TAXONOMY_VERSION_V2, "corpus_manifest_sha256": file_sha256(corpus_manifest),
              "operator_attestation": attestation, "operator_attestation_sha256": manifest.operator_attestation["sha256"],
              "capture_groups": groups, "sessions": sessions,
              "coverage": {key: sum(s["coverage"][key] for s in sessions) for key in
                           ("annotated_duration_s", "explicitly_unknown_duration_s", "truly_unannotated_duration_s")}}
    fingerprint = _stable_hash(stable)
    payload = {**stable, "content_sha256": fingerprint, "snapshot_id": manifest.dataset_id + ":annotations:" + fingerprint,
               "created_at": created_at or utc_now_iso()}
    if any((file_sha256(path) if path.is_file() else None) != digest for path, digest in before.items()):
        raise FreezeValidationError("review inputs changed during snapshot; retry after review writes complete")
    # Revalidate original sources as well, before publishing a snapshot with source references.
    verify_attestation(corpus_manifest)
    # Exclusive creation prevents overwriting an existing research snapshot, even if empty.
    output.mkdir(parents=True, exist_ok=False)
    atomic_write_json(output / "snapshot.json", payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = annotation_snapshot(corpus_manifest=args.corpus_manifest, output=args.output)
    except (ValueError, FileExistsError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({"snapshot_id": result["snapshot_id"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
