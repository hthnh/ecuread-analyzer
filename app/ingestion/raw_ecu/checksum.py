from __future__ import annotations


EXPECTED_FRAME_LENGTH = 29
EXPECTED_SUM_MODULO = 251


def is_valid_checksum(frame: list[int]) -> bool:
    """Return True when a legacy 29-byte RAW ECU frame satisfies the checksum rule."""
    if len(frame) != EXPECTED_FRAME_LENGTH:
        return False
    if any(not isinstance(byte, int) or byte < 0 or byte > 255 for byte in frame):
        return False
    return sum(frame) % 256 == EXPECTED_SUM_MODULO


def expected_checksum_byte(frame_without_checksum: list[int]) -> int:
    if len(frame_without_checksum) != EXPECTED_FRAME_LENGTH - 1:
        raise ValueError("expected 28 bytes before checksum")
    if any(not isinstance(byte, int) or byte < 0 or byte > 255 for byte in frame_without_checksum):
        raise ValueError("frame bytes must be integers in 0..255")
    return (EXPECTED_SUM_MODULO - sum(frame_without_checksum)) % 256


def with_recomputed_checksum(frame: list[int]) -> list[int]:
    if len(frame) != EXPECTED_FRAME_LENGTH:
        raise ValueError("expected a 29-byte frame")
    updated = list(frame)
    updated[-1] = expected_checksum_byte(updated[:28])
    return updated

