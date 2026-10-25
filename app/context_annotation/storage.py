from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.context_annotation.models import (
    ANNOTATION_SCHEMA_VERSION,
    ContextAnnotationDocument,
    SessionLabels,
    SourceProvenance,
    SuggestionDocument,
)


SAFE_SOURCE_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def safe_source_id(value: str) -> str:
    cleaned = SAFE_SOURCE_ID_RE.sub("_", value.strip().replace("/", "__")).strip("._-")
    if not cleaned:
        cleaned = "session"
    if len(cleaned) <= 120:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:12]
    return f"{cleaned[:100]}_{digest}"


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_tree_sha256(path: str | Path) -> str:
    resolved = Path(path)
    if resolved.is_file():
        return file_sha256(resolved)
    digest = hashlib.sha256()
    for child in sorted(item for item in resolved.rglob("*") if item.is_file()):
        rel = child.relative_to(resolved).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with child.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def source_id_for_path(path: Path, input_dir: Path) -> str:
    relative = path.relative_to(input_dir)
    if path.is_file():
        relative = relative.with_suffix("")
    return safe_source_id(relative.as_posix())


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


class AnnotationStorage:
    def __init__(
        self,
        *,
        data_dir: Path = Path("data"),
        input_dir: Path | None = None,
        annotations_dir: Path | None = None,
        exports_dir: Path | None = None,
        suggestions_dir: Path | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.root = data_dir / "context_annotation"
        self.input_dir = input_dir or self.root / "input"
        self.annotations_dir = annotations_dir or self.root / "annotations"
        self.exports_dir = exports_dir or self.root / "exports"
        self.suggestions_dir = suggestions_dir or self.root / "suggestions"
        for directory in (self.input_dir, self.annotations_dir, self.exports_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def annotation_path(self, source_id: str) -> Path:
        return self.annotations_dir / f"{safe_source_id(source_id)}.context.json"

    def suggestion_path(self, source_id: str) -> Path:
        return self.suggestions_dir / f"{safe_source_id(source_id)}.suggestions.json"

    def read_suggestions(self, source_id: str) -> SuggestionDocument | None:
        path = self.suggestion_path(source_id)
        return SuggestionDocument.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None

    def write_suggestions(self, source_id: str, document: SuggestionDocument) -> Path:
        path = self.suggestion_path(source_id)
        atomic_write_json(path, document.model_dump(mode="json"))
        return path

    def read_annotation(self, source_id: str) -> ContextAnnotationDocument | None:
        path = self.annotation_path(source_id)
        if not path.exists():
            return None
        try:
            return ContextAnnotationDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise ValueError(f"annotation file is invalid: {path}: {exc}") from exc

    def write_annotation(self, document: ContextAnnotationDocument) -> Path:
        path = self.annotation_path(document.session_id)
        atomic_write_json(path, document.model_dump(mode="json"))
        return path

    def write_annotation_for_source(self, source_id: str, document: ContextAnnotationDocument) -> Path:
        path = self.annotation_path(source_id)
        atomic_write_json(path, document.model_dump(mode="json"))
        return path

    def blank_document(
        self,
        *,
        source_id: str,
        source_filename: str,
        source_sha256: str,
        session_id: str,
        decoder_provenance: dict[str, object],
        session_labels: SessionLabels | None = None,
    ) -> ContextAnnotationDocument:
        now = utc_now_iso()
        return ContextAnnotationDocument(
            schema_version=ANNOTATION_SCHEMA_VERSION,
            session_id=session_id,
            source=SourceProvenance(
                filename=source_filename,
                sha256=source_sha256,
                session_id=session_id,
                decoder_provenance=decoder_provenance,
            ),
            session_labels=session_labels or SessionLabels(),
            segments=[],
            created_at=now,
            updated_at=now,
        )

    def write_export_json(self, filename: str, payload: dict[str, Any]) -> Path:
        path = self.exports_dir / filename
        atomic_write_json(path, payload)
        return path
