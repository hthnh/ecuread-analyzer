from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.context_annotation.macro import MacroEpisode, detail_hash, generate_macros
from app.context_annotation.models import Suggestion
from app.context_annotation.storage import file_sha256
from app.context_annotation.validation import AnnotationValidationError
from app.context_annotation.web import create_app
from tests.test_context_auto_labeler import document, loaded_session, review_service


def proposal_fixture():
    loaded = loaded_session()
    proposals = document(loaded, [30] * len(loaded.samples))
    states = ["moving_accelerating", "moving_variable", "moving_decelerating", "stationary_running",
              "moving_accelerating", "moving_decelerating", "moving_steady", "unknown", "engine_off"]
    bounds = [0, 10, 20, 30, 30.1, 40, 50, 60, 70, 120]
    proposals.segments = [Suggestion(id=f"s{i}", start_s=bounds[i], end_s=bounds[i+1], operating_context=state,
                                      thermal_phase="warming" if i < 2 else "stable", evidence={}, evidence_quality="strong",
                                      reason="fixture", original_proposal={}) for i, state in enumerate(states)]
    return loaded, proposals


def test_lossless_grouping_stops_unknown_steady_reversal_and_independent_thermal():
    loaded, fine = proposal_fixture()
    before = fine.model_dump()
    macro = generate_macros(fine, loaded)
    op = [e for e in macro.episodes if e.axis == "operating"]
    thermal = [e for e in macro.episodes if e.axis == "thermal"]
    assert op[0].child_ids == ["s0", "s1", "s2"]
    assert any(e.label == "stationary_running" and e.start_s == 30 and e.end_s == 30.1 for e in op)
    assert any(e.label == "unknown" and e.start_s == 60 and e.end_s == 70 for e in op)
    assert any(e.label == "engine_off" for e in op)
    assert any(e.label == "moving_steady" for e in op)
    assert len([e for e in op if e.start_s in (30.1, 40)]) == 2  # Direct trend reversal stays a boundary.
    assert [(e.start_s, e.end_s, e.label) for e in thermal] == [(0, 20, "warming"), (20, 120, "stable")]
    for axis in (op, thermal):
        assert {child for e in axis for child in e.child_ids} == {s.id for s in fine.segments}
        assert sum(e.end_s-e.start_s for e in axis) == pytest.approx(120)
    assert all(e.review_status == "pending" for e in macro.episodes)
    assert fine.model_dump() == before
    assert [e.id for e in macro.episodes] == [e.id for e in generate_macros(fine, loaded).episodes]


def test_engine_event_and_capture_gap_boundaries_are_never_bridged():
    loaded, fine = proposal_fixture()
    fine.events = [Suggestion(id="start", start_s=5, end_s=5.1, event="engine_start", evidence={}, evidence_quality="strong", reason="fixture", original_proposal={})]
    fine.timestamp_integrity["capture_gaps"] = [{"start_s": 12, "end_s": 15}]
    macros = generate_macros(fine, loaded)
    op = [e for e in macros.episodes if e.axis == "operating"]
    assert {5, 12, 15} <= {e.start_s for e in op}
    assert any("start" in e.event_ids for e in op)


def test_overlapping_details_are_rejected_and_missing_speed_remains_unknown():
    loaded, fine = proposal_fixture()
    fine.segments[1].start_s = 9
    with pytest.raises(AnnotationValidationError, match="overlapping"):
        generate_macros(fine, loaded)
    unknown = document(loaded, [None] * len(loaded.samples))
    assert {e.label for e in generate_macros(unknown, loaded).episodes if e.axis == "operating"} == {"unknown"}


def test_macro_review_persistence_never_overwrites_fine_or_accepted_annotations(tmp_path):
    service, source = review_service(tmp_path)
    fine = service.review.generate("real")
    service.review.decide("real", [fine["segments"][0]["id"]], "accept", "Fixture reviewer")
    annotation_path = service.storage.annotation_path("real")
    before_annotation = file_sha256(annotation_path)
    before_fine = service.storage.read_suggestions("real").model_dump(mode="json")
    macro = service.macro_review.generate("real")
    episode = next(e for e in macro["episodes"] if e["axis"] == "operating")
    result = service.macro_review.decide("real", episode["id"], "correct", "Test reviewer", changes={"end_s": 59.5}, review_time_s=2)
    corrected = next(e for e in result["episodes"] if e["id"] == episode["id"])
    assert corrected["end_s"] == 59.5 and corrected["review_status"] == "pending"
    service.macro_review.decide("real", episode["id"], "accept", "Test reviewer", review_time_s=3)
    service.review.generate("real")
    result = service.macro_review.generate("real")
    accepted = next(e for e in result["episodes"] if e["id"] == episode["id"])
    assert accepted["end_s"] == 59.5 and accepted["review_status"] == "accepted"
    assert len(accepted["review_history"]) == 2
    assert file_sha256(annotation_path) == before_annotation
    # Generation can update only generated_at; no fine proposal is accepted by a macro action.
    current = service.storage.read_suggestions("real").model_dump(mode="json")
    assert current["segments"] == before_fine["segments"]
    assert current["events"] == before_fine["events"]
    assert sum(s.review_status == "accepted" for s in service.storage.read_suggestions("real").segments) == 1
    with pytest.raises(AnnotationValidationError, match="finalized"):
        service.macro_review.decide("real", episode["id"], "reject", "Test reviewer")
    assert file_sha256(source) == service.load_session("real").metadata.source.sha256


@pytest.mark.parametrize("action,state", [("reject", "rejected"), ("uncertain", "uncertain"), ("accept", "accepted")])
def test_macro_api_actions_traceability_and_no_auto_accept(tmp_path, action, state):
    service, _ = review_service(tmp_path)
    service.review.generate("real")
    client = TestClient(create_app(service))
    macro = client.post("/context-annotation/api/sessions/real/macro-generate").json()
    e = macro["episodes"][0]
    evidence = client.get(f"/context-annotation/api/sessions/real/macro/{e['id']}").json()
    assert {c["id"] for c in evidence["children"]} == set(e["child_ids"])
    assert evidence["measured_evidence"]["signals"]["vehicle_speed_kmh"]["available_count"] > 0
    assert evidence["measured_evidence"]["boundary_samples"][-1]["timestamp_ms"] == 60000
    endpoint = f"/context-annotation/api/sessions/real/macro/{e['id']}/review"
    assert client.post(endpoint, json={"action":action,"reviewer":" "}).status_code == 422
    response = client.post(endpoint, json={"action":action,"reviewer":"Test"})
    assert response.status_code == 200
    result = response.json()
    assert next(x for x in result["episodes"] if x["id"] == e["id"])["review_status"] == state
    assert not service.storage.annotation_path("real").exists()
    assert all(s.review_status == "pending" for s in service.storage.read_suggestions("real").segments)
    assert result["progress"]["thermal"]["pending"] == 1


def test_stale_inputs_cannot_be_reviewed_and_reviewed_orphans_are_retained(tmp_path):
    service, source = review_service(tmp_path)
    service.review.generate("real")
    macro = service.macro_review.generate("real")
    e = macro["episodes"][0]
    service.macro_review.decide("real", e["id"], "uncertain", "Test")
    fine = service.storage.read_suggestions("real")
    fine.segments[0].operating_context = "moving_variable"
    service.storage.write_suggestions("real", fine)
    assert service.macro_review.read("real")["stale"]
    with pytest.raises(AnnotationValidationError, match="stale"):
        service.macro_review.decide("real", e["id"], "accept", "Test")
    result = service.macro_review.generate("real")
    assert result["retained_reviews"][0]["id"] == e["id"]
    assert result["retained_reviews"][0]["review_status"] == "uncertain"
    fine.segments[0].operating_context = "moving_steady"
    service.storage.write_suggestions("real", fine)
    result = service.macro_review.generate("real")
    assert not result["retained_reviews"]
    assert next(x for x in result["episodes"] if x["id"] == e["id"])["review_status"] == "uncertain"
    source.write_bytes(source.read_bytes()+b"\n")
    with pytest.raises(AnnotationValidationError, match="SHA"):
        service.macro_review.decide("real", result["episodes"][0]["id"], "accept", "Test")


def test_invalid_macro_labels_ranges_and_unconfirmed_acceptance_rejected():
    loaded, fine = proposal_fixture()
    e = generate_macros(fine, loaded).episodes[0].model_dump()
    for changes in ({"label": "warming"}, {"start_s": float("nan")}, {"end_s": -1}, {"review_status": "accepted"}):
        with pytest.raises(ValueError):
            MacroEpisode.model_validate({**e, **changes})
    first = detail_hash(fine)
    fine.segments[0].review_status = "rejected"
    assert first == detail_hash(fine)


def test_macro_corrections_are_bounded_and_cannot_overlap_accepted_axis(tmp_path):
    service, _ = review_service(tmp_path)
    service.review.generate("real")
    fine = service.storage.read_suggestions("real")
    first = fine.segments[0].model_copy(deep=True)
    first.end_s = 30
    last = first.model_copy(deep=True)
    last.id = "last"
    last.start_s, last.end_s, last.operating_context = 30, 60, "moving_variable"
    fine.segments = [first, last]
    service.storage.write_suggestions("real", fine)
    macros = service.macro_review.generate("real")
    op = [e for e in macros["episodes"] if e["axis"] == "operating"]
    service.macro_review.decide("real", op[0]["id"], "accept", "Test")
    for changes, message in [({"start_s": 29}, "overlaps"), ({"end_s": 61}, "exceeds"),
                              ({"child_ids": []}, "unsupported")]:
        with pytest.raises(AnnotationValidationError, match=message):
            service.macro_review.decide("real", op[1]["id"], "correct", "Test", changes=changes)
