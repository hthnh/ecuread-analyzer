from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from app.ingestion.raw_ecu.checksum import EXPECTED_FRAME_LENGTH, is_valid_checksum


RAW_PREFIX_RE = re.compile(r"^\s*RAW\s*:\s*(?P<payload>.*)\s*$", re.IGNORECASE)
HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{1,2}$")
COMPACT_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
JSONL_NORMALIZED_FRAME_LENGTH = 24
JSONL_LEGACY_PREFIX = [0xFF] * 5
JSONL_TIMESTAMP_FIELDS = ("elapsed_ms", "timestamp_ms", "device_time_ms")


@dataclass(slots=True)
class FrameParseResult:
    frame_index: int
    line_number: int
    raw_frame: str
    bytes: list[int]
    parse_ok: bool
    parse_error: str | None
    checksum_valid: bool
    timestamp_ms: float | None = None
    timestamp_source: str | None = None
    source_format: str = "legacy_raw_text"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["bytes_hex"] = ";".join(f"{byte:02X}" for byte in self.bytes) if self.bytes else ""
        return data


@dataclass(slots=True)
class ParseStatistics:
    total_lines: int
    frames_found: int
    frames_parsed: int
    frame_errors: int
    checksum_valid_frames: int
    source_format: str = "legacy_raw_text"
    timestamped_frames: int = 0

    @property
    def checksum_invalid_frames(self) -> int:
        return max(0, self.frames_parsed - self.checksum_valid_frames)

    def to_dict(self) -> dict[str, int | str]:
        return {
            "total_lines": self.total_lines,
            "frames_found": self.frames_found,
            "frames_parsed": self.frames_parsed,
            "frame_errors": self.frame_errors,
            "checksum_valid_frames": self.checksum_valid_frames,
            "checksum_invalid_frames": self.checksum_invalid_frames,
            "source_format": self.source_format,
            "timestamped_frames": self.timestamped_frames,
        }


@dataclass(slots=True)
class ParseResult:
    frames: list[FrameParseResult]
    statistics: ParseStatistics


def _parse_frame_payload(payload: str) -> tuple[list[int], str | None]:
    tokens = [token.strip() for token in payload.strip().split(";")]
    if tokens == [""] or not tokens:
        return [], "empty RAW frame"

    parsed: list[int] = []
    token_errors: list[str] = []
    for position, token in enumerate(tokens):
        if not HEX_TOKEN_RE.match(token):
            token_errors.append(f"token {position} is not 1-2 digit hex: {token!r}")
            continue
        value = int(token, 16)
        if value < 0 or value > 255:
            token_errors.append(f"token {position} is outside byte range: {token!r}")
            continue
        parsed.append(value)

    if token_errors:
        return parsed, "; ".join(token_errors)
    if len(parsed) != EXPECTED_FRAME_LENGTH:
        return parsed, f"expected {EXPECTED_FRAME_LENGTH} bytes, got {len(parsed)}"
    return parsed, None


def _parse_raw_hex_value(value: Any) -> tuple[list[int], str | None]:
    if not isinstance(value, str):
        return [], "raw_hex is missing or is not a string"

    payload = value.strip()
    if not payload:
        return [], "raw_hex is empty"

    if any(separator in payload for separator in (";", " ", ",", "\t", "\n")):
        tokens = [token for token in re.split(r"[\s;,]+", payload) if token]
        if not tokens:
            return [], "raw_hex is empty"
        parsed: list[int] = []
        for position, token in enumerate(tokens):
            token = token.removeprefix("0x").removeprefix("0X")
            if not HEX_TOKEN_RE.match(token):
                return parsed, f"raw_hex token {position} is not 1-2 digit hex: {token!r}"
            parsed.append(int(token, 16))
        return parsed, None

    compact = payload.removeprefix("0x").removeprefix("0X")
    if not COMPACT_HEX_RE.fullmatch(compact):
        return [], "raw_hex contains non-hex characters"
    if len(compact) % 2:
        return [], "raw_hex has odd number of hex characters"
    return [int(compact[index : index + 2], 16) for index in range(0, len(compact), 2)], None


def _jsonl_frame_to_legacy_compat(raw_bytes: list[int]) -> tuple[list[int], str | None]:
    if len(raw_bytes) == EXPECTED_FRAME_LENGTH and raw_bytes[:5] == JSONL_LEGACY_PREFIX:
        return raw_bytes, None
    if len(raw_bytes) == JSONL_NORMALIZED_FRAME_LENGTH:
        return [*JSONL_LEGACY_PREFIX, *raw_bytes], None
    if len(raw_bytes) == EXPECTED_FRAME_LENGTH:
        return raw_bytes, "29-byte raw_hex must start with five FF prefix bytes"
    return raw_bytes, (
        f"expected {JSONL_NORMALIZED_FRAME_LENGTH} bytes or {EXPECTED_FRAME_LENGTH} bytes "
        "with five leading FF bytes"
    )


def _coerce_optional_number(value: Any, field_name: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"{field_name} must be numeric"
    if isinstance(value, int | float):
        return float(value), None
    if isinstance(value, str) and value.strip():
        try:
            return float(value), None
        except ValueError:
            return None, f"{field_name} must be numeric"
    return None, None


def _extract_jsonl_timestamp(record: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
    for field_name in JSONL_TIMESTAMP_FIELDS:
        timestamp_ms, error = _coerce_optional_number(record.get(field_name), field_name)
        if error is not None:
            return None, None, error
        if timestamp_ms is not None:
            return timestamp_ms, field_name, None
    return None, None, None


def _raw_length_error(record: dict[str, Any], raw_length: int) -> str | None:
    expected = record.get("raw_length")
    if expected is None:
        return None
    if isinstance(expected, bool):
        return "raw_length must be numeric"
    if isinstance(expected, int | float):
        expected_int = int(expected)
    elif isinstance(expected, str) and expected.strip().isdigit():
        expected_int = int(expected)
    else:
        return "raw_length must be numeric"
    if expected_int != raw_length:
        return f"raw_length={expected_int} does not match parsed raw_hex length {raw_length}"
    return None


def parse_log_text(text: str) -> ParseResult:
    """Find RAW ECU frames while preserving per-frame parse failures."""
    lines = text.splitlines()
    frames: list[FrameParseResult] = []
    consumed_payload_lines: set[int] = set()

    for line_index, line in enumerate(lines):
        if line_index in consumed_payload_lines:
            continue
        match = RAW_PREFIX_RE.match(line)
        if not match:
            continue

        payload = match.group("payload").strip()
        payload_line_number = line_index + 1
        if payload == "":
            next_index = line_index + 1
            while next_index < len(lines) and lines[next_index].strip() == "":
                next_index += 1
            if next_index < len(lines):
                payload = lines[next_index].strip()
                payload_line_number = next_index + 1
                consumed_payload_lines.add(next_index)

        frame_index = len(frames)
        frame_bytes, parse_error = _parse_frame_payload(payload)
        parse_ok = parse_error is None
        checksum_valid = is_valid_checksum(frame_bytes) if parse_ok else False
        frames.append(
            FrameParseResult(
                frame_index=frame_index,
                line_number=payload_line_number,
                raw_frame=payload,
                bytes=frame_bytes,
                parse_ok=parse_ok,
                parse_error=parse_error,
                checksum_valid=checksum_valid,
                source_format="legacy_raw_text",
            )
        )

    statistics = ParseStatistics(
        total_lines=len(lines),
        frames_found=len(frames),
        frames_parsed=sum(1 for frame in frames if frame.parse_ok),
        frame_errors=sum(1 for frame in frames if not frame.parse_ok),
        checksum_valid_frames=sum(1 for frame in frames if frame.checksum_valid),
        source_format="legacy_raw_text",
        timestamped_frames=sum(1 for frame in frames if frame.timestamp_ms is not None),
    )
    return ParseResult(frames=frames, statistics=statistics)


def parse_jsonl_file(path: str | Path) -> ParseResult:
    """Parse ESP road-run JSONL while emitting legacy-compatible frame bytes."""
    frames: list[FrameParseResult] = []
    total_lines = 0

    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            total_lines += 1
            raw_line = line.strip()
            if not raw_line:
                continue

            frame_index = len(frames)
            timestamp_ms: float | None = None
            timestamp_source: str | None = None
            frame_bytes: list[int] = []
            parse_error: str | None = None

            try:
                record = json.loads(raw_line)
            except JSONDecodeError as exc:
                parse_error = f"malformed JSON at line {line_number}: {exc.msg}"
                record = None

            if parse_error is None and not isinstance(record, dict):
                parse_error = f"JSONL record at line {line_number} must be an object"

            if parse_error is None and isinstance(record, dict):
                timestamp_ms, timestamp_source, parse_error = _extract_jsonl_timestamp(record)

            if parse_error is None and isinstance(record, dict):
                raw_bytes, parse_error = _parse_raw_hex_value(record.get("raw_hex"))
                if parse_error is None:
                    length_error = _raw_length_error(record, len(raw_bytes))
                    if length_error is not None:
                        parse_error = length_error
                if parse_error is None:
                    frame_bytes, parse_error = _jsonl_frame_to_legacy_compat(raw_bytes)

            parse_ok = parse_error is None
            checksum_valid = is_valid_checksum(frame_bytes) if parse_ok else False
            frames.append(
                FrameParseResult(
                    frame_index=frame_index,
                    line_number=line_number,
                    raw_frame=raw_line,
                    bytes=frame_bytes,
                    parse_ok=parse_ok,
                    parse_error=parse_error,
                    checksum_valid=checksum_valid,
                    timestamp_ms=timestamp_ms,
                    timestamp_source=timestamp_source,
                    source_format="esp_jsonl",
                )
            )

    statistics = ParseStatistics(
        total_lines=total_lines,
        frames_found=len(frames),
        frames_parsed=sum(1 for frame in frames if frame.parse_ok),
        frame_errors=sum(1 for frame in frames if not frame.parse_ok),
        checksum_valid_frames=sum(1 for frame in frames if frame.checksum_valid),
        source_format="esp_jsonl",
        timestamped_frames=sum(1 for frame in frames if frame.timestamp_ms is not None),
    )
    return ParseResult(frames=frames, statistics=statistics)


def _looks_like_jsonl_file(path: Path) -> bool:
    if path.suffix.lower() == ".jsonl":
        return True
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.lstrip()
            if not stripped:
                continue
            return stripped.startswith("{")
    return False


def parse_log_file(path: str | Path) -> ParseResult:
    resolved = Path(path)
    if _looks_like_jsonl_file(resolved):
        return parse_jsonl_file(resolved)
    return parse_log_text(resolved.read_text(encoding="utf-8", errors="replace"))
