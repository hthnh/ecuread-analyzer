from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.context_annotation.auto_labeler import RuleConfig, suggest_context
from app.context_annotation.freeze_dataset import freeze_dataset
from app.context_annotation.models import ContextAnnotationDocument, LoadedSession, SessionSource
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.session_loader import LoadedTelemetrySession, load_source
from app.context_annotation.speed import SpeedResolver, decode_speed, read_raw_capture
from app.context_annotation.storage import AnnotationStorage, file_sha256
from app.context_annotation.validation import AnnotationValidationError, find_overlaps
from app.context_annotation.web import create_app
from app.domain.telemetry import CanonicalTelemetrySession, SamplingMetadata, TelemetrySample
from app.ecu.profiles.honda_keihin_71_17 import expected_checksum_byte


def frame(speed: int = 0, rpm: int = 1500, tps: int = 25) -> list[int]:
    b = [2, 24, 0x71, 0x17, rpm >> 8, rpm & 255, tps, 0, 0, 0, 127, 80, 80, 100, 125, 1, 20, 0, 0, speed, 0, 0, 0, 0]
    b[-1] = expected_checksum_byte(b[:-1])
    return b


def loaded_session(times=None, rpm=None, tps=None, ect=None) -> LoadedTelemetrySession:
    times = np.arange(0, 121, 0.5) if times is None else np.asarray(times)
    n = len(times)
    rpm = [1500.0] * n if rpm is None else rpm
    tps = [0.5] * n if tps is None else tps
    ect = [90.0] * n if ect is None else ect
    samples = [TelemetrySample(sequence=i, timestamp_ms=float(t) * 1000, rpm=rpm[i], tps_voltage=tps[i],
                               tps_raw=0, battery_voltage=14, ect_c=ect[i]).model_dump() for i, t in enumerate(times)]
    canonical = CanonicalTelemetrySession(session_id="fixture", decoder_id="test", decoder_version="1",
                                         sampling=SamplingMetadata(sample_interval_ms=500), samples=samples)
    source = SessionSource(source_id="fixture", filename="fixture", path="fixture", sha256="testsha")
    return LoadedTelemetrySession(metadata=LoadedSession(source=source, status="ok", session_id="fixture", duration_s=float(times[-1]), sample_count=n),
                                  canonical_session=canonical, samples=samples, elapsed_s=times.tolist())


def document(loaded, speeds):
    return suggest_context(loaded, speeds=list(speeds), speed_provenance={"status": "test", "available_count": sum(s is not None for s in speeds)})


@pytest.mark.parametrize("prefixed", [False, True])
def test_native_speed_offset_and_normalization(prefixed):
    b = frame(speed=63)
    b[14] = 101
    b[-1] = expected_checksum_byte(b[:-1])
    if prefixed:
        b = [255] * 5 + b
    speed, info = decode_speed(b)
    assert speed == 63
    assert info["valid"]


@pytest.mark.parametrize("malformation", ["checksum", "header", "prefix", "length", "byte"])
def test_malformed_raw_abstains(malformation):
    b = frame(speed=80)
    if malformation == "checksum": b[-1] ^= 1
    if malformation == "header": b[3] = 0x18; b[-1] = expected_checksum_byte(b[:-1])
    if malformation == "prefix": b = [0] * 5 + b
    if malformation == "length": b.pop()
    if malformation == "byte": b[19] = 256
    speed, info = decode_speed(b)
    assert speed is None
    assert not info["valid"]


def test_missing_speed_never_uses_rpm_to_infer_motion_or_stationarity():
    loaded = loaded_session(rpm=[6000.0] * 242)
    doc = document(loaded, [None] * len(loaded.samples))
    assert {s.operating_context for s in doc.segments} == {"unknown"}
    assert doc.metrics["unknown_coverage"] == 1
    assert not doc.events


def test_stops_acceleration_deceleration_and_engine_transitions():
    times = np.arange(0, 181, 0.5)
    speed = np.piecewise(times, [times < 30, (times >= 30) & (times < 60), (times >= 60) & (times < 100), (times >= 100) & (times < 130), times >= 130],
                         [0, lambda t: (t - 30) * 1.5, 45, lambda t: (130 - t) * 1.5, 0])
    rpm = np.where((times < 10) | (times >= 160), 0, np.where(speed > 0, 4000, 1500)).tolist()
    loaded = loaded_session(times=times, rpm=rpm)
    doc = document(loaded, speed)
    states = {s.operating_context for s in doc.segments}
    assert {"engine_off", "stationary_running", "moving_accelerating", "moving_decelerating", "moving_steady"} <= states
    assert {"engine_start", "engine_stop"} <= {e.event for e in doc.events}
    assert not find_overlaps(doc.segments)
    for s in doc.segments:
        if s.operating_context == "stationary_running":
            assert s.evidence["speed_kmh"]["max"] <= 1


def test_quantization_noise_is_not_fragmented_and_invalid_frames_break_evidence():
    loaded = loaded_session()
    speed = [30 + i % 2 for i in range(len(loaded.samples))]
    doc = document(loaded, speed)
    assert len(doc.segments) == 1
    assert doc.segments[0].operating_context == "moving_steady"
    loaded.samples[100]["checksum_valid"] = False
    doc = document(loaded, speed)
    assert any(s.operating_context == "unknown" and s.start_s <= 50 <= s.end_s for s in doc.segments)


def test_proposal_ids_are_deterministic_across_raw_reference_relocation():
    loaded = loaded_session()
    speeds = [30] * len(loaded.samples)
    evidence = {"status": "test", "raw_source_sha256": "same-raw-content", "raw_source_path": "/first/ride.jsonl"}
    first = suggest_context(loaded, speeds=speeds, speed_provenance=evidence)
    second = suggest_context(loaded, speeds=speeds, speed_provenance={**evidence, "raw_source_path": "/relocated/ride.jsonl"})
    assert [s.id for s in first.segments] == [s.id for s in second.segments]
    assert [(s.start_s, s.end_s, s.operating_context) for s in first.segments] == [(s.start_s, s.end_s, s.operating_context) for s in second.segments]


def test_capture_gaps_are_unknown_and_missing_timestamps_are_not_fabricated():
    times = list(np.arange(0, 40, 0.5)) + list(np.arange(60, 100, 0.5))
    loaded = loaded_session(times=times)
    doc = document(loaded, [30] * len(times))
    gap = next(s for s in doc.segments if "capture_gap_s" in s.evidence)
    assert (gap.start_s, gap.end_s, gap.operating_context, gap.thermal_phase) == (39.5, 60, "unknown", "unknown")
    assert gap.evidence["sample_count"] == 0
    loaded.samples[20]["timestamp_ms"] = None
    with pytest.raises(ValueError, match="timestamp integrity"):
        document(loaded, [30] * len(times))


@pytest.mark.parametrize("delta", [0, -10])
def test_duplicate_or_reversed_timestamps_rejected(delta):
    loaded = loaded_session()
    loaded.samples[5]["timestamp_ms"] = loaded.samples[4]["timestamp_ms"] + delta
    with pytest.raises(ValueError, match="non-increasing"):
        document(loaded, [30] * len(loaded.samples))


def test_temperature_trend_not_absolute_cutoff_and_missing_temperature_abstains():
    loaded = loaded_session(ect=(45 + np.arange(242) * 0.1).tolist())
    doc = document(loaded, [0] * len(loaded.samples))
    assert doc.metrics["thermal_duration_s"]["warming"] > 100
    stable = document(loaded_session(ect=[50.0] * 242), [0] * 242)
    assert {s.thermal_phase for s in stable.segments} == {"stable"}
    missing = document(loaded_session(ect=[None] * 242), [0] * 242)
    assert {s.thermal_phase for s in missing.segments} == {"unknown"}


def test_throttle_burst_has_measured_rise_and_requires_sustained_opening():
    times = np.arange(0, 121, 0.5)
    tps = [0.5 if t < 20 or t > 28 else 0.5 + min((t - 20) * 1.0, 2.0) for t in times]
    doc = document(loaded_session(times=times, tps=tps), [30] * len(times))
    events = [e for e in doc.events if e.event == "throttle_burst"]
    assert events
    e = events[0]
    assert e.evidence["tps_rise"] >= RuleConfig().tps_burst_delta_v
    assert 19 <= e.start_s <= 23
    assert e.origin == "auto_suggested" and e.review_status == "pending"
    assert "confidence" not in e.model_dump()
    assert e.original_proposal["evidence"] == e.evidence


def test_repeated_stop_go_uses_speed_cycles_and_does_not_invent_circumstances():
    times = np.arange(0, 121, 0.5)
    speed = [0 if int(t // 15) % 2 == 0 else 25 for t in times]
    doc = document(loaded_session(times=times), speed)
    assert any(e.event == "repeated_stop_go" for e in doc.events)
    labels = {s.operating_context for s in doc.segments}
    assert not {"traffic_light", "city_riding", "highway", "traffic_jam"} & labels


def review_service(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    source = inputs / "real.jsonl"
    source.write_text("\n".join(json.dumps({"elapsed_ms": i * 250, "raw_hex": bytes(frame(speed=30)).hex()}) for i in range(241)) + "\n")
    storage = AnnotationStorage(data_dir=tmp_path, input_dir=inputs)
    service = ContextAnnotationService(storage=storage)
    return service, source


def test_review_roundtrip_v1_preservation_and_no_proposal_coverage(tmp_path):
    service, source = review_service(tmp_path)
    sha = file_sha256(source)
    old = service.add_segment("real", {"start_s": 0, "end_s": 5, "labels": {"engine": "running", "throttle": "closed", "dynamics": "steady", "thermal": "stable"}})
    sidecar = service.storage.annotation_path("real")
    annotation_sha = file_sha256(sidecar)
    proposals = service.review.generate("real")
    assert file_sha256(sidecar) == annotation_sha
    assert service.list_sessions()["sessions"][0]["annotated_duration_s"] == 5
    assert proposals["segments"][0]["conflicts_with_reviewed"]
    assert service.review.decide("real", [proposals["segments"][0]["id"]], "accept", "Researcher")["skipped"]
    item = proposals["segments"][0]
    service.review.edit("real", item["id"], {"start_s": 5, "end_s": 60, "operating_context": "unknown"}, "Researcher")
    result = service.review.decide("real", [item["id"]], "accept", "Researcher")
    accepted = result["annotation"]["segments"][-1]
    assert accepted["origin"] == "auto_suggested"
    assert accepted["human_confirmation"]["reviewer"] == "Researcher"
    assert accepted["human_confirmation"]["corrections"]["operating_context"] == "unknown"
    assert accepted["original_proposal"]["operating_context"] != "unknown"
    assert result["annotation"]["segments"][0]["labels"] == old.segments[0].labels.model_dump()
    assert result["annotation"]["segments"][0]["taxonomy_version"] == "context-taxonomy-v1"
    assert result["annotation"]["schema_version"] == "context-annotation-v2"
    assert service.generate_campaign_status()["annotated_duration_s"] == 60
    assert service.generate_campaign_status()["explicitly_unknown_duration_s"] == 55
    reloaded = ContextAnnotationService(storage=service.storage).session_detail("real")
    assert reloaded["annotation"]["segments"][-1] == accepted
    before = file_sha256(sidecar)
    service.review.generate("real")
    assert file_sha256(sidecar) == before
    assert file_sha256(source) == sha


def test_rejection_persists_rerun_and_source_change_blocks_review(tmp_path):
    service, source = review_service(tmp_path)
    proposals = service.review.generate("real")
    item = proposals["segments"][0]
    service.review.decide("real", [item["id"]], "reject", "Researcher", notes="boundary uncertain")
    assert service.review.generate("real")["segments"][0]["review_status"] == "rejected"
    assert not service.storage.annotation_path("real").exists()
    source.write_text(source.read_text() + "\n")
    with pytest.raises(AnnotationValidationError, match="SHA mismatch"):
        service.review.decide("real", [item["id"]], "accept", "Researcher")


def test_pending_proposals_cannot_be_written_as_annotations(tmp_path):
    service, _ = review_service(tmp_path)
    doc = service.annotation_for(service.load_session("real")).model_dump()
    doc["schema_version"] = "context-annotation-v2"
    doc["segments"] = [{"id": "auto1", "start_s": 0, "end_s": 10, "taxonomy_version": "context-taxonomy-v2",
                        "labels": {"operating_context": "moving_steady", "thermal_phase": "stable"},
                        "origin": "auto_suggested", "review_status": "pending"}]
    with pytest.raises(ValidationError):
        ContextAnnotationDocument.model_validate(doc)


def test_raw_only_tps_without_battery_is_supported_and_speed_absence_is_explicit(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    samples = loaded_session().samples
    for i, sample in enumerate(samples):
        sample["tps_voltage"] = None
        sample["battery_voltage"] = None
        sample["tps_raw"] = 100 if 50 <= i <= 80 else 0
    (inputs / "limited.jsonl").write_text("\n".join(json.dumps(sample) for sample in samples) + "\n")
    service = ContextAnnotationService(storage=AnnotationStorage(data_dir=tmp_path, input_dir=inputs))
    loaded = service.load_session("limited")
    assert loaded.metadata.status == "ok"
    doc = service.review.generate("limited")
    assert doc["metrics"]["unknown_coverage"] == 1
    assert doc["signal_provenance"]["status"] == "unavailable"
    assert any(e["event"] == "throttle_burst" and e["evidence"]["tps_basis"] == "tps_raw" for e in doc["events"])


def test_events_do_not_add_duration_and_web_api_is_reviewable(tmp_path):
    service, source = review_service(tmp_path)
    client = TestClient(create_app(service))
    sha = file_sha256(source)
    response = client.post("/context-annotation/api/sessions/real/auto-label", json={})
    assert response.status_code == 200
    item = response.json()["segments"][0]
    response = client.patch(f"/context-annotation/api/sessions/real/suggestions/{item['id']}", json={"reviewer": "Researcher", "changes": {"end_s": 59}})
    assert response.status_code == 200
    response = client.post("/context-annotation/api/sessions/real/suggestion-review", json={"reviewer": "Researcher", "action": "accept", "start_s": 0, "end_s": 60})
    assert response.status_code == 200
    assert response.json()["decided"] == [item["id"]]
    response = client.post("/context-annotation/api/sessions/real/events", json={"start_s": 59, "end_s": 60, "event": "engine_stop"})
    assert response.status_code == 200
    assert service.generate_campaign_status()["annotated_duration_s"] == 59
    assert file_sha256(source) == sha


@pytest.mark.parametrize("name", ["session_000002", "session_000004", "session_000005", "session_000007"])
def test_four_real_raw_speed_distributions(name):
    path = Path("real_data/real_run") / f"{name}.jsonl"
    capture = read_raw_capture(str(path), file_sha256(path))
    speed = np.asarray(capture.speeds, dtype=float)
    assert np.isfinite(speed).all()
    assert speed.min() == 0
    assert 40 < np.quantile(speed, 0.95) < 130
    assert capture.validation_counts.get("invalid_frames", 0) == 0


def test_real_canonical_part_alignment_and_raw_sha_guard(tmp_path):
    raw = Path("real_data/fulldata_afterclean/session_000002.jsonl")
    original_source = SessionSource(source_id=raw.stem, filename=raw.name, path=str(raw), sha256=file_sha256(raw))
    full = load_source(original_source)
    sid = "calib-real-run-session-000002-part-02"
    canonical = full.canonical_session.model_copy(update={"session_id": sid, "samples": full.canonical_session.samples[5000:]})
    part = tmp_path / "part.json"
    part.write_text(canonical.model_dump_json())
    loaded = load_source(SessionSource(source_id=sid, filename=part.name, path=str(part), sha256=file_sha256(part)))
    entry = {"session_id": sid, "canonical_sha256": file_sha256(part), "raw_source_path": str(raw),
             "raw_source_sha256": file_sha256(raw), "sample_offset": 5000, "sample_count": len(canonical.samples),
             "timestamp_field": "elapsed_ms"}
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps({"sessions": [entry]}))
    speed, info = SpeedResolver(map_path).resolve(loaded)
    assert info["status"] == "available" and info["sample_offset"] == 5000
    original = read_raw_capture(entry["raw_source_path"], entry["raw_source_sha256"])
    assert speed == original.speeds[5000:]
    entry["raw_source_sha256"] = "wronghash"
    map_path.write_text(json.dumps({"sessions": [entry]}))
    speed, info = SpeedResolver(map_path).resolve(loaded)
    assert all(s is None for s in speed)
    assert "SHA mismatch" in info["reason"]


def test_accepted_auto_events_and_v2_freeze_retain_provenance(tmp_path):
    service, source = review_service(tmp_path)
    rows = []
    for i in range(241):
        voltage_byte = 100 if 50 <= i <= 80 else 25
        rows.append({"elapsed_ms": i * 250, "raw_hex": bytes(frame(speed=30, tps=voltage_byte)).hex()})
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    source_sha = file_sha256(source)
    proposals = service.review.generate("real")
    event = next(e for e in proposals["events"] if e["event"] == "throttle_burst")
    result = service.review.decide("real", [event["id"]], "accept", "Researcher")
    assert result["annotation"]["events"][0]["origin"] == "auto_suggested"
    assert service.generate_campaign_status()["annotated_duration_s"] == 0
    service.review.decide("real", [], "accept", "Researcher", start_s=0, end_s=60)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"schema_version": "context-curated-corpus-v1", "dataset_id": "test-context",
        "independent_captured_sessions": 1, "created_at": "2026-10-09T00:00:00Z", "selection_rule": "isolated test",
        "sessions": [{"session_id": "real", "source_path": str(source), "source_sha256": source_sha, "reason_included": "test"}]}))
    first = freeze_dataset(corpus_manifest=manifest_path, output=tmp_path / "freeze-a", data_dir=tmp_path,
                           annotations_dir=service.storage.annotations_dir)
    second = freeze_dataset(corpus_manifest=manifest_path, output=tmp_path / "freeze-b", data_dir=tmp_path,
                            annotations_dir=service.storage.annotations_dir)
    assert first["content_fingerprint_sha256"] == second["content_fingerprint_sha256"]
    assert first["annotation_schema_version"] == "context-annotation-v2"
    assert first["sessions"][0]["events"][0]["human_confirmation"]["reviewer"] == "Researcher"
    assert "rule_config" in first["sessions"][0]["segments"][0]["original_proposal"]
    assert file_sha256(source) == source_sha
    accepted = service.annotation_for(service.load_session("real")).segments[0]
    corrected = service.update_segment("real", accepted.id,
        {"labels": {"operating_context": "unknown", "thermal_phase": "stable"}}, reviewer="Second researcher")
    latest = corrected.segments[0]
    assert latest.origin == "auto_suggested" and latest.rule_version == accepted.rule_version
    assert latest.human_confirmation.reviewer == "Second researcher"
    assert latest.confirmation_history[-1].reviewer == "Researcher"
    assert latest.original_proposal == accepted.original_proposal
