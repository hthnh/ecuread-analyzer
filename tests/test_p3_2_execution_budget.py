from dataclasses import replace
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings
from app.services.analysis_service import AnalysisService
from tests.test_canonical_pipeline import make_canonical_session


def test_execution_budget_is_configurable_and_not_45_seconds(monkeypatch):
    monkeypatch.setenv('RAW_ANALYSIS_TIMEOUT_SECONDS','321')
    assert Settings().raw_analysis_timeout_seconds==321


def test_canonical_attempt_is_idempotent_after_app_restart_and_shares_admission(settings):
    payload=make_canonical_session().model_dump(mode='json');headers={'Idempotency-Key':'same-canonical'}
    with TestClient(create_app(settings)) as client:
        first=client.post('/api/v1/analysis',json=payload,headers=headers)
    assert first.status_code==200
    with TestClient(create_app(settings)) as client,patch.object(AnalysisService,'analyze',side_effect=AssertionError('must recover completed analysis')):
        assert client.post('/api/v1/analysis',json=payload,headers=headers).json()==first.json()
        changed={**payload,'vehicle_id':'changed'}
        assert client.post('/api/v1/analysis',json=changed,headers=headers).status_code==409


def test_canonical_budget_and_input_limits_remain_enforced(settings):
    app=create_app(replace(settings,raw_analysis_timeout_seconds=1e-9))
    with TestClient(app) as client:
        response=client.post('/api/v1/analysis',json=make_canonical_session().model_dump(mode='json'))
    assert response.status_code==504


def test_elapsed_60_seconds_is_accepted_by_new_budget_but_rejected_by_old_budget(settings):
    # Advance only the deadline clock, never model inputs or detector outputs.
    from tests.test_raw_request_limits import payload
    for budget,expected in [(45,504),(300,200)]:
        app=create_app(replace(settings,data_dir=settings.data_dir/str(budget),raw_analysis_timeout_seconds=budget))
        with TestClient(app) as client,patch('app.api.raw.monotonic',return_value=1000),patch('app.services.analysis_service.monotonic',return_value=1060):
            response=client.post('/api/v1/raw/decode-analyze',json=payload())
        assert response.status_code==expected
