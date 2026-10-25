from xml.etree import ElementTree as ET

import numpy as np
import pytest
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from app.evaluation import context_validation_plan as plan


def fixture_capture(number):
    sid = f"session_{number:06}"
    source = {"session_id": sid, "source_path": "test.jsonl", "source_sha256": f"{number:064x}",
              "physical_capture_group_id": f"operator:{number}", "duration_s": 200, "canonical_sample_count": 801}
    tracks = {}
    for track in plan.TRACKS:
        segments = [(0, 20, "stationary_running"), (20, 25, "unknown"), (25, 60, "moving_episode"),
                    (60, 100, "moving_steady"), (100, 120, "stationary_running"), (120, 200, "moving_episode")] if track.endswith("operating") else [
                    (0, 60, "warming"), (60, 80, "unknown"), (80, 200, "stable")]
        tracks[track] = [{"id": f"{track}-{i}", "start_s": a, "end_s": b, "label": label,
                         "review_provenance": "fixture"} for i, (a, b, label) in enumerate(segments)]
    windows = [{"window_index": i, "row_start": i * 10, "row_end": i * 10 + 49, "start_s": i * 2.5, "end_s": i * 2.5 + 12.25,
                "tracks": {t: plan.independent_assignment(tracks[t], i * 2.5, i * 2.5 + 12.25, 200) for t in plan.TRACKS}} for i in range(76)]
    points = [{"canonical_index": i, "sequence": i, "timestamp_ms": i * 250, "elapsed_s": i / 4,
               "vehicle_speed_kmh": i % 30, "rpm": 1000 + i, "tps_voltage": .5 + i / 1000, "tps_raw": i % 30,
               "ect_c": 40 + i / 20, "frame_valid": True, "checksum_valid": True} for i in range(801)]
    return {"source": source, "labels": {"tracks": tracks, "event_markers": []}, "windows": windows, "points": points}


def fixture_packet():
    captures = {f"session_{n:06}": fixture_capture(n) for n in plan.SELECTION}
    regions = plan.select_regions(captures)
    for r in regions:
        r["review_sample_indices"] = list(range(13, 30)) + [49, 50] if r["capture_id"] == "session_000001" else []
    return {"regions": regions}


def test_selection_has_40_regions_and_reproduces_without_detector_outcomes():
    captures = {f"session_{n:06}": fixture_capture(n) for n in plan.SELECTION}
    before = plan.select_regions(captures)
    for c in captures.values():
        for w in c["windows"]:
            w["is_anomaly"], w["score_sample"] = True, -1000000
    assert plan.select_regions(captures) == before
    assert len(before) == 40
    assert [r["review_order"] for r in before] == list(range(1, 41))


def test_discrepancies_pending_review_and_no_decimation():
    check = plan.validate_packet(fixture_packet())
    assert check["pending"] == 40
    assert check["unique_001_discrepancies"] == 19
    assert check["selection_kinds"] == {"full": 1, "window": 20, "boundary": 19}


def test_missing_discrepancy_is_rejected():
    packet = fixture_packet()
    next(r for r in packet["regions"] if r["capture_id"] == "session_000001")["review_sample_indices"].pop()
    with pytest.raises(ValueError, match="missing 001 discrepancies"):
        plan.validate_packet(packet)


@pytest.mark.parametrize("field,value", [("score_sample", -.8), ("is_anomaly", True), ("prediction", -1)])
def test_detector_outcomes_cannot_enter_reviewer_evidence(field, value):
    packet = fixture_packet()
    packet["regions"][0]["samples"][0][field] = value
    with pytest.raises(ValueError, match="detector outcome leaked"):
        plan.validate_packet(packet)


def test_automatic_confirm_is_rejected():
    packet = fixture_packet()
    packet["regions"][0]["review"]["action"] = "Confirm"
    with pytest.raises(ValueError, match="no automatic human review"):
        plan.validate_packet(packet)


def test_boundary_regions_not_misrepresented_as_homogeneous_point_windows():
    regions = fixture_packet()["regions"]
    for r in regions:
        if r["focus"]["kind"] == "boundary":
            assert r["assignment_scope"] == "evidence_region"
            assert r["focus_assignment"][r["focus"]["track"]]["status"] == "ambiguous"


def test_svg_has_every_sample_and_exact_focal_boundary():
    region = fixture_packet()["regions"][0]
    root = ET.fromstring(plan.render_plot(region))
    lines = root.findall("{http://www.w3.org/2000/svg}polyline")
    assert {n.attrib["data-signal"] for n in lines} == {r[0] for r in plan.PLOT_SIGNALS}
    assert all(len(n.attrib["points"].split()) == region["sample_count"] for n in lines)
    assert plan.render_plot(region) == plan.render_plot(region)


def test_nonoverlapping_calibration_selection_uses_sample_geometry():
    windows = [{"row_start": i * 10, "row_end": i * 10 + 49, "window_index": i} for i in range(16)]
    assert [w["window_index"] for w in plan.nonoverlapping(windows)] == [0, 5, 10, 15]


def test_18_balanced_disjoint_fitting_calibration_test_partitions():
    sources = [fixture_capture(n)["source"] for n in range(1, 19)]
    folds = plan.calibration_split(sources)
    value = {"partition_policy": {"folds": folds}, "success_and_stop_rules": {"execution_in_this_phase": False}}
    assert plan.validate_protocol(value)["disjoint_roles_verified"]
    assert plan.calibration_split(list(reversed(sources))) == folds
    value["partition_policy"]["folds"][0]["calibration"][0] = value["partition_policy"]["folds"][0]["test"][0]
    with pytest.raises(ValueError, match="capture leakage"):
        plan.validate_protocol(value)


def test_duplicate_source_hashes_cannot_cross_roles():
    sources = [fixture_capture(n)["source"] for n in range(1, 19)]
    sources[1]["source_sha256"] = sources[0]["source_sha256"]
    with pytest.raises(ValueError, match="distinct original capture"):
        plan.calibration_split(sources)


def test_support_counts_only_calibration_groups_and_abstains_if_sparse():
    sources = [fixture_capture(n)["source"] for n in range(1, 19)]
    fold = plan.calibration_split(sources)[0]
    data = {s["session_id"]: fixture_capture(int(s["session_id"][-6:]))["windows"] for s in fold["calibration"]}
    rows = plan.support_plan([fold], data)
    assert all(not r["test_outcomes_used"] and not r["threshold_learned"] for r in rows)
    sparse = next(r for r in rows if r["context"] == "stationary_running")
    assert not sparse["eligible_to_learn_future_cutoff"]
    assert sparse["qualified_groups"] == 0
    supported = next(r for r in rows if r["context"] == "stable")
    assert supported["eligible_to_learn_future_cutoff"]
    assert supported["qualified_groups"] == 5


@pytest.mark.parametrize("estimator", [IsolationForest, RobustScaler])
def test_design_phase_fitting_guard(estimator):
    with plan.prohibit_fitting(), pytest.raises(AssertionError, match="H10.2A forbids fitting"):
        estimator().fit(np.ones((10, 2)))


def test_published_packet_protocol_and_immutable_inputs():
    if not (plan.STUDY / "readiness.json").exists():
        pytest.skip("build H10.2A packet before published-output verification")
    plan.verify()
    packet = plan.read_json(plan.STUDY / "review_packet.json")
    for sid in {r["capture_id"] for r in packet["regions"]}:
        regions = [r for r in packet["regions"] if r["capture_id"] == sid]
        source = regions[0]
        canonical = plan.import_native_jsonl_file(plan.ROOT / source["source_path"], session_id=sid).canonical_session
        raw = plan.read_raw_capture(str(plan.ROOT / source["source_path"]), source["source_sha256"])
        labels = plan.read_json(plan.EXPORT / "sessions" / sid / "labels.json")
        for r in regions:
            for point in r["samples"]:
                i = point["canonical_index"]
                native = canonical.samples[i]
                assert (point["timestamp_ms"], point["sequence"], point["rpm"], point["tps_voltage"], point["tps_raw"], point["ect_c"]) == (
                    native.timestamp_ms, native.sequence, native.rpm, native.tps_voltage, native.tps_raw, native.ect_c)
                assert point["vehicle_speed_kmh"] == raw.speeds[i]
            for t in plan.TRACKS:
                assert r["proposed_context"][t] == [i for i in labels["tracks"][t] if i["start_s"] <= r["end_s"] and i["end_s"] > r["start_s"]]
    responses = plan.read_json(plan.STUDY / "review_responses.template.json")
    assert len(responses["regions"]) == 40
    assert all(r["action"] is None and r["manual_review_time_s"] is None for r in responses["regions"])
