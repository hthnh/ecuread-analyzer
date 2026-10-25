from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.config import Settings
from app.context_annotation.corpus import read_curated_manifest
from app.context_annotation.models import (
    ANNOTATION_SCHEMA_VERSION,
    DEFAULT_DATASET_ID,
    FREEZE_SCHEMA_VERSION,
    OPERATING_VALUES, THERMAL_PHASE_VALUES, EVENT_VALUES,
    RawCorpusManifest,
)
from app.context_annotation.validation import validate_document
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import AnnotationStorage, atomic_write_json, file_sha256, source_tree_sha256, utc_now_iso


class FreezeValidationError(ValueError):
    pass


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _validate_unique_hashes(source_hashes: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str, str]] = []
    for item in source_hashes:
        sha = item["source_sha256"]
        session_id = item["session_id"]
        if sha in seen:
            duplicates.append((sha, seen[sha], session_id))
        else:
            seen[sha] = session_id
    if duplicates:
        detail = ", ".join(f"{sha}: {left}/{right}" for sha, left, right in duplicates)
        raise FreezeValidationError(f"duplicate source SHA values in curated corpus: {detail}")


def freeze_dataset(
    *,
    corpus_manifest: Path,
    output: Path,
    data_dir: Path = Path("data"),
    annotations_dir: Path | None = None,
    created_at: str | None = None,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    manifest = read_curated_manifest(corpus_manifest)
    if isinstance(manifest, RawCorpusManifest):
        raise FreezeValidationError("RAW campaign is an unfrozen research registry; physical capture groups and annotation review must be resolved before a new freeze contract is defined")
    storage = AnnotationStorage(
        data_dir=data_dir,
        input_dir=data_dir / "context_annotation" / "input",
        annotations_dir=annotations_dir or data_dir / "context_annotation" / "annotations",
        exports_dir=data_dir / "context_annotation" / "exports",
    )
    service = ContextAnnotationService(
        settings=Settings(data_dir=data_dir, model_dir=data_dir / "models"),
        storage=storage,
        corpus_manifest=corpus_manifest,
    )
    listing = service.list_sessions()
    campaign_status = service.build_campaign_status(listing)

    if listing["summary"]["total_sessions"] != manifest.independent_captured_sessions:
        raise FreezeValidationError(
            "curated session count mismatch: "
            f"{listing['summary']['total_sessions']} != {manifest.independent_captured_sessions}"
        )
    bad_sources = [
        item
        for item in listing["sessions"]
        if item["parse_status"] in {"source_missing", "source_hash_mismatch"}
    ]
    if bad_sources:
        detail = ", ".join(f"{item['session_id']}={item['parse_status']}" for item in bad_sources)
        raise FreezeValidationError(f"cannot freeze with missing or SHA-mismatched sources: {detail}")
    if listing["summary"]["parse_failures"]:
        detail = ", ".join(
            f"{item['session_id']}={item['parse_status']}"
            for item in listing["sessions"]
            if item["parse_status"] != "ok"
        )
        raise FreezeValidationError(f"cannot freeze with parse failures: {detail}")
    if not allow_incomplete and listing["summary"]["fully_annotated"] != manifest.independent_captured_sessions:
        remaining = manifest.independent_captured_sessions - listing["summary"]["fully_annotated"]
        raise FreezeValidationError(f"cannot freeze incomplete annotation campaign: {remaining} sessions remaining")

    source_hashes: list[dict[str, Any]] = []
    annotation_hashes: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    for entry in manifest.sessions:
        source_path = Path(entry.source_path)
        actual_source_sha = source_tree_sha256(source_path)
        if actual_source_sha != entry.source_sha256:
            raise FreezeValidationError(
                f"source SHA mismatch for {entry.session_id}: {actual_source_sha} != {entry.source_sha256}"
            )
        source_hashes.append(
            {
                "session_id": entry.session_id,
                "source_path": entry.source_path,
                "source_sha256": entry.source_sha256,
            }
        )
        annotation_path = storage.annotation_path(entry.session_id)
        document = storage.read_annotation(entry.session_id)
        if document is not None:
            try:
                validate_document(document, duration_s=next(i["duration_s"] for i in listing["sessions"] if i["source_id"] == entry.session_id),
                                  source_sha256=entry.source_sha256)
            except ValueError as exc:
                raise FreezeValidationError(f"invalid annotations for {entry.session_id}: {exc}") from exc
        annotation_sha = file_sha256(annotation_path) if annotation_path.exists() else None
        annotation_hashes.append(
            {
                "session_id": entry.session_id,
                "annotation_path": str(annotation_path),
                "annotation_sha256": annotation_sha,
                "annotation_present": document is not None,
            }
        )
        summary = next(item for item in listing["sessions"] if item["source_id"] == entry.session_id)
        sessions.append(
            {
                "session_id": entry.session_id,
                "source_path": entry.source_path,
                "source_sha256": entry.source_sha256,
                "duration_s": summary["duration_s"],
                "sample_count": summary["sample_count"],
                "annotation_coverage": summary["annotation_coverage"],
                "session_labels": document.session_labels.model_dump(mode="json") if document is not None else None,
                "segments": [segment.model_dump(mode="json") for segment in document.segments] if document is not None else [],
                "events": [event.model_dump(mode="json") for event in document.events] if document is not None else [],
                "annotation_schema_version": document.schema_version if document else None,
                "annotation_sha256": annotation_sha,
            }
        )

    _validate_unique_hashes(source_hashes)
    missing_annotations = [item["session_id"] for item in annotation_hashes if not item["annotation_present"]]
    if missing_annotations and not allow_incomplete:
        raise FreezeValidationError(
            "cannot freeze with missing annotation files: " + ", ".join(missing_annotations)
        )

    created = created_at or utc_now_iso()
    deterministic_payload = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "dataset_id": manifest.dataset_id or DEFAULT_DATASET_ID,
        "independent_captured_sessions": manifest.independent_captured_sessions,
        "corpus_manifest_path": str(corpus_manifest),
        "corpus_manifest_sha256": file_sha256(corpus_manifest),
        "annotation_schema_version": (
            next(iter({s["annotation_schema_version"] for s in sessions if s["annotation_schema_version"]}))
            if len({s["annotation_schema_version"] for s in sessions if s["annotation_schema_version"]}) == 1
            else "mixed-v1-v2" if any(s["annotation_schema_version"] for s in sessions) else None
        ),
        "annotation_schema_versions": sorted({s["annotation_schema_version"] for s in sessions if s["annotation_schema_version"]}),
        "taxonomy": {
            "operating_context": list(OPERATING_VALUES),
            "thermal_phase": list(THERMAL_PHASE_VALUES),
            "event": list(EVENT_VALUES),
            "engine": ["off", "running", "unknown"],
            "throttle": ["closed", "low", "mid", "high", "unknown"],
            "dynamics": ["steady", "opening", "closing", "transition", "unknown"],
            "thermal": ["cold", "warming", "stable", "unknown"],
            "capture_type": ["road", "stationary", "calibration", "unknown"],
            "start_thermal_state": ["cold", "warm", "unknown"],
        },
        "coverage_stats": {
            "total_curated_sessions": campaign_status["total_curated_sessions"],
            "unstarted": campaign_status["unstarted"],
            "partial": campaign_status["partial"],
            "complete": campaign_status["complete"],
            "total_corpus_duration_s": campaign_status["total_corpus_duration_s"],
            "annotated_duration_s": campaign_status["annotated_duration_s"],
            "explicitly_unknown_duration_s": campaign_status["explicitly_unknown_duration_s"],
            "truly_unannotated_duration_s": campaign_status["truly_unannotated_duration_s"],
        },
        "source_hashes": source_hashes,
        "annotation_hashes": annotation_hashes,
        "sessions": sessions,
    }
    fingerprint = _stable_hash(deterministic_payload)
    freeze_payload = {
        **deterministic_payload,
        "created_at": created,
        "content_fingerprint_sha256": fingerprint,
        "freeze_complete": listing["summary"]["fully_annotated"] == manifest.independent_captured_sessions,
    }

    output.mkdir(parents=True, exist_ok=True)
    storage.write_export_json("annotation_campaign_status.json", campaign_status)

    atomic_write_json(output / "dataset_manifest.json", freeze_payload)
    atomic_write_json(output / "source_hashes.json", {"source_hashes": source_hashes})
    atomic_write_json(output / "annotation_hashes.json", {"annotation_hashes": annotation_hashes})
    atomic_write_json(output / "coverage_stats.json", freeze_payload["coverage_stats"])
    atomic_write_json(output / "annotation_campaign_status.json", campaign_status)
    _atomic_write_text(output / "content_fingerprint.sha256", fingerprint + "\n")
    return freeze_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze a context annotation dataset snapshot.")
    parser.add_argument("--corpus-manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--annotations-dir", default=None)
    parser.add_argument("--created-at", default=None)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    try:
        payload = freeze_dataset(
            corpus_manifest=Path(args.corpus_manifest),
            output=Path(args.output),
            data_dir=Path(args.data_dir),
            annotations_dir=Path(args.annotations_dir) if args.annotations_dir else None,
            created_at=args.created_at,
            allow_incomplete=args.allow_incomplete,
        )
    except FreezeValidationError as exc:
        raise SystemExit(f"freeze failed: {exc}") from exc
    print(json.dumps({"output": args.output, "content_fingerprint_sha256": payload["content_fingerprint_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
