from __future__ import annotations

from app.ecu.parser import parse_log_text


VALID_FRAME = "FF;FF;FF;FF;FF;2;18;71;17;0;0;19;0;FF;FF;81;49;5C;59;7D;0;0;58;7C;0;0;0;0;77"
VALID_FRAME_TWO_DIGIT = "FF;FF;FF;FF;FF;02;18;71;17;00;00;19;00;FF;FF;81;49;5C;59;7D;00;00;58;7C;00;00;00;00;77"


def test_parser_raw_same_line() -> None:
    result = parse_log_text(f"noise\nRAW: {VALID_FRAME}\n")
    assert result.statistics.frames_found == 1
    assert result.statistics.frames_parsed == 1
    assert result.frames[0].parse_ok is True
    assert result.frames[0].bytes[5] == 0x02


def test_parser_raw_next_line() -> None:
    result = parse_log_text(f"RAW:\n{VALID_FRAME_TWO_DIGIT}\n")
    assert result.statistics.frames_found == 1
    assert result.statistics.frames_parsed == 1
    assert result.frames[0].line_number == 2


def test_parser_accepts_one_and_two_character_hex() -> None:
    result = parse_log_text(f"RAW: {VALID_FRAME}")
    assert result.frames[0].bytes[5] == 2
    assert result.frames[0].bytes[9] == 0


def test_parser_rejects_short_frame_without_aborting_file() -> None:
    result = parse_log_text(f"RAW: FF;FF\nRAW: {VALID_FRAME}\n")
    assert result.statistics.frames_found == 2
    assert result.statistics.frame_errors == 1
    assert result.statistics.frames_parsed == 1
    assert result.frames[1].parse_ok is True


def test_parser_rejects_non_hex_token() -> None:
    result = parse_log_text("RAW: FF;NOPE;00\n")
    assert result.statistics.frames_found == 1
    assert result.frames[0].parse_ok is False
    assert "not 1-2 digit hex" in result.frames[0].parse_error


def test_parser_multiple_frames() -> None:
    result = parse_log_text(f"RAW: {VALID_FRAME}\nRAW:\n{VALID_FRAME_TWO_DIGIT}\n")
    assert result.statistics.frames_found == 2
    assert result.statistics.frames_parsed == 2
    assert result.frames[0].frame_index == 0
    assert result.frames[1].frame_index == 1

