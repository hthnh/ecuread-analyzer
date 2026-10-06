from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
JSONL_SESSION_000001 = ROOT / "real_data" / "real_run" / "session_000001.jsonl"
JSONL_SESSION_000002 = ROOT / "real_data" / "real_run" / "session_000002.jsonl"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload_session(
    client: TestClient,
    path: Path,
    *,
    device_id: str = "sim-esp-e2e-001",
    session_id: str | None = "road-run-000001",
):
    data = {
        "device_id": device_id,
        "vehicle_id": "honda_shmode_4v_001",
        "firmware_version": "sim-fw-e2e-0.1",
        "sample_interval_ms": "100",
    }
    if session_id is not None:
        data["session_id"] = session_id
    with path.open("rb") as handle:
        return client.post(
            "/api/v1/sessions",
            files={"file": (path.name, handle, "application/jsonl")},
            data=data,
        )


def committed_raw_files(settings) -> list[Path]:
    return list((settings.data_dir / "raw").glob("*/original.txt"))


def entity_session_files(settings) -> list[Path]:
    return list((settings.data_dir / "entities" / "sessions").glob("*.json"))


def raw_artifact_files(settings) -> list[Path]:
    return list((settings.data_dir / "entities" / "raw_artifacts").glob("*.json"))


def test_legacy_upload_regression_generates_canonical_telemetry(settings, fixture_204330) -> None:
    client = TestClient(create_app(settings))
    with fixture_204330.open("rb") as handle:
        response = client.post(
            "/api/v1/sessions",
            files={"file": (fixture_204330.name, handle, "text/plain")},
            data={"sample_interval_ms": "100", "device_id": "xiao-ecu-01"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["statistics"]["frames_found"] > 0
    assert body["statistics"]["frames_parsed"] > 0

    telemetry = client.get(f"/api/v1/sessions/{body['session_id']}/telemetry")
    assert telemetry.status_code == 200
    assert telemetry.json()["count"] == body["statistics"]["frames_found"]


def test_real_jsonl_upload_generates_canonical_artifacts_and_preserves_raw(settings) -> None:
    client = TestClient(create_app(settings))
    response = upload_session(client, JSONL_SESSION_000001)

    assert response.status_code == 200
    body = response.json()
    assert body["statistics"]["source_format"] == "esp_jsonl"
    assert body["statistics"]["frames_found"] > 0
    assert body["statistics"]["frames_parsed"] > 0
    assert body["statistics"]["frame_errors"] == 0
    assert body["statistics"]["windows_generated"] > 0
    assert body["input"]["time_basis"] == "source_timestamp_ms"
    assert body["result"]["overall_status"] in {"model_unavailable", "healthy", "warning"}

    persisted_raw = Path(body["artifacts"]["raw"])
    assert persisted_raw.exists()
    assert sha256(persisted_raw) == sha256(JSONL_SESSION_000001)

    telemetry = client.get(f"/api/v1/sessions/{body['session_id']}/telemetry")
    assert telemetry.status_code == 200
    assert telemetry.json()["count"] == body["statistics"]["frames_found"]

    analyses = client.get(f"/api/v1/sessions/{body['session_id']}/analyses")
    assert analyses.status_code == 200
    assert len(analyses.json()["analyses"]) == 1


def test_retry_same_device_session_and_payload_reuses_existing_result(settings) -> None:
    client = TestClient(create_app(settings))
    first = upload_session(client, JSONL_SESSION_000001)
    second = upload_session(client, JSONL_SESSION_000001)

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert second_body["duplicate_reused"] is True
    assert second_body["session_id"] == first_body["session_id"]
    assert second_body["analysis_run_id"] == first_body["analysis_run_id"]
    assert len(entity_session_files(settings)) == 1
    assert len(raw_artifact_files(settings)) == 1
    assert len(committed_raw_files(settings)) == 1


def test_same_device_session_with_different_payload_conflicts(settings) -> None:
    client = TestClient(create_app(settings))
    first = upload_session(client, JSONL_SESSION_000001, session_id="road-run-collision")
    second = upload_session(client, JSONL_SESSION_000002, session_id="road-run-collision")

    assert first.status_code == 200
    assert second.status_code == 409
    assert "conflicts" in second.json()["detail"]
    assert len(entity_session_files(settings)) == 1
    assert len(raw_artifact_files(settings)) == 1
    assert len(committed_raw_files(settings)) == 1


def test_malformed_jsonl_is_rejected_without_committed_raw_or_session(settings, tmp_path) -> None:
    client = TestClient(create_app(settings))
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text('{"elapsed_ms": 0, "raw_hex": \n', encoding="utf-8")

    response = upload_session(client, malformed, session_id="bad-jsonl")

    assert response.status_code == 422
    assert "malformed JSONL upload" in response.json()["detail"]
    assert committed_raw_files(settings) == []
    assert entity_session_files(settings) == []
    assert raw_artifact_files(settings) == []


def test_invalid_jsonl_raw_hex_is_rejected_without_committed_raw(settings, tmp_path) -> None:
    client = TestClient(create_app(settings))
    invalid = tmp_path / "invalid_raw_hex.jsonl"
    invalid.write_text('{"elapsed_ms": 0, "raw_hex": "not-hex"}\n', encoding="utf-8")

    response = upload_session(client, invalid, session_id="bad-raw-hex")

    assert response.status_code == 422
    assert "raw_hex" in response.json()["detail"]
    assert committed_raw_files(settings) == []
    assert entity_session_files(settings) == []
    assert raw_artifact_files(settings) == []
