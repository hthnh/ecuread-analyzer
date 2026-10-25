from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.context_annotation.corpus import manifest_sources, read_curated_manifest
from app.context_annotation.freeze_dataset import FreezeValidationError, freeze_dataset
from app.context_annotation.models import RawCorpusManifest
from app.context_annotation.raw_campaign import import_archive, inspect_raw, validate_campaign_split
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json
from tests.test_native_upload_compatibility import content
from tests.test_raw_decode_analyze_api import asymmetric_native_frame


@pytest.fixture
def bundle(tmp_path):
    payload = content([asymmetric_native_frame()] * 30)
    sid = "session_000099"
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / f"{sid}.jsonl").write_bytes(payload)
    supplied = {"source_count": 1, "capture_independence": {"user_attestation": "researcher selected"},
                "source_entries": [{"source_session_id": sid, "relative_path": f"raw/{sid}.jsonl",
                                    "sha256": hashlib.sha256(payload).hexdigest(), "file_size_bytes": len(payload),
                                    "frame_count": 30, "duration_s": 7.25}]}
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("manifest.json", json.dumps(supplied))
        output.writestr(f"raw/{sid}.jsonl", payload)
    return archive, staged, tmp_path / "campaign", {sid}


def imported(bundle):
    archive, staged, campaign, ids = bundle
    return import_archive(archive, staged, campaign, expected_ids=ids)


def test_import_is_hash_pinned_idempotent_and_independence_unknown(bundle):
    first, audit = imported(bundle)
    manifest = bundle[2] / "corpus_manifest.json"
    before = manifest.read_bytes()
    second, repeated = imported(bundle)
    assert manifest.read_bytes() == before
    assert second == first
    assert first.independent_captured_sessions is None
    assert first.source_count == audit["unique_source_hashes"] == 1
    assert audit["total_frames"] == 30
    assert repeated["sessions"][0]["import_status"] == "reused_identical"
    source = Path(first.sessions[0].source_path)
    assert source.stat().st_mode & 0o222 == 0
    assert first.sessions[0].origin["physical_capture_group_id"] is None
    assert not first.sessions[0].origin["evaluation_eligible"]
    assert isinstance(read_curated_manifest(manifest), RawCorpusManifest)


def test_staging_mismatch_fails_without_partial_import(bundle):
    (bundle[1] / "session_000099.jsonl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="staged source"):
        imported(bundle)
    assert not bundle[2].exists()


def test_existing_import_is_never_overwritten_and_mismatch_is_visible(bundle, settings):
    first, _ = imported(bundle)
    path = Path(first.sessions[0].source_path)
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="never overwritten"):
        imported(bundle)
    sources = manifest_sources(bundle[2] / "corpus_manifest.json")
    assert sources[0].sha256 != sources[0].expected_sha256
    service = ContextAnnotationService(settings=settings, corpus_manifest=bundle[2] / "corpus_manifest.json")
    assert service.load_session("session_000099").metadata.status == "source_hash_mismatch"


def test_unlisted_archive_member_is_rejected(bundle):
    with zipfile.ZipFile(bundle[0], "a") as output:
        output.writestr("raw/session_000098.jsonl", b"extra")
    with pytest.raises(ValueError, match="unlisted RAW"):
        imported(bundle)
    assert not bundle[2].exists()


def test_duplicate_archive_member_is_rejected(bundle):
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(bundle[0], "a") as output:
            output.writestr("raw/session_000099.jsonl", b"different")
    with pytest.raises(ValueError, match="duplicate or unlisted RAW"):
        imported(bundle)


@pytest.mark.parametrize("mutation", ["count", "duplicate_id", "duplicate_hash", "independence"])
def test_registry_rejects_false_counts_or_duplicate_identity(bundle, mutation):
    manifest, _ = imported(bundle)
    value = manifest.model_dump(mode="json")
    if mutation == "count":
        value["source_count"] = 18
    elif mutation == "independence":
        value["independent_captured_sessions"] = 18
    else:
        entry = dict(value["sessions"][0])
        if mutation == "duplicate_hash":
            entry["session_id"] = "session_000098"
        else:
            entry["source_sha256"] = "0" * 64
        value["sessions"].append(entry)
        value["source_count"] = 2
    with pytest.raises(ValidationError):
        RawCorpusManifest.model_validate(value)


def test_campaign_sidecars_are_isolated_and_freeze_is_blocked(bundle, settings):
    imported(bundle)
    manifest = bundle[2] / "corpus_manifest.json"
    service = ContextAnnotationService(settings=settings, corpus_manifest=manifest)
    assert service.storage.annotations_dir == bundle[2] / "annotations"
    assert service.storage.suggestions_dir == bundle[2] / "suggestions"
    assert service.storage.exports_dir == bundle[2] / "exports"
    output = bundle[2] / "freeze"
    with pytest.raises(FreezeValidationError, match="unfrozen research registry"):
        freeze_dataset(corpus_manifest=manifest, output=output, data_dir=settings.data_dir)
    assert not output.exists()
    assert not list(service.storage.annotations_dir.iterdir())


def test_splits_fail_closed_for_unknown_physical_capture_identity(bundle):
    imported(bundle)
    with pytest.raises(ValueError, match="quarantined physical capture"):
        validate_campaign_split(bundle[2] / "corpus_manifest.json", {"train": ["session_000099"]})


def test_resolved_physical_group_cannot_cross_partitions(bundle):
    manifest, _ = imported(bundle)
    value = manifest.model_dump(mode="json")
    value["sessions"][0]["origin"].update(evaluation_eligible=True, physical_capture_group_id="verified-parent")
    alias = {**value["sessions"][0], "session_id": "session_000098", "source_sha256": "0" * 64}
    value["sessions"].append(alias)
    value["source_count"] = 2
    path = bundle[2] / "corpus_manifest.json"
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="crosses partitions"):
        validate_campaign_split(path, {"train": ["session_000099"], "test": ["session_000098"]})
    assert validate_campaign_split(path, {"train": ["session_000099", "session_000098"]})["capture_groups"] == 1


@pytest.mark.parametrize("kind", ["checksum", "backwards", "gap"])
def test_invalid_frames_and_time_discontinuities_fail(kind):
    rows = [json.loads(line) for line in content([asymmetric_native_frame()] * 3).splitlines()]
    if kind == "checksum":
        rows[0]["raw_hex"] = rows[0]["raw_hex"][:-2] + "00"
    else:
        rows[1]["elapsed_ms"] = -1 if kind == "backwards" else 2000
    with pytest.raises(ValueError):
        inspect_raw("\n".join(json.dumps(row) for row in rows).encode())


def test_chart_decimation_retains_full_timeline_endpoints(settings):
    service = ContextAnnotationService(settings=settings)
    points = [{"t": i / 4, "y": i % 3} for i in range(17976)]
    sampled = service._downsample_points(points, max_points=1200)
    assert len(sampled) <= 1200
    assert sampled[0] == points[0]
    assert sampled[-1] == points[-1]
    assert all(a["t"] <= b["t"] for a, b in zip(sampled, sampled[1:]))
