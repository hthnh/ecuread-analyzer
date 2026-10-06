from __future__ import annotations

import pytest

from app.ecu.checksum import is_valid_checksum
from app.ecu.decoder import decode_frame


def bytes_from_raw(raw: str) -> list[int]:
    return [int(token, 16) for token in raw.split(";")]


def test_decoder_confirmed_signals_sample_one() -> None:
    frame = bytes_from_raw("FF;FF;FF;FF;FF;02;18;71;17;07;76;19;00;FF;FF;7F;4A;67;54;89;03;D0;80;6E;00;00;00;00;FC")
    assert is_valid_checksum(frame)
    decoded = decode_frame(frame, frame_index=0)
    assert decoded.rpm == 1910
    assert decoded.tps_voltage == pytest.approx(0.48828125)
    assert decoded.tps_raw_candidate == 0
    assert decoded.battery_voltage == pytest.approx(12.7)
    assert decoded.iat_c == 34
    assert decoded.ect_c_candidate == 63
    assert decoded.map_raw == 84


def test_decoder_confirmed_signals_sample_two() -> None:
    frame = bytes_from_raw("FF;FF;FF;FF;FF;02;18;71;17;00;00;19;00;FF;FF;81;49;5C;59;7D;00;00;58;7C;00;00;00;00;77")
    assert is_valid_checksum(frame)
    decoded = decode_frame(frame, frame_index=0)
    assert decoded.rpm == 0
    assert decoded.battery_voltage == pytest.approx(12.9)
    assert decoded.iat_c == 33
    assert decoded.ect_c_candidate == 52
    assert decoded.map_raw == 89

