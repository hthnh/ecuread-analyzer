from __future__ import annotations

import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.context_annotation.corpus import active_corpus_manifest
from app.context_annotation.raw_campaign import EXPECTED_IDS, import_archive
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256
from scripts.reset_context_raw_campaign import backup_paths, legacy_paths, reset_campaign, verify_inputs
from tests.test_native_upload_compatibility import content
from tests.test_raw_decode_analyze_api import asymmetric_native_frame
from tests.test_raw_context_campaign import bundle  # noqa: F401


def legacy_manifest(root):
    entries = [{"session_id": f"old-{n:03d}", "source_path": f"data/telemetry/old-{n:03d}",
                "source_sha256": str(n), "reason_included": "test"} for n in range(57)]
    path = root / "data/context_annotation/curated_corpus.json"
    atomic_write_json(path, {"independent_captured_sessions": 57, "created_at": "2026-10-11", "selection_rule": "test", "sessions": entries})
    return path


def test_retirement_is_scoped_to_exact_old_ids(tmp_path):
    manifest = legacy_manifest(tmp_path)
    ids, paths = legacy_paths(tmp_path, manifest)
    assert len(ids) == 57 and len(paths) == 228
    assert all("real_data" not in p.parts and "models" not in p.parts for p in paths)
    payload = json.loads(manifest.read_text())
    payload["sessions"][0]["source_path"] = "real_data/fulldata_afterclean"
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="expected telemetry"):
        legacy_paths(tmp_path, manifest)


def test_retirement_rejects_path_traversal(tmp_path):
    manifest = legacy_manifest(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["sessions"][0].update(session_id="../models", source_path="data/models")
    atomic_write_json(manifest, payload)
    with pytest.raises(ValueError, match="expected telemetry"):
        legacy_paths(tmp_path, manifest)


def test_backup_verifies_actual_dirty_contents_and_refuses_overwrite(tmp_path):
    root = tmp_path / "repo"
    source = root / "data/telemetry/old/samples.jsonl"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"user-edited-content\n")
    backup = tmp_path / "recovery/old.tar.gz"
    result = backup_paths([source.parent], root=root, output=backup)
    assert result["file_count"] == 1 and result["sha256"] == file_sha256(backup)
    with tarfile.open(backup) as archive:
        assert archive.extractfile("data/telemetry/old/samples.jsonl").read() == source.read_bytes()
    with pytest.raises(FileExistsError):
        backup_paths([source.parent], root=root, output=backup)


def test_destructive_reset_requires_explicit_flag(tmp_path):
    with pytest.raises(ValueError, match="explicit --delete-legacy-57"):
        reset_campaign(tmp_path, tmp_path, delete_legacy=False)


def test_input_inventory_rejects_changed_missing_extra_files(tmp_path):
    raw = content([asymmetric_native_frame()] * 12)
    entries = []
    for sid in sorted(EXPECTED_IDS):
        path = tmp_path / f"{sid}.jsonl"
        path.write_bytes(raw)
        entries.append(SimpleNamespace(session_id=sid, source_sha256=file_sha256(path), origin={"sample_count": 12, "duration_s": 2.75}))
    manifest = SimpleNamespace(sessions=entries)
    assert len(verify_inputs(tmp_path, manifest)) == 18
    extra = tmp_path / "extra.jsonl"
    extra.write_bytes(raw)
    with pytest.raises(ValueError, match="exact pinned"):
        verify_inputs(tmp_path, manifest)
    extra.unlink()
    target = tmp_path / f"{entries[0].session_id}.jsonl"
    target.write_bytes(raw + b"\n")
    with pytest.raises(ValueError, match="SHA mismatch"):
        verify_inputs(tmp_path, manifest)
    target.unlink()
    with pytest.raises(ValueError, match="exact pinned"):
        verify_inputs(tmp_path, manifest)


def test_active_campaign_default_and_explicit_folder_mode(bundle, tmp_path):
    manifest, _ = import_archive(*bundle[:3], expected_ids=bundle[3])
    path = bundle[2] / "corpus_manifest.json"
    data_dir = tmp_path / "data"
    pointer = data_dir / "context_annotation/active_campaign.json"
    payload = {"schema_version": "context-active-campaign-v1", "dataset_id": manifest.dataset_id,
               "source_count": 1, "corpus_manifest": str(path), "corpus_manifest_sha256": file_sha256(path)}
    atomic_write_json(pointer, payload)
    assert active_corpus_manifest(data_dir) == path
    service = ContextAnnotationService(settings=Settings(data_dir=data_dir))
    assert service.corpus_manifest == path
    assert [s.source_id for s in service._sources()] == ["session_000099"]
    payload["corpus_manifest_sha256"] = "changed"
    atomic_write_json(pointer, payload)
    with pytest.raises(ValueError, match="SHA mismatched"):
        ContextAnnotationService(settings=Settings(data_dir=data_dir))
    folder = ContextAnnotationService(settings=Settings(data_dir=data_dir), input_dir=tmp_path / "exploration")
    assert folder.corpus_manifest is None
