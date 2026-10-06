from __future__ import annotations

import pytest

from app.ecu.profiles.honda_keihin_71_17 import (
    FrameFormatError,
    checksum_valid,
    decode_frame,
    expected_checksum_byte,
    normalize_frame,
    validate_normalized_frame,
)


EXAMPLE_HEX = "0218711700141900FFFF60571F846E03ED9E7C0000000061"


def frame_from_hex(raw_hex: str) -> list[int]:
    return list(bytes.fromhex(raw_hex))


def test_example_frame_candidate_decode() -> None:
    frame = frame_from_hex(EXAMPLE_HEX)
    validation = validate_normalized_frame(frame)
    decoded = decode_frame(frame, strict_checksum=True)

    assert len(frame) == 24
    assert validation.length_valid is True
    assert validation.header_valid is True
    assert validation.checksum_valid is True
    assert checksum_valid(frame)
    assert decoded["rpm"] == 20
    assert decoded["tps_voltage_candidate"] == pytest.approx(0.48828125)
    assert decoded["tps_position_raw_candidate"] == 0
    assert decoded["ect_voltage_raw"] == 255
    assert decoded["ect_temperature_raw"] == 255
    assert decoded["ect_status"] == "unavailable_or_unsupported"
    assert decoded["fields"]["ect"]["status"] == "unavailable"
    assert decoded["iat_voltage_candidate"] == pytest.approx(1.875)
    assert decoded["iat_c_candidate"] == 47
    assert decoded["map_voltage_candidate"] == pytest.approx(0.60546875)
    assert decoded["map_engineering_raw_candidate"] == 132
    assert decoded["battery_v_candidate"] == pytest.approx(11.0)
    assert decoded["injector_raw_candidate"] == 1005


def test_checksum_corruption_blocks_strict_decode() -> None:
    frame = frame_from_hex(EXAMPLE_HEX)
    frame[-1] ^= 0x01
    decoded = decode_frame(frame, strict_checksum=True)
    assert decoded["checksum_valid"] is False
    assert decoded["parse_valid"] is False
    assert decoded["fields"] == {}


def test_invalid_header_is_not_profile_supported() -> None:
    frame = frame_from_hex(EXAMPLE_HEX)
    frame[2] = 0x72
    frame[-1] = expected_checksum_byte(frame[:-1])
    decoded = decode_frame(frame, strict_checksum=True)
    assert decoded["length_valid"] is True
    assert decoded["header_valid"] is False
    assert decoded["profile_supported"] is False
    assert decoded["fields"] == {}


def test_wrong_frame_length_is_rejected() -> None:
    with pytest.raises(FrameFormatError):
        normalize_frame(frame_from_hex(EXAMPLE_HEX)[:-1])


def test_legacy_29_byte_normalization() -> None:
    frame = frame_from_hex(EXAMPLE_HEX)
    legacy = [0xFF] * 5 + frame
    normalized, source_format = normalize_frame(legacy, allow_legacy_29_byte=True)
    assert normalized == frame
    assert source_format == "legacy_29_ff_prefix"
    assert decode_frame(legacy, strict_checksum=True, allow_legacy_29_byte=True)["rpm"] == 20


def test_legacy_29_byte_requires_opt_in() -> None:
    legacy = [0xFF] * 5 + frame_from_hex(EXAMPLE_HEX)
    with pytest.raises(FrameFormatError):
        normalize_frame(legacy, allow_legacy_29_byte=False)


def test_non_sentinel_temperature_pair_is_candidate() -> None:
    frame = frame_from_hex(EXAMPLE_HEX)
    frame[8] = 0x60
    frame[9] = 0x57
    frame[-1] = expected_checksum_byte(frame[:-1])
    decoded = decode_frame(frame, strict_checksum=True)
    assert decoded["ect_status"] == "candidate"
    assert decoded["ect_voltage_candidate"] == pytest.approx(1.875)
    assert decoded["ect_c_candidate"] == 47

