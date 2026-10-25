from __future__ import annotations

import json
import hashlib
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.api.raw import MAX_RAW_RECORDS

from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.ingestion.raw_ecu.adapter import import_raw_file
from app.ingestion.raw_dispatch import detect_upload_format
from app.main import create_app
from app.ml.model_selection import model_dir_for_schema
from tests.test_raw_decode_analyze_api import asymmetric_native_frame, raw_hex


ROOT = Path(__file__).resolve().parents[1]


def content(frames):
    return "".join(json.dumps({"elapsed_ms": i * 250, "raw_hex": raw_hex(frame)}) + "\n" for i, frame in enumerate(frames)).encode()


def upload(client, payload, route="/api/v1/sessions", filename="original.jsonl", **data):
    return client.post(route, files={"file": (filename, payload, "application/jsonl")},
                       data={"device_id": "raw1-test", "session_id": "same-original", "process_with_model": "false", **data})


@pytest.mark.parametrize("route", ["/api/v1/sessions", "/api/v1/import/raw-session"])
def test_native_dispatch_exact_parity_and_retry(settings, tmp_path, route):
    path = ROOT / "real_data/fulldata_afterclean/session_000001.jsonl"
    expected = import_native_jsonl_file(path).canonical_session
    app = create_app(settings)
    with TestClient(app) as client:
        first = upload(client, path.read_bytes(), route)
        assert first.status_code == 200, first.text
        result = first.json()
        count, samples = app.state.repository.read_telemetry_page(result["session_id"], 1000, 0)
        assert count == len(expected.samples) == 117
        assert samples == [s.model_dump(mode="json") for s in expected.samples]
        assert result["input"]["telemetry_schema_version"] == "canonical-telemetry-v2"
        assert result["input"]["time_basis"] == "source_timestamp_ms"
        entity = app.state.repository.read_session(result["session_id"])
        assert entity["raw_frame_count"] == 117
        assert entity["decoder_id"] == expected.decoder_id
        retry = upload(client, path.read_bytes(), route).json()
        assert retry["duplicate_reused"]
        assert retry["analysis_run_id"] == result["analysis_run_id"]
        assert len(app.state.repository.list_sessions(200)) == 1
        assert len(app.state.repository.list_analyses_for_session(result["session_id"])) == 1


def test_more_than_5000_samples_without_split_or_truncation(settings):
    rows = (ROOT / "real_data/fulldata_afterclean/session_000018.jsonl").read_bytes().splitlines(keepends=True)[:5001]
    app = create_app(replace(settings, window_size_samples=50, window_step_samples=10))
    with TestClient(app) as client:
        response = upload(client, b"".join(rows))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["statistics"]["frames_found"] == result["statistics"]["frames_parsed"] == 5001
    assert result["statistics"]["windows_generated"] == 496
    count, samples = app.state.repository.read_telemetry_page(result["session_id"], 5002, 0)
    assert count == len(samples) == 5001
    assert samples[-1]["sequence"] == 5000
    assert samples[-1]["timestamp_ms"] == json.loads(rows[-1])["elapsed_ms"]


def test_json_array_limit_remains_an_explicit_rejection(settings):
    app = create_app(settings)
    frame = raw_hex(asymmetric_native_frame())
    with TestClient(app) as client:
        response = client.post("/api/v1/raw/decode-analyze", json={
            "session_id": "too-large-for-json-contract", "raw_representation": "native24_table17",
            "process_with_model": False,
            "records": [{"sequence": i, "timestamp_ms": i * 250, "raw_hex": frame} for i in range(MAX_RAW_RECORDS + 1)],
        })
    assert response.status_code == 422
    assert any(error["type"] == "too_long" and error["loc"] == ["body", "records"] for error in response.json()["detail"])
    assert app.state.repository.list_sessions(200) == []


@pytest.mark.parametrize("filename", ["original.jsonl", "original.txt"])
def test_legacy_jsonl_remains_legacy(settings, tmp_path, filename):
    payload = content([[255] * 5 + asymmetric_native_frame() for _ in range(30)])
    source = tmp_path / "legacy.jsonl"
    source.write_bytes(payload)
    expected = import_raw_file(source, settings).canonical_session
    app = create_app(settings)
    with TestClient(app) as client:
        response = upload(client, payload, filename=filename, raw_representation="legacy29_ff5")
    assert response.status_code == 200, response.text
    _, samples = app.state.repository.read_telemetry_page(response.json()["session_id"], 100, 0)
    assert samples == [s.model_dump(mode="json") for s in expected.samples]
    assert response.json()["input"]["telemetry_schema_version"] == "canonical-telemetry-v1"


@pytest.mark.parametrize("payload, requested, message", [
    (content([asymmetric_native_frame(), [255] * 5 + asymmetric_native_frame()]), None, "ambiguous"),
    (content([asymmetric_native_frame()]), "legacy29_ff5", "conflicts"),
    (content([[1] * 24]), None, "structure/header"),
    (b'{"raw_hex":"no-hex"}\n', None, "raw_hex"),
    (b'{"raw_hex":"', None, "malformed JSONL"),
    (b'legacy serial log\n' + content([asymmetric_native_frame()]), None, "malformed JSONL"),
])
def test_ambiguous_or_bad_structure_is_rejected(settings, payload, requested, message):
    app = create_app(settings)
    with TestClient(app) as client:
        response = upload(client, payload, **({"raw_representation": requested} if requested else {}))
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert app.state.repository.list_sessions(200) == []
    assert not list(settings.raw_dir.glob("*/original.txt"))


def test_invalid_native_checksum_rejected_before_commit(settings):
    frame = asymmetric_native_frame()
    frame[-1] ^= 1
    app = create_app(settings)
    with TestClient(app) as client:
        response = upload(client, content([frame] * 30))
    assert response.status_code == 422
    assert "invalid native" in response.json()["detail"]
    assert not list(settings.raw_dir.glob("*/original.txt"))
    assert app.state.repository.list_sessions(200) == []


def test_native_never_selects_legacy_root_model(settings):
    settings.model_dir.mkdir(parents=True)
    (settings.model_dir / "model_metadata.json").write_text('{}')
    assert model_dir_for_schema(settings, "canonical-telemetry-v2") == settings.model_dir / "honda_keihin_71_17_v2"
    assert model_dir_for_schema(settings, "canonical-telemetry-v1") == settings.model_dir


def test_feature_schema_mismatch_remains_explicit_and_unscored(settings):
    native = settings.model_dir / "honda_keihin_71_17_v2"
    shutil.copytree(ROOT / "data/models/honda_keihin_71_17_v2", native)
    metadata = native / "model_metadata.json"
    payload = json.loads(metadata.read_text())
    payload["feature_schema_version"] = "deliberately-incompatible-test-schema"
    metadata.write_text(json.dumps(payload))
    app = create_app(settings)
    with TestClient(app) as client:
        response = upload(client, content([asymmetric_native_frame()] * 60), process_with_model="true")
    assert response.status_code == 200, response.text
    result = response.json()
    detector = result["diagnostic_evidence"]["detectors"]["isolation_forest"]
    assert detector["execution_status"] == "skipped"
    assert detector["skip_or_failure_reason"] == "model_incompatible"
    assert detector["scored_window_count"] == 0
    assert result["result"]["overall_status"] == "model_unavailable"
    assert result["result"]["health_score"] is None
    assert any("feature schema mismatch" in warning for warning in result["result"]["warnings"])


def test_old_legacy_idempotency_result_is_not_silently_reused(settings):
    app = create_app(settings)
    payload = (ROOT / "real_data/fulldata_afterclean/session_000001.jsonl").read_bytes()
    with TestClient(app) as client:
        result = upload(client, payload).json()
        path = app.state.repository.root / "sessions" / f"{result['session_id']}.json"
        stored = json.loads(path.read_text())
        stored["telemetry_schema_version"] = "canonical-telemetry-v1"
        path.write_text(json.dumps(stored))
        retry = upload(client, payload)
    assert retry.status_code == 409
    assert "different decoder contract" in retry.json()["detail"]
    assert len(app.state.repository.list_sessions(200)) == 1


def test_upload_byte_guard_is_retained(settings):
    app = create_app(replace(settings, max_upload_mb=1))
    with TestClient(app) as client:
        response = upload(client, b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413
    assert app.state.repository.list_sessions(200) == []


def test_text_extension_cannot_hide_mixed_jsonl():
    with pytest.raises(ValueError, match="mixed text log and JSONL"):
        detect_upload_format(b"legacy serial log\n" + content([asymmetric_native_frame()]), "upload.txt")


@pytest.mark.parametrize("preamble", [b"[ECU boot]\n", b"\xffECU boot\n"])
def test_legacy_text_preamble_is_not_mistaken_for_json(settings, preamble):
    frame = [255] * 5 + asymmetric_native_frame()
    payload = preamble + (("RAW: " + ";".join(f"{v:02X}" for v in frame) + "\n") * 30).encode()
    app = create_app(settings)
    with TestClient(app) as client:
        response = upload(client, payload, filename="serial.txt", raw_representation="legacy_raw_text", sample_interval_ms="250")
    assert response.status_code == 200, response.text
    assert response.json()["input"]["telemetry_schema_version"] == "canonical-telemetry-v1"
    assert response.json()["statistics"]["frames_parsed"] == 30


def test_persisted_native_model_artifact_is_the_one_actually_selected(settings):
    native = settings.model_dir / "honda_keihin_71_17_v2"
    shutil.copytree(ROOT / "data/models/honda_keihin_71_17_v2", native)
    app = create_app(settings)
    with TestClient(app) as client:
        response = upload(client, content([asymmetric_native_frame()] * 60), process_with_model="true")
    assert response.status_code == 200, response.text
    result = response.json()
    analysis = app.state.repository.read_analysis(result["analysis_run_id"])
    provenance = analysis["model_artifact_provenance"]
    for prefix, filename in (("", "isolation_forest.joblib"), ("scaler_", "robust_scaler.joblib")):
        assert provenance[f"{prefix}artifact_path"] == str(native / filename)
        assert provenance[f"{prefix}artifact_sha256"] == hashlib.sha256((native / filename).read_bytes()).hexdigest()
    registry = json.loads((app.state.repository.root / "model_versions" / f"{analysis['model_version_id']}.json").read_text())
    assert registry["artifact_path"] == provenance["artifact_path"]
    assert registry["artifact_sha256"] == provenance["artifact_sha256"]
    assert result["diagnostic_evidence"]["detectors"]["isolation_forest"]["execution_status"] == "ok"
