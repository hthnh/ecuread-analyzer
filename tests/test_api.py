from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.ecu.parser import parse_log_file
from app.ml.training import train_isolation_forest
from app.services.session_processor import build_feature_frame, build_frames_dataframe


def train_fixture_model(settings, fixture_path) -> None:
    parse_result = parse_log_file(fixture_path)
    frames = build_frames_dataframe(parse_result, settings, sample_interval_ms=100)
    features = build_feature_frame(frames, settings)
    train_isolation_forest(features, [fixture_path.name], settings)


def test_health_without_model(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["model_loaded"] is False


def test_upload_valid_file_without_model(settings, fixture_204330) -> None:
    client = TestClient(create_app(settings))
    with fixture_204330.open("rb") as handle:
        response = client.post(
            "/api/v1/sessions",
            files={"file": ("serial_log.txt", handle, "text/plain")},
            data={"sample_interval_ms": "100", "device_id": "xiao-ecu-01"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["statistics"]["frames_found"] > 0
    assert body["statistics"]["windows_generated"] > 0
    assert body["result"]["model_loaded"] is False
    assert body["result"]["overall_status"] == "model_unavailable"


def test_upload_empty_file(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.post("/api/v1/sessions", files={"file": ("empty.txt", b"", "text/plain")})
    assert response.status_code == 400


def test_upload_wrong_format(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.post("/api/v1/sessions", files={"file": ("bad.txt", b"hello\n", "text/plain")})
    assert response.status_code == 422


def test_missing_session(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.get("/api/v1/sessions/sess_missing")
    assert response.status_code == 404


def test_model_training_api_disabled(settings, fixture_204330) -> None:
    client = TestClient(create_app(settings))
    with fixture_204330.open("rb") as handle:
        response = client.post("/api/v1/model/train", files={"file": ("log.txt", handle, "text/plain")})
    assert response.status_code == 403


def test_health_and_upload_with_model(settings, fixture_204330) -> None:
    train_fixture_model(settings, fixture_204330)
    client = TestClient(create_app(settings))
    assert client.get("/health").json()["model_loaded"] is True
    with fixture_204330.open("rb") as handle:
        response = client.post(
            "/api/v1/sessions",
            files={"file": ("serial_log.txt", handle, "text/plain")},
            data={"sample_interval_ms": "100"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["model_loaded"] is True
    assert body["result"]["health_score"] is not None

