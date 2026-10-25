"""50k input boundary and complete RAW decoding; inference is deliberately stubbed."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.analysis_service import AnalysisService
from tests.test_raw_decode_analyze_api import asymmetric_native_frame, raw_payload


@pytest.mark.parametrize('representation,separated', [('native24_table17', False), ('legacy29_ff5', True)])
def test_raw_api_accepts_50000_records_without_truncation(settings, representation, separated):
    payload = raw_payload(asymmetric_native_frame(), raw_representation=representation)
    frame = payload['records'][0]['raw_hex']
    if separated:
        frame = ';'.join(frame[i:i+2] for i in range(0, len(frame), 2))
    payload['records'] = [
        {'sequence': i, 'timestamp_ms': i * 250 + 0.123456, 'raw_hex': frame}
        for i in range(50_000)
    ]
    # Match the outgoing client's normal JSON encoding, including spaces.
    body = json.dumps(payload).encode()
    assert len(body) < settings.raw_request_max_bytes

    def accept_complete_input(_service, canonical, **kwargs):
        assert len(canonical.samples) == 50_000
        assert canonical.samples[0].sequence == 0
        assert canonical.samples[-1].sequence == 49_999
        assert canonical.samples[-1].timestamp_ms == payload['records'][-1]['timestamp_ms']
        assert canonical.samples[-1].frame_valid
        return SimpleNamespace(summary={'analysis_run_id': 'validation-only-50k'})

    app = create_app(settings)
    with TestClient(app) as client, patch.object(AnalysisService, 'analyze', autospec=True, side_effect=accept_complete_input) as analyze:
        response = client.post('/api/v1/raw/decode-analyze', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 200
    samples = response.json()['canonical_session']['samples']
    assert len(samples) == 50_000
    assert samples[-1]['sequence'] == 49_999
    analyze.assert_called_once()
