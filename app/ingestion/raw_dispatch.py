"""Select an existing RAW importer without guessing a decoding contract."""
from __future__ import annotations

import json
from pathlib import Path

from app.ecu.profiles.honda_keihin_71_17 import HEADER, FRAME_LENGTH, LEGACY_PREFIX
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.ingestion.raw_ecu.adapter import import_raw_file, parse_result_to_canonical_session
from app.ingestion.raw_ecu.parser import _parse_raw_hex_value, _raw_length_error, parse_jsonl_file


SUPPORTED_UPLOAD_FORMATS = {"native24_table17", "legacy29_ff5", "legacy_raw_text"}


def detect_upload_format(content: bytes, filename: str | None, requested: str | None = None) -> str:
    if requested is not None and requested not in SUPPORTED_UPLOAD_FORMATS:
        raise ValueError(f"unsupported raw_representation: {requested!r}")
    text = content.decode("utf-8", errors="replace")
    lines = [(i, line.strip()) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
    jsonl = (Path(filename or "").suffix.lower() == ".jsonl"
             or bool(lines and lines[0][1].startswith("{"))
             or requested in {"native24_table17", "legacy29_ff5"})
    if not jsonl:
        if any(line.startswith("{") for _, line in lines):
            raise ValueError("ambiguous RAW upload: mixed text log and JSONL records")
        if requested not in {None, "legacy_raw_text"}:
            raise ValueError("raw_representation requires JSONL records with raw_hex")
        return "legacy_raw_text"
    formats = set()
    for number, line in lines:
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"malformed JSONL upload at line {number}: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"malformed JSONL upload: line {number} must be an object")
        frame, error = _parse_raw_hex_value(record.get("raw_hex"))
        error = error or _raw_length_error(record, len(frame))
        if error:
            raise ValueError(f"malformed JSONL upload: line {number}: {error}")
        if len(frame) == FRAME_LENGTH and frame[:len(HEADER)] == HEADER:
            formats.add("native24_table17")
        elif len(frame) == FRAME_LENGTH + len(LEGACY_PREFIX) and frame[:len(LEGACY_PREFIX)] == LEGACY_PREFIX:
            formats.add("legacy29_ff5")
        else:
            raise ValueError(f"unsupported RAW structure/header at line {number}; expected native24 Table 17 or legacy29 FF5")
    if len(formats) != 1:
        raise ValueError("ambiguous or empty RAW JSONL: use one consistent native24 or legacy29 representation")
    detected = formats.pop()
    if requested is not None and requested != detected:
        raise ValueError(f"raw_representation={requested!r} conflicts with detected {detected!r}")
    return detected


def import_dispatched_raw_file(path: Path, settings, *, raw_representation: str | None = None, **metadata):
    representation = detect_upload_format(path.read_bytes(), path.name, raw_representation)
    if representation == "native24_table17":
        result = import_native_jsonl_file(path, **metadata)
    elif representation == "legacy29_ff5":
        result = parse_result_to_canonical_session(parse_jsonl_file(path), settings, **metadata)
    else:
        result = import_raw_file(path, settings, **metadata)
    result.statistics["raw_representation"] = representation
    return result
