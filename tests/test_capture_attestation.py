from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.context_annotation import attestation
from app.context_annotation.annotation_snapshot import annotation_snapshot
from app.context_annotation.corpus import read_curated_manifest, repo_root
from app.context_annotation.freeze_dataset import FreezeValidationError
from app.context_annotation.models import AttestedRawCorpusManifest, ContextAnnotationDocument
from app.context_annotation.raw_campaign import import_archive, validate_campaign_split
from app.context_annotation.review_effort import review_statistics
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256
from app.context_annotation.web import create_app
from tests.test_raw_context_campaign import bundle  # noqa: F401


@pytest.fixture
def declared(bundle, monkeypatch):
    archive, staged, campaign, ids = bundle
    manifest, _ = import_archive(archive, staged, campaign, expected_ids=ids)
    path = campaign / "corpus_manifest.json"
    atomic_write_json(campaign / "exports/old_to_raw_lineage.json",
                      {"new_manifest_sha256": file_sha256(path),
                       "old_manifest_sha256": file_sha256(repo_root() / "data/context_annotation/curated_corpus.json"), "sessions": []})
    monkeypatch.setattr(attestation, "EXPECTED_IDS", ids)
    original = path.read_bytes()
    value = attestation.record_attestation(campaign, "2026-10-11")
    return bundle, value, original


def test_attestation_exact_inventory_and_strict_policy_unchanged(declared):
    bundle, manifest, original = declared
    path = bundle[2] / "corpus_manifest.json"
    loaded, record, groups = attestation.verify_attestation(path)
    assert loaded == manifest
    assert isinstance(read_curated_manifest(path), AttestedRawCorpusManifest)
    assert manifest.operator_attested_independent_captures == 1
    assert manifest.machine_verified_independent_captures is None
    assert manifest.independent_captured_sessions is None
    assert record["attestation_source"] == "data_collector"
    assert record["attestation_type"] == "operator_declared_independence"
    assert record["sources"][0]["source_sha256"] == manifest.sessions[0].source_sha256
    assert record["sources"][0]["confirmed_session_metadata"] == {}
    assert (bundle[2] / "provenance/corpus_manifest.pre_ctx23.json").read_bytes() == original
    with pytest.raises(ValueError, match="quarantined physical capture"):
        validate_campaign_split(path, {"train": ["session_000099"]})
    result = validate_campaign_split(path, {"train": ["session_000099"]}, policy=attestation.ATTESTED_POLICY)
    assert result["valid"] and "operator attestation" in result["basis"]
    with pytest.raises(ValueError, match="unknown evaluation policy"):
        validate_campaign_split(path, {"train": ["session_000099"]}, policy="automatic")


def test_recording_and_reimport_are_idempotent_and_do_not_drop_declaration(declared):
    bundle, manifest, _ = declared
    path = bundle[2] / "corpus_manifest.json"
    original = path.read_bytes()
    assert attestation.record_attestation(bundle[2], "2026-10-11") == manifest
    assert path.read_bytes() == original
    assert import_archive(*bundle[:3], expected_ids=bundle[3])[0] == manifest
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="date differs"):
        attestation.record_attestation(bundle[2], "2026-10-12")


@pytest.mark.parametrize("target", ["raw", "attestation", "groups", "lineage"])
def test_changed_source_or_evidence_fails_closed(declared, target):
    bundle, manifest, _ = declared
    paths = {"raw": Path(manifest.sessions[0].source_path),
             "attestation": Path(manifest.operator_attestation["path"]),
             "groups": Path(manifest.operator_attestation["capture_groups_path"]),
             "lineage": bundle[2] / "exports/old_to_raw_lineage.json"}
    path = paths[target]
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA|hash"):
        attestation.verify_attestation(bundle[2] / "corpus_manifest.json")


@pytest.mark.parametrize("mutation", ["strict", "verified", "count", "group"])
def test_registry_cannot_claim_machine_verification(declared, mutation):
    _, manifest, _ = declared
    value = manifest.model_dump(mode="json")
    if mutation == "strict":
        value["sessions"][0]["origin"]["evaluation_eligible"] = True
    elif mutation == "verified":
        value["machine_verified_independent_captures"] = 1
    elif mutation == "count":
        value["operator_attested_independent_captures"] = 2
    else:
        value["sessions"][0]["origin"]["physical_capture_group_id"] = None
    with pytest.raises(ValidationError):
        AttestedRawCorpusManifest.model_validate(value)


def test_derived_records_must_inherit_exact_parent_group(declared):
    _, manifest, _ = declared
    entry = manifest.sessions[0]
    parent = {"session_id": entry.session_id, "source_sha256": entry.source_sha256, **entry.origin}
    child = attestation.inherited_group(parent, parent_source_sha256=entry.source_sha256)
    assert child["physical_capture_group_id"] == parent["physical_capture_group_id"]
    assert not child["evaluation_eligible"]
    with pytest.raises(ValueError, match="SHA mismatch"):
        attestation.inherited_group(parent, parent_source_sha256="0" * 64)
    with pytest.raises(ValueError, match="synthetic"):
        attestation.inherited_group(parent, parent_source_sha256=entry.source_sha256, synthetic=True)
    parent["operator_evaluation_eligible"] = False
    with pytest.raises(ValueError, match="unresolved"):
        attestation.inherited_group(parent, parent_source_sha256=entry.source_sha256)


def test_attested_split_rejects_correlated_alias_across_partitions(declared, monkeypatch):
    bundle, manifest, _ = declared
    entry = manifest.sessions[0]
    path = bundle[2] / "corpus_manifest.json"
    alias = {"session_id": "old-alias", "source_sha256": entry.source_sha256, "source_path": entry.source_path,
             "physical_capture_group_id": entry.origin["physical_capture_group_id"], "operator_evaluation_eligible": True}
    monkeypatch.setattr(attestation, "verify_attestation", lambda _: (manifest, {}, {"historical_entries": [alias]}))
    monkeypatch.setattr("app.context_annotation.storage.source_tree_sha256", lambda _: entry.source_sha256)
    with pytest.raises(ValueError, match="crosses partitions"):
        attestation.validate_attested_split(path, {"train": [entry.session_id], "test": ["old-alias"]})
    assert attestation.validate_attested_split(path, {"train": [entry.session_id, "old-alias"]})["capture_groups"] == 1
    alias["operator_evaluation_eligible"] = False
    with pytest.raises(ValueError, match="synthetic or incompatible"):
        attestation.validate_attested_split(path, {"train": ["old-alias"]})


def test_review_controls_preserve_acceptance_measure_effort_and_do_not_count_noop(declared):
    bundle, manifest, _ = declared
    path = bundle[2] / "corpus_manifest.json"
    service = ContextAnnotationService(corpus_manifest=path)
    sid = manifest.sessions[0].session_id
    suggestions = service.review.generate(sid)
    selected = suggestions["segments"][0]
    with TestClient(create_app(service)) as client:
        base = f"/context-annotation/api/sessions/{sid}"
        assert client.get(base).json()["capture_provenance"]["machine_verified"] is False
        payload = {"changes": {"end_s": selected["end_s"]}, "reviewer": "isolated test"}
        assert client.patch(base + "/suggestions/" + selected["id"], json=payload).status_code == 200
        assert service.storage.read_suggestions(sid).segments[0].review_history == []
        payload["changes"]["end_s"] -= .25
        payload["review_time_s"] = 7.5
        assert client.patch(base + "/suggestions/" + selected["id"], json=payload).status_code == 200
        decision = {"suggestion_ids": [selected["id"]], "action": "uncertain", "reviewer": "isolated test", "review_time_s": 2}
        assert client.post(base + "/suggestion-review", json=decision).status_code == 200
        assert not service.storage.annotation_path(sid).exists()
        decision["action"] = "accept"
        assert client.post(base + "/suggestion-review", json=decision).status_code == 200
        accepted_sha = file_sha256(service.storage.annotation_path(sid))
        service.review.generate(sid)
        fresh = ContextAnnotationService(corpus_manifest=path)
        assert file_sha256(fresh.storage.annotation_path(sid)) == accepted_sha
        assert fresh.review.read(sid)["segments"][0]["review_status"] == "accepted"
        assert client.patch(base + "/suggestions/" + selected["id"], json=payload).status_code == 422
        decision["review_time_s"] = -1
        assert client.post(base + "/suggestion-review", json=decision).status_code == 422
        items = fresh.review.read(sid)["segments"]
        stats = review_statistics(items, [])
        assert stats["corrected"] == stats["accepted"] == 1
        assert stats["boundary_corrections"][0]["absolute_boundary_change_s"] == .25
        assert stats["manual_review_time_s"] == 11.5


def test_snapshot_refuses_missing_pending_uncertain_or_unconfirmed_review(declared, tmp_path):
    bundle, manifest, _ = declared
    path = bundle[2] / "corpus_manifest.json"
    output = tmp_path / "not-created"
    service = ContextAnnotationService(corpus_manifest=path)
    sid = manifest.sessions[0].session_id
    with pytest.raises(FreezeValidationError, match="incomplete"):
        annotation_snapshot(corpus_manifest=path, output=output)
    service.review.generate(sid)
    item = service.review.read(sid)["segments"][0]
    service.review.decide(sid, [item["id"]], "uncertain", "isolated test")
    with pytest.raises(FreezeValidationError, match="incomplete"):
        annotation_snapshot(corpus_manifest=path, output=output)
    assert not output.exists()


def test_complete_snapshot_reproducible_annotation_only_and_no_overwrite(declared, tmp_path):
    bundle, manifest, _ = declared
    path = bundle[2] / "corpus_manifest.json"
    service = ContextAnnotationService(corpus_manifest=path)
    sid = manifest.sessions[0].session_id
    proposals = service.review.generate(sid)
    ids = [p["id"] for p in proposals["segments"] + proposals["events"]]
    service.review.decide(sid, ids, "accept", "isolated test", review_time_s=6)
    kwargs = {"corpus_manifest": path, "created_at": "2026-10-11T00:00:00+00:00"}
    first = annotation_snapshot(output=tmp_path / "first", **kwargs)
    second = annotation_snapshot(output=tmp_path / "second", **kwargs)
    assert first == second
    assert (tmp_path / "first/snapshot.json").read_bytes() == (tmp_path / "second/snapshot.json").read_bytes()
    assert len(list((tmp_path / "first").iterdir())) == 1
    assert first["machine_verified_independent_captures"] is None
    assert first["coverage"]["truly_unannotated_duration_s"] == 0
    with pytest.raises(FileExistsError):
        annotation_snapshot(output=tmp_path / "first", **kwargs)
    document = service.storage.read_annotation(sid)
    changed = document.model_dump(mode="json")
    changed["segments"][0].update(origin="human", human_confirmation=None)
    service.storage.write_annotation_for_source(sid, ContextAnnotationDocument.model_validate(changed))
    with pytest.raises(FreezeValidationError, match="human review provenance"):
        annotation_snapshot(output=tmp_path / "third", **kwargs)


def test_missing_manual_time_is_unknown_and_batch_time_not_multiplied():
    assert review_statistics([], [])["manual_review_time_s"] is None
    item = {"id": "one", "start_s": 0, "end_s": 1, "original_proposal": {"start_s": 0, "end_s": 1},
            "review_status": "accepted", "review_history": [{"action": "accept", "action_id": "batch", "review_time_s": 5}]}
    second = {**item, "id": "two"}
    assert review_statistics([item, second], [])["manual_review_time_s"] == 5


def test_named_manual_labels_and_events_preserve_confirmation(declared):
    bundle, manifest, _ = declared
    service = ContextAnnotationService(corpus_manifest=bundle[2] / "corpus_manifest.json")
    sid = manifest.sessions[0].session_id
    with TestClient(create_app(service)) as client:
        base = f"/context-annotation/api/sessions/{sid}"
        segment = {"start_s": 0, "end_s": 1, "taxonomy_version": "context-taxonomy-v2",
                   "labels": {"operating_context": "unknown", "thermal_phase": "unknown"}, "reviewer": "named human test"}
        response = client.post(base + "/segments", json=segment)
        assert response.status_code == 200
        accepted = response.json()["segments"][0]
        assert accepted["human_confirmation"]["reviewer"] == "named human test"
        response = client.post(base + "/events", json={"event": "throttle_burst", "start_s": 0, "end_s": .25, "reviewer": "named human test"})
        assert response.status_code == 200
        assert response.json()["events"][0]["human_confirmation"]["reviewer"] == "named human test"
        segment["end_s"] = .75
        response = client.put(base + "/segments/" + accepted["id"], json=segment)
        assert response.status_code == 200
        assert len(response.json()["segments"][0]["confirmation_history"]) == 1


def test_effort_uses_authoritative_accepted_boundary_edits():
    item = {"id": "proposal", "start_s": 0, "end_s": 2, "original_proposal": {"start_s": 0, "end_s": 2},
            "review_status": "accepted", "review_history": [], "human_confirmation": {"corrections": {}}}
    accepted = {"suggestion_id": "proposal", "start_s": .5, "end_s": 2,
                "human_confirmation": {"reviewer": "human", "corrections": {"start_s": .5}}}
    stats = review_statistics([item], [accepted])
    assert stats["corrected"] == 1
    assert stats["boundary_corrections"][0]["absolute_boundary_change_s"] == .5


def test_snapshot_refuses_review_writes_during_export(declared, tmp_path, monkeypatch):
    bundle, manifest, _ = declared
    path = bundle[2] / "corpus_manifest.json"
    service = ContextAnnotationService(corpus_manifest=path)
    sid = manifest.sessions[0].session_id
    proposals = service.review.generate(sid)
    service.review.decide(sid, [p["id"] for p in proposals["segments"] + proposals["events"]], "accept", "isolated test")
    from app.context_annotation import annotation_snapshot as snapshot_module
    original_hash = snapshot_module._stable_hash

    def concurrent_change(value):
        target = service.storage.suggestion_path(sid)
        target.write_bytes(target.read_bytes() + b"\n")
        return original_hash(value)

    monkeypatch.setattr(snapshot_module, "_stable_hash", concurrent_change)
    with pytest.raises(FreezeValidationError, match="inputs changed"):
        annotation_snapshot(corpus_manifest=path, output=tmp_path / "refused")
    assert not (tmp_path / "refused").exists()
