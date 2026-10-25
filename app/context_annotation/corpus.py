from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from app.config import Settings
from app.context_annotation.models import AttestedRawCorpusManifest, CuratedCorpusEntry, CuratedCorpusManifest, RawCorpusManifest, SessionSource
from app.context_annotation.session_loader import discover_sources, load_source
from app.context_annotation.storage import atomic_write_json, source_tree_sha256, utc_now_iso


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def active_corpus_manifest(data_dir: Path) -> Path | None:
    pointer = data_dir / "context_annotation/active_campaign.json"
    if not pointer.is_file():
        return None
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "context-active-campaign-v1":
        raise ValueError("unsupported active annotation campaign")
    path = Path(payload["corpus_manifest"])
    path = path if path.is_absolute() else repo_root() / path
    from app.context_annotation.storage import file_sha256
    if not path.is_file() or file_sha256(path) != payload["corpus_manifest_sha256"]:
        raise ValueError("active annotation campaign manifest missing or SHA mismatched")
    manifest = read_curated_manifest(path)
    if manifest.dataset_id != payload["dataset_id"] or len(manifest.sessions) != payload["source_count"]:
        raise ValueError("active annotation campaign identity/count mismatch")
    return path


def _git_tracked_paths(pattern: str, *, cwd: Path) -> set[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", pattern],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _entity_session_payload(session_id: str, *, root: Path) -> dict[str, Any]:
    path = root / "data" / "entities" / "sessions" / f"{session_id}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _origin_for_session(source: SessionSource, loaded: Any, *, root: Path) -> dict[str, Any]:
    entity = _entity_session_payload(loaded.metadata.session_id, root=root)
    return {
        "vehicle_id": entity.get("vehicle_id"),
        "device_id": entity.get("device_id"),
        "source_type": entity.get("source_type") or loaded.metadata.source_format,
        "uploaded_at": entity.get("uploaded_at"),
        "created_at": loaded.metadata.capture_time,
        "decoder_id": loaded.metadata.decoder_provenance.get("decoder_id"),
        "decoder_version": loaded.metadata.decoder_provenance.get("decoder_version"),
        "telemetry_schema_version": loaded.metadata.telemetry_schema_version,
        "git_tracked": bool(source.belongs_to_curated_corpus),
    }


def audit_telemetry_corpus(
    *,
    telemetry_dir: Path = Path("data/telemetry"),
    output_path: Path = Path("data/context_annotation/exports/corpus_audit.json"),
    settings: Settings | None = None,
    expected_curated_count: int = 57,
) -> dict[str, Any]:
    root = repo_root()
    settings = settings or Settings(data_dir=Path("data"), model_dir=Path("data/models"))
    tracked_metadata = _git_tracked_paths("data/telemetry/*/metadata.json", cwd=root)
    tracked_session_ids = {
        Path(path).parent.name
        for path in tracked_metadata
    }
    sessions: list[dict[str, Any]] = []
    duplicate_sources: dict[str, list[str]] = {}
    for source in discover_sources(telemetry_dir):
        belongs = source.source_id in tracked_session_ids
        source = source.model_copy(update={"belongs_to_curated_corpus": belongs})
        loaded = load_source(source, settings)
        reason = (
            "included: tracked telemetry session in the curated source corpus"
            if belongs
            else "excluded: untracked telemetry session generated after the curated corpus was selected"
        )
        duplicate_sources.setdefault(source.sha256, []).append(loaded.metadata.session_id)
        sessions.append(
            {
                "session_id": loaded.metadata.session_id,
                "source_id": source.source_id,
                "source_path": str(Path(source.path)),
                "source_sha256": source.sha256,
                "duration_s": loaded.metadata.duration_s,
                "sample_count": loaded.metadata.sample_count,
                "origin": _origin_for_session(source, loaded, root=root),
                "belongs_to_intended_curated_corpus": belongs,
                "reason_included_excluded": reason,
                "status": loaded.metadata.status,
                "error": loaded.metadata.error,
            }
        )
    duplicate_sources = {
        sha: ids for sha, ids in duplicate_sources.items() if len(ids) > 1
    }
    payload = {
        "schema_version": "context-corpus-audit-v1",
        "generated_at": utc_now_iso(),
        "telemetry_dir": str(telemetry_dir),
        "expected_curated_count": expected_curated_count,
        "discovered_session_count": len(sessions),
        "tracked_curated_count": sum(1 for item in sessions if item["belongs_to_intended_curated_corpus"]),
        "excluded_count": sum(1 for item in sessions if not item["belongs_to_intended_curated_corpus"]),
        "excluded_session_ids": [
            item["session_id"] for item in sessions if not item["belongs_to_intended_curated_corpus"]
        ],
        "duplicate_source_hashes": duplicate_sources,
        "sessions": sorted(sessions, key=lambda item: item["session_id"]),
    }
    atomic_write_json(output_path, payload)
    return payload


def create_curated_manifest_from_audit(
    *,
    audit: dict[str, Any],
    output_path: Path = Path("data/context_annotation/curated_corpus.json"),
    dataset_id: str = "ecu-context-v1",
) -> CuratedCorpusManifest:
    included = [
        item
        for item in audit["sessions"]
        if item["belongs_to_intended_curated_corpus"]
    ]
    manifest = CuratedCorpusManifest(
        dataset_id=dataset_id,
        independent_captured_sessions=len(included),
        created_at=utc_now_iso(),
        selection_rule=(
            "Tracked canonical telemetry sessions under data/telemetry at the CTX1.1 corpus lock; "
            "untracked/generated telemetry directories are excluded."
        ),
        excluded_sessions=[
            {
                "session_id": item["session_id"],
                "source_path": item["source_path"],
                "source_sha256": item["source_sha256"],
                "reason_excluded": item["reason_included_excluded"],
            }
            for item in audit["sessions"]
            if not item["belongs_to_intended_curated_corpus"]
        ],
        sessions=[
            CuratedCorpusEntry(
                session_id=item["session_id"],
                source_path=item["source_path"],
                source_sha256=item["source_sha256"],
                reason_included=item["reason_included_excluded"],
                origin=item["origin"],
            )
            for item in included
        ],
    )
    atomic_write_json(output_path, manifest.model_dump(mode="json"))
    return manifest


def read_curated_manifest(path: Path) -> CuratedCorpusManifest | RawCorpusManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    model = {"context-raw-corpus-v1": RawCorpusManifest,
             "context-raw-corpus-v2": AttestedRawCorpusManifest}.get(payload.get("schema_version"), CuratedCorpusManifest)
    return model.model_validate(payload)


def manifest_sources(path: Path) -> list[SessionSource]:
    manifest = read_curated_manifest(path)
    root = repo_root()
    sources: list[SessionSource] = []
    for entry in manifest.sessions:
        sources.append(_source_from_manifest_entry(entry))
    return sources


def manifest_source(path: Path, source_id: str) -> SessionSource | None:
    manifest = read_curated_manifest(path)
    for entry in manifest.sessions:
        if entry.session_id == source_id:
            return _source_from_manifest_entry(entry)
    return None


def _source_from_manifest_entry(entry: CuratedCorpusEntry) -> SessionSource:
    root = repo_root()
    source_path = Path(entry.source_path)
    resolved = source_path if source_path.is_absolute() else root / source_path
    exists = resolved.exists()
    actual_sha = source_tree_sha256(resolved) if exists else ""
    return SessionSource(
        source_id=entry.session_id,
        filename=entry.source_path,
        path=str(resolved),
        sha256=actual_sha,
        expected_sha256=entry.source_sha256,
        manifest_session_id=entry.session_id,
        manifest_reason=entry.reason_included,
        belongs_to_curated_corpus=True,
        is_directory=resolved.is_dir() if exists else entry.source_path.endswith("/"),
    )


def generate_default_curated_files() -> dict[str, Any]:
    audit = audit_telemetry_corpus()
    manifest = create_curated_manifest_from_audit(audit=audit)
    return {
        "audit_path": "data/context_annotation/exports/corpus_audit.json",
        "manifest_path": "data/context_annotation/curated_corpus.json",
        "tracked_curated_count": manifest.independent_captured_sessions,
        "excluded_session_ids": audit["excluded_session_ids"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and pin the CTX curated context corpus.")
    parser.add_argument("--telemetry-dir", default="data/telemetry")
    parser.add_argument("--audit-output", default="data/context_annotation/exports/corpus_audit.json")
    parser.add_argument("--manifest-output", default="data/context_annotation/curated_corpus.json")
    args = parser.parse_args()
    audit = audit_telemetry_corpus(
        telemetry_dir=Path(args.telemetry_dir),
        output_path=Path(args.audit_output),
    )
    manifest = create_curated_manifest_from_audit(
        audit=audit,
        output_path=Path(args.manifest_output),
    )
    print(
        json.dumps(
            {
                "audit_output": args.audit_output,
                "manifest_output": args.manifest_output,
                "discovered_session_count": audit["discovered_session_count"],
                "curated_session_count": manifest.independent_captured_sessions,
                "excluded_session_ids": audit["excluded_session_ids"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
