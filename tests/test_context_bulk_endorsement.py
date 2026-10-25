from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.context_annotation.annotation_snapshot import annotation_snapshot
from app.context_annotation.approved_export import export_approved, sample_range, window_assignment, write_mapping
from app.context_annotation.bulk_endorsement import (
    apply_transaction, check_traceability, endorse_campaign, json_sha, plan_session, provenance, provenance_context, read_json,
)
from app.context_annotation.freeze_dataset import FreezeValidationError
from app.context_annotation.models import BulkEndorsement, ContextAnnotationDocument
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256
from app.context_annotation.web import create_app
from tests.test_capture_attestation import declared  # noqa: F401
from tests.test_raw_context_campaign import bundle  # noqa: F401
from tests.test_context_auto_labeler import review_service, loaded_session


ENDORSEMENT = BulkEndorsement(approval_id="explicit-test-authorization", approval_record_sha256="a" * 64,
                              reviewed_at="2026-10-11T12:00:00+00:00")


def inputs(service, sid):
    fine = service.review.generate(sid)
    service.macro_review.generate(sid)
    return (read_json(service.storage.suggestion_path(sid)), read_json(service.macro_review.path(sid)),
            service.annotation_for(service.load_session(sid)).model_dump(mode="json"))


def plan(service, sid, args):
    return plan_session(fine=args[0], macro=args[1], annotation=args[2],
                        duration=service.load_session(sid).metadata.duration_s, endorsement=ENDORSEMENT)


def test_bulk_is_distinct_lossless_unknown_and_idempotent(tmp_path):
    service, source = review_service(tmp_path)
    args = inputs(service, "real")
    old = deepcopy(args)
    raw_sha = file_sha256(source)
    fine, macro, annotation, conflicts = plan(service, "real", args)
    assert args == old and not conflicts
    assert {s["review_status"] for s in fine["segments"] + fine["events"]} == {"accepted"}
    assert {e["review_status"] for e in macro["episodes"]} == {"accepted"}
    assert all(s["human_confirmation"] is None for s in annotation["segments"] + annotation["events"])
    assert all(s["bulk_endorsement"]["individual_interval_review"] is False for s in annotation["segments"])
    for before, after in zip(old[0]["segments"], fine["segments"], strict=True):
        assert before["original_proposal"] == after["original_proposal"]
        assert (before["start_s"], before["end_s"]) == (after["start_s"], after["end_s"])
    assert file_sha256(source) == raw_sha
    assert plan(service, "real", (fine, macro, annotation)) == (fine, macro, annotation, [])
    assert ContextAnnotationDocument.model_validate(annotation)


@pytest.mark.parametrize("action", ["reject", "uncertain", "correct", "accept"])
def test_existing_fine_human_decisions_never_overwritten(tmp_path, action):
    service, _ = review_service(tmp_path)
    inputs(service, "real")
    selected = service.storage.read_suggestions("real").segments[0]
    if action == "correct":
        service.review.edit("real", selected.id, {"notes": "manually corrected note"}, "Human")
    else:
        service.review.decide("real", [selected.id], action, "Human")
    args = (read_json(service.storage.suggestion_path("real")), read_json(service.macro_review.path("real")),
            service.annotation_for(service.load_session("real")).model_dump(mode="json"))
    fine, macro, annotation, unresolved = plan(service, "real", args)
    assert fine["segments"][0] == args[0]["segments"][0]
    if action != "accept":
        assert unresolved and not annotation["segments"]
        assert all(e["review_status"] == "pending" for e in macro["episodes"])
    else:
        assert annotation["segments"] == args[2]["segments"]


@pytest.mark.parametrize("action", ["reject", "uncertain", "correct", "accept"])
def test_existing_macro_human_decisions_never_overwritten(tmp_path, action):
    service, _ = review_service(tmp_path)
    inputs(service, "real")
    selected = service.macro_review.document("real").episodes[0]
    service.macro_review.decide("real", selected.id, action, "Human", changes={"notes": "manual correction"} if action == "correct" else None)
    args = (read_json(service.storage.suggestion_path("real")), read_json(service.macro_review.path("real")),
            service.annotation_for(service.load_session("real")).model_dump(mode="json"))
    fine, macro, annotation, unresolved = plan(service, "real", args)
    assert macro["episodes"][0] == args[1]["episodes"][0]
    if action != "accept":
        assert unresolved and fine["segments"][0]["review_status"] == "pending"


def test_macro_traceability_fails_closed(tmp_path):
    service, _ = review_service(tmp_path)
    fine, macro, annotation = inputs(service, "real")
    macro["episodes"][0]["child_ids"] = ["missing"]
    with pytest.raises(ValueError, match="missing"):
        plan(service, "real", (fine, macro, annotation))


def test_transaction_retry_and_concurrent_review_preservation(tmp_path):
    path, stage = tmp_path / "live.json", tmp_path / "stage.json"
    atomic_write_json(path, {"old": True})
    atomic_write_json(stage, {"new": True})
    transaction = {"targets": [{"path": str(path), "staged_path": stage.name, "before_sha256": file_sha256(path),
                                "after_sha256": file_sha256(stage)}], "unresolved": [], "approval": {"approval_id": "test"}}
    assert apply_transaction(transaction, tmp_path)["files_written_this_run"] == 1
    assert apply_transaction(transaction, tmp_path)["files_written_this_run"] == 0
    atomic_write_json(path, {"later_human": True})
    with pytest.raises(ValueError, match="preserved"):
        apply_transaction(transaction, tmp_path)
    assert read_json(path) == {"later_human": True}
    atomic_write_json(stage, {"changed": True})
    with pytest.raises(ValueError, match="staged"):
        apply_transaction(transaction, tmp_path)


def test_campaign_endorsement_persistence_strict_freeze_and_export_reproducibility(declared, tmp_path):
    path = declared[0][2] / "corpus_manifest.json"
    service = ContextAnnotationService(corpus_manifest=path)
    sid = declared[1].sessions[0].session_id
    inputs(service, sid)
    result = endorse_campaign(path)
    assert not result["unresolved"]
    assert endorse_campaign(path)["files_written_this_run"] == 0
    approval = read_json(declared[0][2] / "provenance/operator_context_approval.v1.json")
    assert approval["individual_interval_review"] is False
    assert result["approval_sha256"] == json_sha(approval)
    fresh = ContextAnnotationService(corpus_manifest=path)
    with TestClient(create_app(fresh)) as client:
        detail = client.get(f"/context-annotation/api/sessions/{sid}")
        assert detail.status_code == 200
        assert detail.json()["annotation"]["segments"][0]["bulk_endorsement"]
        assert fresh.review.read(sid)["pending_count"] == 0
    with pytest.raises(FreezeValidationError, match="missing named human"):
        annotation_snapshot(corpus_manifest=path, output=tmp_path / "not-frozen")
    assert not (tmp_path / "not-frozen").exists()
    first = export_approved(corpus_manifest=path, output=tmp_path / "export1")
    second = export_approved(corpus_manifest=path, output=tmp_path / "export2")
    assert first["content_sha256"] == second["content_sha256"]
    assert first["decision"] == "approval_recorded_snapshot_blocked" and not first["frozen"]
    with pytest.raises(FileExistsError):
        export_approved(corpus_manifest=path, output=tmp_path / "export1")
    ann_sha = file_sha256(fresh.storage.annotation_path(sid))
    fine = fresh.storage.read_suggestions(sid)
    macro = fresh.macro_review.document(sid)
    fresh.review.generate(sid)
    fresh.macro_review.generate(sid)
    assert file_sha256(fresh.storage.annotation_path(sid)) == ann_sha
    assert fresh.storage.read_suggestions(sid).segments == fine.segments
    assert fresh.macro_review.document(sid).episodes == macro.episodes


@pytest.mark.parametrize("target", ["source", "approved_proposal", "scope", "later_annotation"])
def test_endorsement_retry_detects_changed_inputs(declared, target):
    path = declared[0][2] / "corpus_manifest.json"
    service = ContextAnnotationService(corpus_manifest=path)
    sid = declared[1].sessions[0].session_id
    inputs(service, sid)
    result = endorse_campaign(path)
    if target == "source":
        changed = Path(declared[1].sessions[0].source_path)
        changed.chmod(0o644)
        changed.write_bytes(changed.read_bytes() + b"\n")
    elif target == "approved_proposal":
        changed = Path(result["recovery_directory"]) / "before" / f"{sid}.suggestions.json"
        changed.write_bytes(changed.read_bytes() + b"\n")
    elif target == "scope":
        approval_path = Path(result["approval_path"])
        approval = read_json(approval_path)
        approval["input_source_hashes"][sid] = "0" * 64
        atomic_write_json(approval_path, approval)
    else:
        changed = service.storage.annotation_path(sid)
        payload = read_json(changed)
        payload["session_labels"]["notes"] = "later human change"
        atomic_write_json(changed, payload)
    with pytest.raises(ValueError):
        endorse_campaign(path)


def test_interrupted_batch_can_resume_without_duplicate_acceptance(declared, monkeypatch):
    import app.context_annotation.bulk_endorsement as module
    path = declared[0][2] / "corpus_manifest.json"
    service = ContextAnnotationService(corpus_manifest=path)
    sid = declared[1].sessions[0].session_id
    inputs(service, sid)
    original = module.apply_transaction
    def interrupted(transaction, work):
        original({**transaction, "targets": transaction["targets"][:1]}, work)
        raise RuntimeError("simulated interruption")
    monkeypatch.setattr(module, "apply_transaction", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        endorse_campaign(path)
    monkeypatch.setattr(module, "apply_transaction", original)
    assert endorse_campaign(path)["files_written_this_run"] == 2
    assert endorse_campaign(path)["files_written_this_run"] == 0
    accepted = service.storage.read_annotation(sid)
    assert len(accepted.segments) == len(service.storage.read_suggestions(sid).segments)


def test_half_open_sample_mapping_and_final_endpoint():
    times = [0., 1., 2., 3.]
    assert sample_range(times, 0., 1., 3.) == (0, 1)
    assert sample_range(times, 1., 3., 3.) == (1, 4)
    assert sample_range(times, .1, .2, 3.) == (1, 1)


def test_named_batch_confirmation_does_not_claim_individual_inspection():
    history = [{"action": "accept", "reviewer": "Human", "action_id": "shared"}]
    items = [{"id": str(i), "review_history": history, "human_confirmation": {"corrections": {}}} for i in range(2)]
    context = provenance_context({"segments": items, "events": []}, {"episodes": []})
    assert all(provenance(i, **context) == "multi_item_human_confirmation" for i in items)
    annotation = {"suggestion_id": "0", "human_confirmation": {"corrections": {}}}
    assert provenance(annotation, **context) == "multi_item_human_confirmation"
    annotation["human_confirmation"]["corrections"] = {"start_s": 1}
    assert provenance(annotation, **context) == "manually_corrected"
    assert provenance(items[0]) == "named_human_confirmation_method_unresolved"


@pytest.mark.parametrize("labels,status", [(["moving_steady"], "homogeneous"), (["unknown"], "abstained"),
                                            (["moving_steady", "stationary_running"], "ambiguous"),
                                            (["moving_steady", "moving_steady"], "ambiguous")])
def test_crossing_windows_never_majority_assign(labels, status):
    rows = [{"id": str(i), "label": label, "start_s": i * 5., "end_s": (i + 1) * 5.,
             "review_provenance": "bulk_operator_endorsement"} for i, label in enumerate(labels)]
    result = window_assignment(rows, 0., len(labels) * 5. - .1, 10.)
    assert result["status"] == status
    assert (result["assigned_label"] is None) == (status == "ambiguous")
    assert window_assignment(rows, 0., 11., 12.)["status"] == "unresolved"


def test_short_unsampled_interval_not_hidden_in_window_mapping():
    rows = [{"id": str(i), "label": value, "start_s": start, "end_s": end, "review_provenance": "bulk_operator_endorsement"}
            for i, (value, start, end) in enumerate([("moving_steady", 0., 1.1), ("stationary_running", 1.1, 1.2), ("moving_steady", 1.2, 5.)])]
    result = window_assignment(rows, 0., 4., 5.)
    assert result["status"] == "ambiguous" and result["interval_ids"] == ["0", "1", "2"]


def test_50_sample_mapping_window_count_and_no_capture_concatenation(tmp_path):
    loaded = loaded_session()
    stats = write_mapping(tmp_path, loaded, {"micro_operating": []}, [])
    assert stats["windows"] == (len(loaded.samples) - 50) // 10 + 1
    windows = [json.loads(line) for line in (tmp_path / "windows.jsonl").read_text().splitlines()]
    assert windows[0]["row_start"] == 0 and windows[-1]["row_end"] < len(loaded.samples)
    assert all(w["sample_count"] == 50 and w["tracks"]["micro_operating"]["status"] == "unresolved" for w in windows)
