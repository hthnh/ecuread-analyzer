from dataclasses import replace
import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
import httpx

from app.api.raw import raw_request_slot
from app.main import create_app
from app.services.analysis_service import AnalysisService
from tests.test_raw_decode_analyze_api import asymmetric_native_frame, raw_payload


def payload():
    result = raw_payload(asymmetric_native_frame())
    result["idempotency_key"] = "attempt-1"
    return result


@pytest.mark.parametrize("chunked", [False, True])
def test_byte_limit_precedes_parsing_and_persistence(settings, chunked):
    app = create_app(replace(settings, raw_request_max_bytes=256))
    body = b" " * 257
    with TestClient(app) as client:
        response = client.post("/api/v1/raw/decode-analyze", content=iter([body[:128], body[128:]]) if chunked else body)
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "RAW_REQUEST_TOO_LARGE"
    assert app.state.repository.list_sessions() == []


def test_body_deadline_releases_admission_without_persistence(settings):
    app = create_app(replace(settings, raw_body_timeout_seconds=0.01))

    async def exercise():
        async def slow_body():
            yield b'{'
            await asyncio.sleep(0.1)
            yield b'}'

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/raw/decode-analyze", content=slow_body())
            assert response.status_code == 408
            assert response.json()["detail"]["code"] == "RAW_BODY_TIMEOUT"
            retry = await client.post("/api/v1/raw/decode-analyze", json=payload())
            assert retry.status_code == 200

    asyncio.run(exercise())


def test_busy_requests_are_not_queued_or_persisted(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        with raw_request_slot(settings.data_dir):
            response = client.post("/api/v1/raw/decode-analyze", json=payload())
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "5"
        assert app.state.repository.list_sessions() == []
        assert client.post("/api/v1/raw/decode-analyze", json=payload()).status_code == 200


def test_expired_analysis_returns_retryable_error_without_persistence(settings):
    app = create_app(replace(settings, raw_analysis_timeout_seconds=1e-9))
    with TestClient(app) as client:
        response = client.post("/api/v1/raw/decode-analyze", json=payload())
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "RAW_ANALYSIS_TIMEOUT"
    assert app.state.repository.list_sessions() == []
    reservation = json.loads(next(settings.data_dir.glob("raw_request_idempotency/*.json")).read_text())
    assert "response" not in reservation


def test_retry_reuses_completed_result_even_after_app_restart(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.post("/api/v1/raw/decode-analyze", json=payload())
    assert first.status_code == 200
    run = first.json()["analysis"]["analysis_run_id"]
    with TestClient(create_app(settings)) as client, patch.object(AnalysisService, "analyze", side_effect=AssertionError("retry must not infer again")):
        retry = client.post("/api/v1/raw/decode-analyze", json=payload())
        assert retry.status_code == 200
        assert retry.json() == first.json()
        changed = {**payload(), "session_note": "different input"}
        conflict = client.post("/api/v1/raw/decode-analyze", json=changed)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "RAW_IDEMPOTENCY_CONFLICT"
    assert [a["id"] for a in app.state.repository.list_analyses_for_session(payload()["session_id"])] == [run]


def test_new_key_creates_new_analysis_and_retains_old_result(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.post("/api/v1/raw/decode-analyze", json=payload()).json()
        second = client.post("/api/v1/raw/decode-analyze", json={**payload(), "idempotency_key": "attempt-2"}).json()
    assert first["analysis"]["analysis_run_id"] != second["analysis"]["analysis_run_id"]
    assert first["canonical_session"] == second["canonical_session"]
    assert len(app.state.repository.list_analyses_for_session(payload()["session_id"])) == 2


def test_restart_after_completed_run_recovers_without_reanalysis(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.post("/api/v1/raw/decode-analyze", json=payload())
    path = next(settings.data_dir.glob("raw_request_idempotency/*.json"))
    cache = json.loads(path.read_text())
    # Emulate a process loss just before the final response-cache replacement.
    path.write_text(json.dumps({"input_sha256": cache["input_sha256"],
        "analysis_run_id": first.json()["analysis"]["analysis_run_id"],
        "canonical_session": cache["response"]["canonical_session"]}))
    with TestClient(create_app(settings)) as client, patch.object(AnalysisService, "analyze", side_effect=AssertionError("completed attempt must not run again")):
        retry = client.post("/api/v1/raw/decode-analyze", json=payload())
    assert retry.status_code == 200 and retry.json() == first.json()


def test_retry_repairs_interrupted_persistence_under_the_same_reserved_run(settings):
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch.object(app.state.repository, "write_analysis_run", side_effect=OSError("interrupted write")):
            first = client.post("/api/v1/raw/decode-analyze", json=payload())
        assert first.status_code == 500
        path = next(settings.data_dir.glob("raw_request_idempotency/*.json"))
        reserved = json.loads(path.read_text())["analysis_run_id"]
        assert app.state.repository.read_analysis(reserved) is None
        retry = client.post("/api/v1/raw/decode-analyze", json=payload())
    assert retry.status_code == 200
    assert retry.json()["analysis"]["analysis_run_id"] == reserved
    assert len(list((settings.data_dir / "analyses").iterdir())) == 1


def test_raw_session_identifier_cannot_escape_storage(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/v1/raw/decode-analyze", json={**payload(), "session_id": "../escape"})
    assert response.status_code == 422
    assert app.state.repository.list_sessions() == []
