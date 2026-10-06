from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def canonical_payload(count: int = 25) -> dict:
    return {
        "session_id": "sess_api_canonical",
        "vehicle_id": "bike-001",
        "device_id": "xiao-ecu-01",
        "ecu_profile_id": "honda_keihin_legacy_29",
        "decoder_id": "honda_keihin_legacy_29",
        "decoder_version": "0.1.0",
        "sampling": {"sample_interval_ms": 100},
        "samples": [
            {
                "sequence": index,
                "timestamp_ms": index * 100,
                "rpm": 1200 + index,
                "tps_voltage": 0.5,
                "tps_raw_candidate": 0,
                "battery_voltage": 12.8,
                "iat_c": 33,
                "ect_c_candidate": 52,
                "map_raw": 89,
                "frame_valid": True,
                "checksum_valid": True,
            }
            for index in range(count)
        ],
    }


def test_canonical_analysis_api(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.post("/api/v1/analysis", json=canonical_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "sess_api_canonical"
    assert body["analysis_run_id"]
    assert body["feature_schema_version"] == "ecu-window-features-v1"
    assert body["overall_status"] == "model_unavailable"

    telemetry = client.get("/api/v1/sessions/sess_api_canonical/telemetry")
    assert telemetry.status_code == 200
    assert telemetry.json()["count"] == 25

    analyses = client.get("/api/v1/sessions/sess_api_canonical/analyses")
    assert analyses.status_code == 200
    assert analyses.json()["analyses"][0]["id"] == body["analysis_run_id"]

    windows = client.get(f"/api/v1/analyses/{body['analysis_run_id']}/windows")
    assert windows.status_code == 200
    assert windows.json()["count"] > 0

    second_response = client.post("/api/v1/analysis", json=canonical_payload())
    assert second_response.status_code == 200
    assert second_response.json()["analysis_run_id"] != body["analysis_run_id"]
    analyses = client.get("/api/v1/sessions/sess_api_canonical/analyses")
    assert len(analyses.json()["analyses"]) == 2


def test_raw_compatibility_endpoint_persists_traceable_entities(settings, fixture_204330) -> None:
    client = TestClient(create_app(settings))
    with fixture_204330.open("rb") as handle:
        response = client.post(
            "/api/v1/import/raw-session",
            files={"file": ("serial_log.txt", handle, "text/plain")},
            data={"sample_interval_ms": "100", "device_id": "xiao-ecu-01"},
        )
    assert response.status_code == 200
    body = response.json()
    session_id = body["session_id"]
    analysis_run_id = body["analysis_run_id"]

    assert (settings.data_dir / "entities" / "sessions" / f"{session_id}.json").exists()
    assert list((settings.data_dir / "entities" / "raw_artifacts").glob("*.json"))
    assert list((settings.data_dir / "entities" / "decoder_versions").glob("*.json"))
    assert (settings.data_dir / "telemetry" / session_id / "samples.jsonl").exists()
    assert (settings.data_dir / "analyses" / analysis_run_id / "analysis_run.json").exists()
    assert (settings.data_dir / "analyses" / analysis_run_id / "analysis_windows.jsonl").exists()
