from __future__ import annotations

from app.ecu.checksum import is_valid_checksum


VALID_FRAME = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x02, 0x18, 0x71, 0x17, 0x00, 0x00, 0x19, 0x00, 0xFF, 0xFF, 0x81, 0x49, 0x5C, 0x59, 0x7D, 0x00, 0x00, 0x58, 0x7C, 0x00, 0x00, 0x00, 0x00, 0x77]


def test_checksum_valid() -> None:
    assert is_valid_checksum(VALID_FRAME)


def test_checksum_invalid_modified_byte() -> None:
    frame = list(VALID_FRAME)
    frame[10] = 1
    assert not is_valid_checksum(frame)


def test_checksum_invalid_short_frame() -> None:
    assert not is_valid_checksum(VALID_FRAME[:-1])


def test_checksum_invalid_byte_range() -> None:
    frame = list(VALID_FRAME)
    frame[3] = 256
    assert not is_valid_checksum(frame)

