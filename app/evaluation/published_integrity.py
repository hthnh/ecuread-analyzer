"""Verify published research inputs across an explicitly recorded code transition.

New experiment runs still require the original strict current-input guards.
Only published-result verification may consult the archived pre-refresh code.
"""
from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "data/model_refresh/authorized_code_transition.json"


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify_published_inputs(expected):
    transition = json.loads(RECORD.read_text()) if RECORD.exists() else {}
    changed, archived = [], []
    archive_path = ROOT / transition.get("archive_path", "missing-archive")
    archive = None
    try:
        for name, sha in expected.items():
            current = ROOT / name
            current_sha = digest(current) if current.is_file() else None
            if current_sha == sha:
                continue
            allowed = transition.get("files", {}).get(name, {})
            if (not name.startswith(("app/", "tests/")) or allowed.get("before_sha256") != sha
                    or allowed.get("after_sha256") != current_sha or current_sha is None):
                changed.append(name)
                continue
            if archive is None:
                if digest(archive_path) != transition.get("archive_sha256"):
                    raise ValueError("pre-refresh code archive hash mismatch")
                archive = tarfile.open(archive_path, "r")
            handle = archive.extractfile(name)
            if handle is None or hashlib.sha256(handle.read()).hexdigest() != sha:
                raise ValueError(f"archived published input mismatch: {name}")
            archived.append(name)
    finally:
        if archive:
            archive.close()
    if changed:
        raise ValueError(f"published inputs changed without exact authorized transition: {changed}")
    return {"pinned_files_checked": len(expected), "changed_files": [], "archived_code_inputs": archived,
            "historical_data_and_models_checked_in_place": True}


def record_transition():
    path = ROOT / "data/model_refresh/pre_refresh_code.tar"
    files = {}
    with tarfile.open(path, "r") as archive:
        for member in archive.getmembers():
            if not member.isfile() or "__pycache__" in Path(member.name).parts:
                continue
            before = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
            current = ROOT / member.name
            after = digest(current) if current.is_file() else None
            if before != after:
                files[member.name] = {"before_sha256": before, "after_sha256": after}
    record = {"schema_version": "authorized-code-transition-v1", "authorization": "FINAL MODEL PASS operator-approved engineering refresh",
              "archive_path": str(path.relative_to(ROOT)), "archive_sha256": digest(path),
              "files": files, "policy": "Published verification only. Strict guards for new research runs remain unchanged."}
    RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"changed_code_files": len(files), "archive_sha256": record["archive_sha256"]}))


if __name__ == "__main__":
    record_transition()
