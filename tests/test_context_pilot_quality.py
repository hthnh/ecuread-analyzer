import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.context_annotation import pilot_quality
from app.context_annotation.pilot_quality import coalesce, distribution, oscillations, validate_split, validate_split_inputs
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import file_sha256
from app.context_annotation.web import create_app
from tests.test_context_auto_labeler import review_service


def test_axes_are_coalesced_separately_and_gaps_are_preserved():
    segments = [dict(id=str(i), start_s=a, end_s=b, operating_context=op, thermal_phase=th)
                for i, (a, b, op, th) in enumerate([
                    (0, 5, "moving_steady", "warming"), (5, 10, "moving_steady", "stable"),
                    (10, 15, "stationary_running", "stable"), (20, 25, "stationary_running", "stable")])]
    op = coalesce(segments, "operating_context")
    th = coalesce(segments, "thermal_phase")
    assert [(r["start_s"], r["end_s"]) for r in op] == [(0, 10), (10, 15), (20, 25)]
    assert [(r["start_s"], r["end_s"]) for r in th] == [(0, 5), (5, 15), (20, 25)]
    assert distribution([]) == {"count": 0, "total_s": 0.0}


def test_oscillation_screen_distinguishes_small_from_meaningful_changes():
    runs = [dict(start_s=a, end_s=b, label=label) for a, b, label in
            [(0, 8, "moving_variable"), (8, 12, "moving_steady"), (12, 20, "moving_variable")]]
    times = np.arange(21)
    small = oscillations(runs, times, 30 + times % 2, 8, 40, 3)
    large = oscillations(runs, times, times * 2, 8, 40, 3)
    assert small[0]["low_amplitude"]
    assert not large[0]["low_amplitude"]
    runs[1]["start_s"] = 9
    assert not oscillations(runs, times, times, 8, 40, 3)


@pytest.fixture
def lineage():
    return {"sessions": [dict(session_id=sid, correlation_group_id=group, evaluation_eligible=eligible)
                         for sid, group, eligible in [("a", "capture:1", True), ("alias", "capture:1", True),
                                                      ("split", "capture:1", True), ("b", "capture:2", True),
                                                      ("synthetic", "unknown", False), ("unresolved", "unknown2", False)]]}


@pytest.mark.parametrize("split,reason", [({"train": ["a"], "test": ["alias"]}, "crosses partitions"),
    ({"train": ["a"], "test": ["split"]}, "crosses partitions"),
    ({"test": ["synthetic"]}, "quarantined"), ({"test": ["unresolved"]}, "quarantined"),
    ({"train": ["a"], "test": ["a"]}, "repeated"), ({"test": ["missing"]}, "unknown"),
    ({"test": "a"}, "session ID list")])
def test_split_guard_fails_closed(lineage, split, reason):
    with pytest.raises(ValueError, match=reason):
        validate_split(lineage, split)


def test_split_guard_keeps_capture_group_together(lineage):
    assert validate_split(lineage, {"train": ["a", "alias", "split"], "test": ["b"]}) == {
        "valid": True, "entries": 4, "capture_groups": 2}


@pytest.mark.parametrize("changed", ["manifest", "source", "map", "labels", "raw", "entry_set", None])
def test_split_input_hash_guards(monkeypatch, changed):
    root = pilot_quality.repo_root()
    manifest = Path("corpus.json")
    evidence_map = root / "data/context_annotation/raw_signal_sources.json"
    labels = root / "data/calibration/session_labels.csv"
    raw = root / "real_data/test.jsonl"
    hashes = {manifest: "manifest", evidence_map: "map", labels: "labels", raw: "raw"}
    report = {"corpus_manifest_sha256": "manifest", "evidence": {
        "raw_source_map_sha256": "map", "synthetic_seed_labels_sha256": "labels"}, "sessions": [{
            "session_id": "a", "source_sha256": "source", "evaluation_eligible": True,
            "lineage": {"parent_source_path": "real_data/test.jsonl", "parent_source_sha256": "raw"}}]}
    source = SimpleNamespace(source_id="a", sha256="source", expected_sha256="source")
    if changed in {"manifest", "map", "labels", "raw"}:
        p = {"manifest": manifest, "map": evidence_map, "labels": labels, "raw": raw}[changed]
        hashes[p] = "changed"
    elif changed == "source":
        source.sha256 = "changed"
    elif changed == "entry_set":
        report["sessions"] = []
    monkeypatch.setattr(pilot_quality, "file_sha256", lambda p: hashes[p])
    monkeypatch.setattr(pilot_quality, "manifest_sources", lambda p: [source])
    if changed:
        with pytest.raises(ValueError):
            validate_split_inputs(report, manifest)
    else:
        validate_split_inputs(report, manifest)


def test_correction_uncertainty_reload_and_rerun_add_no_annotations(tmp_path):
    service, source = review_service(tmp_path)
    original_sha = file_sha256(source)
    # Existing human context provides an overwrite guard without accepting any proposal.
    service.add_segment("real", {"start_s": 0, "end_s": 5, "labels": {
        "engine": "running", "throttle": "closed", "dynamics": "steady", "thermal": "stable"}})
    annotation = service.storage.annotation_path("real")
    before = file_sha256(annotation)
    client = TestClient(create_app(service))
    proposals = service.review.generate("real")
    item = proposals["segments"][0]
    response = client.patch(f"/context-annotation/api/sessions/real/suggestions/{item['id']}", json={
        "reviewer": "Isolated test", "changes": {"end_s": item["end_s"] - .25, "operating_context": "unknown"}})
    assert response.status_code == 200
    response = client.post("/context-annotation/api/sessions/real/suggestion-review", json={
        "suggestion_ids": [item["id"]], "action": "uncertain", "reviewer": "Isolated test"})
    assert response.status_code == 200
    reloaded = ContextAnnotationService(storage=service.storage).review.read("real")
    deferred = reloaded["segments"][0]
    assert deferred["review_status"] == "uncertain"
    assert deferred["end_s"] == item["end_s"] - .25
    assert deferred["operating_context"] == "unknown"
    assert deferred["human_confirmation"] is None
    assert deferred["original_proposal"] == item["original_proposal"]
    assert reloaded["uncertain_count"] == 1
    assert service.generate_campaign_status()["annotated_duration_s"] == 5
    assert service.generate_campaign_status()["explicitly_unknown_duration_s"] == 0
    service.review.generate("real")
    assert service.review.read("real")["segments"][0]["review_status"] == "uncertain"
    assert file_sha256(annotation) == before
    assert file_sha256(source) == original_sha
    # Batch range rejection also cannot silently resolve an uncertain item.
    result = service.review.decide("real", [], "reject", "Isolated test", start_s=0, end_s=60)
    assert not result["decided"]
    assert result["skipped"]
    service.review.decide("real", [item["id"]], "reject", "Isolated test")
    assert file_sha256(annotation) == before


def test_real_audit_accounts_for_all_proposals_and_pinned_entries():
    root = Path(__file__).resolve().parents[1]
    quality = json.loads((root / "data/context_annotation/exports/ctx2_proposal_quality.json").read_text())
    provenance = json.loads((root / "data/context_annotation/exports/ctx2_provenance_reconciliation.json").read_text())
    assert quality["total_proposals"] == 5035
    assert quality["total_proposals"] == quality["joint_segment_count"] + quality["event_marker_count"]
    assert len(quality["sessions"]) == len(provenance["sessions"]) == 57
    assert quality["motion_evidence"]["unsupported"] == []
    assert quality["motion_evidence"]["stationary_constraint_failures"] == []
    assert quality["event_evidence"]["checked"] == 2586
    assert quality["event_evidence"]["rule_inconsistencies"] == []
    assert quality["input_hashes_unchanged"]
    assert len(quality["source_hash_audit"]["duplicate_samples_file_sha256"]) == 5
    assert provenance["corrected_independent_capture_count"] is None
    assert sum(provenance["classification_counts"].values()) == 57
    for record in quality["sessions"]:
        if record["speed_provenance"]["status"] == "unavailable":
            assert not record["motion_evidence_checks"]
