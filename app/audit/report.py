from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean, median
from typing import Any, Iterable

from app.audit.byte_statistics import (
    BYTE_ATLAS_RANGE,
    change_point_summary,
    compute_byte_statistics,
    correlation_matrix,
    frozen_signal_summary,
    injector_scale_summary,
    write_correlation_heatmap_svg,
    write_timeseries_svg,
)
from app.audit.calibration import run_calibration_audit
from app.audit.formula_search import infer_formula_matches
from app.audit.loading import parse_raw_hex
from app.audit.plausibility import evaluate_plausibility, readiness_gate
from app.ecu.profile_registry import get_profile
from app.ecu.profiles.honda_keihin_71_17 import (
    FrameFormatError,
    bytes_to_hex,
    default_profile_document,
    decode_frame,
    normalize_frame,
    validate_normalized_frame,
)


SUPPORTED_SUFFIXES = {".jsonl", ".log", ".txt"}
DECODED_JSON_FIELDS = [
    "rpm",
    "tps_voltage",
    "tps_percent",
    "iat_c",
    "ect_c",
    "battery_v",
    "injector_raw",
    "injector_ms",
    "fuel_cut_inferred",
]


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "session"


def discover_input_files(input_dir: Path | None, input_paths: list[Path]) -> tuple[list[Path], list[dict[str, Any]]]:
    discovered: list[Path] = []
    unsupported: list[dict[str, Any]] = []
    if input_dir is not None:
        if not input_dir.exists():
            raise ValueError(f"input directory does not exist: {input_dir}")
        for path in sorted(input_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                discovered.append(path)
            else:
                unsupported.append(
                    {
                        "source_file": str(path),
                        "issue": "unsupported file extension",
                        "supported_extensions": ",".join(sorted(SUPPORTED_SUFFIXES)),
                    }
                )
    for path in input_paths:
        if not path.exists():
            unsupported.append({"source_file": str(path), "issue": "input file does not exist"})
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            unsupported.append(
                {
                    "source_file": str(path),
                    "issue": "unsupported file extension",
                    "supported_extensions": ",".join(sorted(SUPPORTED_SUFFIXES)),
                }
            )
            continue
        discovered.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in discovered:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique, unsupported


def _decoded_fields_from_json(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row[field] for field in DECODED_JSON_FIELDS if field in row}


def _jsonl_entries(path: Path, *, max_records: int | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    accepted = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            if max_records is not None and accepted >= max_records:
                break
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                malformed.append(
                    {
                        "source_file": str(path),
                        "line_number": line_number,
                        "issue": f"malformed JSONL record: {exc.msg}",
                    }
                )
                continue
            raw_hex = row.get("raw_hex")
            if not isinstance(raw_hex, str):
                malformed.append(
                    {
                        "source_file": str(path),
                        "line_number": line_number,
                        "issue": "missing string raw_hex",
                    }
                )
                continue
            try:
                raw_bytes = parse_raw_hex(raw_hex)
            except ValueError as exc:
                malformed.append(
                    {
                        "source_file": str(path),
                        "line_number": line_number,
                        "issue": f"malformed raw string: {exc}",
                    }
                )
                continue
            entries.append(
                {
                    "source_file": str(path),
                    "line_number": line_number,
                    "source_type": "jsonl",
                    "raw_bytes": raw_bytes,
                    "raw_hex": raw_hex,
                    "seq": row.get("seq"),
                    "device_time_ms": row.get("device_time_ms"),
                    "session_hash": row.get("session_hash"),
                    "decoded_fields": _decoded_fields_from_json(row),
                    "source_record": row,
                }
            )
            accepted += 1
    return entries, malformed


RAW_PREFIX_RE = re.compile(r"^\s*RAW\s*:\s*(?P<payload>.*)\s*$", re.IGNORECASE)


def _text_entries(path: Path, *, max_records: int | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    consumed_payload_lines: set[int] = set()
    for line_index, line in enumerate(lines):
        if max_records is not None and len(entries) >= max_records:
            break
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
        try:
            raw_bytes = parse_raw_hex(payload)
        except ValueError as exc:
            malformed.append(
                {
                    "source_file": str(path),
                    "line_number": payload_line_number,
                    "issue": f"malformed RAW frame: {exc}",
                }
            )
            continue
        entries.append(
            {
                "source_file": str(path),
                "line_number": payload_line_number,
                "source_type": "text",
                "raw_bytes": raw_bytes,
                "raw_hex": payload,
                "seq": len(entries) + 1,
                "device_time_ms": None,
                "session_hash": path.stem,
                "decoded_fields": {},
                "source_record": {},
            }
        )
    return entries, malformed


def load_source_entries(path: Path, *, max_records: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix.lower() == ".jsonl":
        return _jsonl_entries(path, max_records=max_records)
    return _text_entries(path, max_records=max_records)


def _positive_time_gap_threshold(deltas: list[int | float]) -> float:
    positive = [float(delta) for delta in deltas if isinstance(delta, int | float) and delta > 0]
    if not positive:
        return 1000.0
    return max(1000.0, float(median(positive)) * 10.0)


def _quality_for_source(
    path: Path,
    entries: list[dict[str, Any]],
    malformed: list[dict[str, Any]],
    normalized_records: list[dict[str, Any]],
) -> dict[str, Any]:
    source_records = [record for record in normalized_records if record.get("source_file") == str(path)]
    normalized_frames = [record for record in source_records if record.get("normalized_hex")]
    valid_records = [record for record in source_records if record.get("parse_valid")]
    checksums = [record for record in source_records if record.get("length_valid") and record.get("header_valid")]
    checksum_failures = sum(1 for record in checksums if not record.get("checksum_valid"))
    header_failures = sum(1 for record in source_records if record.get("length_valid") and not record.get("header_valid"))
    length_failures = sum(1 for record in source_records if not record.get("length_valid"))
    unsupported_format = sum(1 for record in source_records if record.get("format_error"))

    normalized_hexes = [record["normalized_hex"] for record in normalized_frames]
    duplicate_frames = sum(count - 1 for count in Counter(normalized_hexes).values() if count > 1)
    consecutive_duplicate_frames = sum(
        1
        for index in range(1, len(normalized_hexes))
        if normalized_hexes[index] == normalized_hexes[index - 1]
    )

    seqs = [entry.get("seq") for entry in entries]
    missing_seq = sum(1 for seq in seqs if not isinstance(seq, int))
    repeated_seq = 0
    seq_gap_count = 0
    missing_seq_values = 0
    previous_seq: int | None = None
    for seq in seqs:
        if not isinstance(seq, int):
            continue
        if previous_seq is not None:
            if seq == previous_seq:
                repeated_seq += 1
            elif seq > previous_seq + 1:
                seq_gap_count += 1
                missing_seq_values += seq - previous_seq - 1
        previous_seq = seq

    times = [entry.get("device_time_ms") for entry in entries]
    time_deltas: list[int | float] = []
    time_regressions = 0
    previous_time: int | float | None = None
    for value in times:
        if not isinstance(value, int | float):
            continue
        if previous_time is not None:
            delta = value - previous_time
            time_deltas.append(delta)
            if delta < 0:
                time_regressions += 1
        previous_time = value
    threshold = _positive_time_gap_threshold(time_deltas)
    large_time_gaps = sum(1 for delta in time_deltas if isinstance(delta, int | float) and delta > threshold)

    session_hashes = [entry.get("session_hash") for entry in entries if entry.get("session_hash") is not None]
    session_hash_changes = sum(
        1 for index in range(1, len(session_hashes)) if session_hashes[index] != session_hashes[index - 1]
    )

    normalized_24 = sum(1 for record in source_records if record.get("source_format") == "normalized_24")
    legacy_29 = sum(1 for record in source_records if record.get("source_format") == "legacy_29_ff_prefix")
    records_seen = len(entries) + len(malformed)
    checksum_failure_ratio = checksum_failures / len(checksums) if checksums else 0.0
    return {
        "source_file": str(path),
        "records_seen": records_seen,
        "records_parsed": len(entries),
        "malformed_records": len(malformed),
        "normalized_24_frames": normalized_24,
        "legacy_29_frames": legacy_29,
        "unsupported_frame_formats": unsupported_format,
        "valid_frames": len(valid_records),
        "length_failures": length_failures,
        "header_failures": header_failures,
        "checksum_failures": checksum_failures,
        "checksum_failure_ratio": checksum_failure_ratio,
        "duplicate_frames": duplicate_frames,
        "consecutive_duplicate_frames": consecutive_duplicate_frames,
        "missing_seq": missing_seq,
        "repeated_seq": repeated_seq,
        "seq_gap_count": seq_gap_count,
        "missing_seq_values": missing_seq_values,
        "time_regressions": time_regressions,
        "large_time_gaps": large_time_gaps,
        "large_time_gap_threshold_ms": threshold,
        "session_hash_changes": session_hash_changes,
        "session_hashes": ";".join(str(value) for value in sorted(set(session_hashes))),
    }


def _aggregate_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = defaultdict(int)
    for row in rows:
        for key, value in row.items():
            if key in {"source_file", "session_hashes"}:
                continue
            if isinstance(value, int):
                totals[key] += value
            elif isinstance(value, float) and key != "checksum_failure_ratio":
                totals[key] += value
    records_seen = int(totals.get("records_seen", 0))
    checksum_failures = int(totals.get("checksum_failures", 0))
    checksum_checked = sum(row["checksum_failures"] / row["checksum_failure_ratio"] for row in rows if row.get("checksum_failure_ratio"))
    if checksum_checked:
        checksum_failure_ratio = checksum_failures / checksum_checked
    else:
        valid_like = sum(row["valid_frames"] + row["checksum_failures"] for row in rows)
        checksum_failure_ratio = checksum_failures / valid_like if valid_like else 0.0
    totals["checksum_failure_ratio"] = checksum_failure_ratio
    totals["records_seen"] = records_seen
    return dict(totals)


def _normalize_entries(
    entries_by_file: dict[Path, list[dict[str, Any]]],
    *,
    allow_legacy_29_byte: bool,
    strict_checksum: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_records: list[dict[str, Any]] = []
    valid_records: list[dict[str, Any]] = []
    for entries in entries_by_file.values():
        for entry in entries:
            record = {
                "source_file": entry["source_file"],
                "line_number": entry["line_number"],
                "source_type": entry["source_type"],
                "raw_hex": entry["raw_hex"],
                "raw_length": len(entry["raw_bytes"]),
                "seq": entry.get("seq"),
                "device_time_ms": entry.get("device_time_ms"),
                "session_hash": entry.get("session_hash"),
                "decoded_fields": entry.get("decoded_fields", {}),
            }
            try:
                frame, source_format = normalize_frame(
                    entry["raw_bytes"], allow_legacy_29_byte=allow_legacy_29_byte
                )
            except FrameFormatError as exc:
                record.update(
                    {
                        "frame": None,
                        "normalized_hex": "",
                        "source_format": "",
                        "parse_valid": False,
                        "length_valid": False,
                        "header_valid": False,
                        "checksum_valid": False,
                        "profile_supported": False,
                        "format_error": str(exc),
                        "candidate": {},
                    }
                )
                normalized_records.append(record)
                continue

            validation = validate_normalized_frame(frame, source_format=source_format)
            candidate: dict[str, Any] = {}
            if validation.length_valid and validation.header_valid and (validation.parse_valid or not strict_checksum):
                decoded = decode_frame(frame, strict_checksum=strict_checksum)
                if decoded.get("fields") or validation.parse_valid:
                    candidate = decoded
            record.update(
                {
                    "frame": frame,
                    "normalized_hex": bytes_to_hex(frame),
                    "source_format": source_format,
                    "parse_valid": validation.parse_valid,
                    "length_valid": validation.length_valid,
                    "header_valid": validation.header_valid,
                    "checksum_valid": validation.checksum_valid,
                    "profile_supported": validation.profile_supported,
                    "validation_errors": "; ".join(validation.errors),
                    "format_error": "",
                    "candidate": candidate,
                }
            )
            normalized_records.append(record)
            if validation.parse_valid:
                valid_records.append(record)
    return normalized_records, valid_records


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], *, fieldnames: list[str] | None = None) -> None:
    materialized = list(rows)
    if fieldnames is None:
        fieldnames = []
        seen: set[str] = set()
        for row in materialized:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _csv_value(value: Any) -> Any:
    if isinstance(value, list | dict):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return value
    if value is None:
        return ""
    return value


def _normalized_frame_rows(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        candidate = record.get("candidate", {})
        rows.append(
            {
                "source_file": record.get("source_file"),
                "line_number": record.get("line_number"),
                "seq": record.get("seq"),
                "device_time_ms": record.get("device_time_ms"),
                "session_hash": record.get("session_hash"),
                "raw_length": record.get("raw_length"),
                "source_format": record.get("source_format"),
                "normalized_hex": record.get("normalized_hex"),
                "parse_valid": record.get("parse_valid"),
                "length_valid": record.get("length_valid"),
                "header_valid": record.get("header_valid"),
                "checksum_valid": record.get("checksum_valid"),
                "profile_supported": record.get("profile_supported"),
                "validation_errors": record.get("validation_errors") or record.get("format_error"),
                "rpm": candidate.get("rpm"),
                "tps_voltage_candidate": candidate.get("tps_voltage_candidate"),
                "tps_position_raw_candidate": candidate.get("tps_position_raw_candidate"),
                "ect_status": candidate.get("ect_status"),
                "iat_c_candidate": candidate.get("iat_c_candidate"),
                "map_voltage_candidate": candidate.get("map_voltage_candidate"),
                "map_engineering_raw_candidate": candidate.get("map_engineering_raw_candidate"),
                "battery_v_candidate": candidate.get("battery_v_candidate"),
                "injector_raw_candidate": candidate.get("injector_raw_candidate"),
                "injector_scale_status": candidate.get("injector_scale_status"),
                "ignition_raw_candidate": candidate.get("ignition_raw_candidate"),
                "speed_or_signal_raw_candidate": candidate.get("speed_or_signal_raw_candidate"),
                "unknown_reserved_raw": candidate.get("unknown_reserved_raw"),
            }
        )
    return rows


def _mapping_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in matches:
        interpretation = "explains_existing_decoder"
        field = match["decoded_field"]
        if field == "iat_c" and match["best_raw_source"] == "byte 12" and match["best_transform"] == "raw - 40":
            interpretation = "offset_suspected: byte 12 is MAP voltage candidate in v0.1"
        if field == "ect_c" and match["best_raw_source"] == "byte 13" and match["best_transform"] == "raw - 40":
            interpretation = "offset_suspected: byte 13 is MAP engineering raw candidate in v0.1"
        rows.append({**match, "interpretation": interpretation})
    return rows


def _candidate_mapping_summary() -> list[dict[str, Any]]:
    return [
        {
            "field": "rpm",
            "source_bytes": "4-5",
            "formula": "big-endian unsigned integer",
            "status": "high_confidence_candidate",
        },
        {
            "field": "tps_voltage_candidate",
            "source_bytes": "6",
            "formula": "raw * 5 / 256",
            "status": "high_confidence_candidate",
        },
        {
            "field": "tps_position_raw_candidate",
            "source_bytes": "7",
            "formula": "raw preserved",
            "status": "candidate",
        },
        {
            "field": "ect",
            "source_bytes": "8-9",
            "formula": "FF/FF sentinel; otherwise voltage and raw-40 candidate",
            "status": "unavailable",
        },
        {
            "field": "iat_c_candidate",
            "source_bytes": "10-11",
            "formula": "byte 11 - 40, with byte 10 voltage candidate",
            "status": "high_confidence_candidate",
        },
        {
            "field": "map",
            "source_bytes": "12-13",
            "formula": "byte 12 voltage; byte 13 raw preserved",
            "status": "scale_unverified",
        },
        {
            "field": "battery_v_candidate",
            "source_bytes": "14",
            "formula": "raw / 10",
            "status": "high_confidence_candidate",
        },
        {
            "field": "injector_raw_candidate",
            "source_bytes": "15-16",
            "formula": "big-endian unsigned integer; ms scale unverified",
            "status": "scale_unverified",
        },
        {
            "field": "ignition_raw_candidate",
            "source_bytes": "17",
            "formula": "raw preserved",
            "status": "unknown",
        },
        {
            "field": "speed_or_signal_raw_candidate",
            "source_bytes": "18",
            "formula": "raw preserved; not speed without external reference",
            "status": "candidate",
        },
    ]


def _write_ground_truth_plan(path: Path) -> None:
    text = """# Ground-Truth Test Plan

This plan is for the next controlled recording session. It avoids manual road-condition labeling and focuses on synchronized external references that can validate the 0x71/0x17 byte layout.

## Key-On, Engine-Off

Record external ambient temperature, battery voltage measured by multimeter, throttle fully closed, and vehicle stationary.

Expected uses:

- Identify MAP atmospheric baseline.
- Confirm battery scaling.
- Confirm TPS closed value.
- Find the true speed byte.
- Detect unsupported ECT/EOT channels.

## Cold Start

Record ambient temperature before starting.

Expected uses:

- IAT should initially be near ambient.
- ECT or EOT should initially be near ambient.
- Battery should drop during cranking.
- Injector duration should increase during start.

## Warm-Up At Idle

Record for several minutes without throttle changes where possible.

Expected uses:

- Engine temperature should rise smoothly.
- IAT should not behave identically to engine temperature.
- MAP should remain within an idle operating band.

## Throttle Sweep With Engine Off

Slowly move the throttle from closed to open and back while stationary.

Expected uses:

- Identify TPS voltage byte.
- Identify TPS position byte.
- Derive closed and open calibration points.

## External Battery Reference

Log simultaneous multimeter readings at key-on engine-off, cranking, idle, and elevated RPM.

## Injector Validation

Where safe and technically possible, compare the candidate injector duration with an oscilloscope or logic analyzer measurement. Do not rely on firmware `/100` scaling until this is checked.

## Vehicle Speed Validation

Record externally measured speed or GPS speed and synchronize identifiable speed steps. Do not require dangerous interaction while riding.
"""
    path.write_text(text, encoding="utf-8")


def _summary_markdown(summary: dict[str, Any]) -> str:
    sessions = summary["sessions"]
    session_lines = "\n".join(
        f"- `{Path(row['source_file']).name}`: {row['records_parsed']} parsed records, "
        f"{row['valid_frames']} valid frames, checksum failure ratio {row['checksum_failure_ratio']:.4%}"
        for row in sessions
    )
    formula_lines = "\n".join(
        f"- `{row['decoded_field']}`: {row['best_raw_source']} via `{row['best_transform']}` "
        f"(exact {row['exact_match_ratio']:.2%}, mean error {row['mean_error']:.6g})"
        for row in summary["existing_decoder_mappings"]
    )
    readiness_lines = "\n".join(f"- {reason}" for reason in summary["readiness"]["reasons"])
    safe_fields = ", ".join(f"`{field}`" for field in summary["safe_ml_candidate_fields"]) or "none"
    unresolved = ", ".join(f"`{field}`" for field in summary["unresolved_fields"]) or "none"

    return f"""# Decoder Audit Summary

## 1. Verified Frame-Level Facts

- Profile: `{summary['profile_id']}`
- Total parsed records: {summary['totals']['records_parsed']}
- Structurally valid frames: {summary['totals']['valid_frames']}
- Checksum rule verified as `sum(frame) % 256 == 0` for valid normalized 24-byte frames.
- Normalized 24-byte frames: {summary['totals']['normalized_24_frames']}
- Legacy 29-byte frames normalized: {summary['totals']['legacy_29_frames']}

{session_lines}

## 2. High-Confidence Field Candidates

- `rpm`: bytes 4-5, big-endian unsigned integer.
- `tps_voltage_candidate`: byte 6, `raw * 5 / 256`.
- `iat_c_candidate`: byte 11 minus 40 with byte 10 as voltage candidate. This is a high-confidence candidate pending ground-truth validation, not a declared truth.
- `battery_v_candidate`: byte 14, `raw / 10`.

## 3. Weak Candidates

- `tps_position_raw_candidate`: byte 7 preserved raw; no percent formula selected.
- `map`: byte 12 voltage candidate and byte 13 engineering raw; scale unverified.
- `injector_raw_candidate`: bytes 15-16 big-endian raw; `/100`, `/250`, and `/256` are compared but not verified.
- `speed_or_signal_raw_candidate`: byte 18 preserved raw; not named speed without external reference.

## 4. Contradicted Existing Mappings

{formula_lines}

The existing JSON fields `iat_c` and `ect_c` are explained by bytes 12 and 13 using `raw - 40`. Under the v0.1 candidate profile, those bytes belong to the MAP candidate pair, so the old temperature mapping is marked `offset_suspected`.

## 5. Unresolved Fields

{unresolved}

## 6. Required Ground-Truth Experiments

See `ground_truth_test_plan.md` for key-on engine-off, cold-start, warm-up, throttle sweep, battery, injector, and vehicle-speed validation procedures.

## 7. Dataset Readiness

- `ready_for_ml`: {str(summary['readiness']['ready_for_ml']).lower()}
- Fields with sufficient candidate status for later ML consideration: {safe_fields}

Blocking reasons:

{readiness_lines}
"""


def _profile_with_evidence(
    *,
    profile_id: str,
    input_files: list[Path],
    totals: dict[str, Any],
    formula_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    profile = default_profile_document(profile_id)
    profile["evidence"].append(
        {
            "input_files": [str(path) for path in input_files],
            "records_parsed": totals.get("records_parsed", 0),
            "valid_frames": totals.get("valid_frames", 0),
            "checksum_failure_ratio": totals.get("checksum_failure_ratio", 0.0),
            "existing_decoder_findings": [
                row
                for row in formula_matches
                if row.get("decoded_field") in {"iat_c", "ect_c", "rpm", "battery_v", "injector_raw"}
            ],
        }
    )
    profile["limitations"].append(
        "The supplied logs do not include external temperature, speed, multimeter, injector pulse, ECU ID, or ECU part-number references."
    )
    return profile


def run_audit(
    *,
    input_dir: Path | None,
    input_paths: list[Path],
    profile_id: str,
    output_dir: Path,
    strict_checksum: bool = False,
    allow_legacy_29_byte: bool = False,
    generate_plots: bool = False,
    compare_existing_fields: bool = False,
    include_calibration: bool = False,
    max_records: int | None = None,
) -> dict[str, Any]:
    get_profile(profile_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    plots_dir = output_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    input_files, unsupported = discover_input_files(input_dir, input_paths)
    entries_by_file: dict[Path, list[dict[str, Any]]] = {}
    malformed_by_file: dict[Path, list[dict[str, Any]]] = {}
    malformed_rows: list[dict[str, Any]] = []
    for path in input_files:
        entries, malformed = load_source_entries(path, max_records=max_records)
        entries_by_file[path] = entries
        malformed_by_file[path] = malformed
        malformed_rows.extend(malformed)

    normalized_records, valid_records = _normalize_entries(
        entries_by_file,
        allow_legacy_29_byte=allow_legacy_29_byte,
        strict_checksum=strict_checksum,
    )

    quality_rows = [
        _quality_for_source(path, entries_by_file[path], malformed_by_file[path], normalized_records)
        for path in input_files
    ]
    for row in unsupported:
        quality_rows.append(
            {
                "source_file": row["source_file"],
                "records_seen": 0,
                "records_parsed": 0,
                "malformed_records": 0,
                "normalized_24_frames": 0,
                "legacy_29_frames": 0,
                "unsupported_frame_formats": 0,
                "valid_frames": 0,
                "length_failures": 0,
                "header_failures": 0,
                "checksum_failures": 0,
                "checksum_failure_ratio": 0.0,
                "duplicate_frames": 0,
                "consecutive_duplicate_frames": 0,
                "missing_seq": 0,
                "repeated_seq": 0,
                "seq_gap_count": 0,
                "missing_seq_values": 0,
                "time_regressions": 0,
                "large_time_gaps": 0,
                "large_time_gap_threshold_ms": 0,
                "session_hash_changes": 0,
                "session_hashes": "",
                "issue": row["issue"],
            }
        )

    totals = _aggregate_quality([row for row in quality_rows if row.get("records_seen", 0) > 0])
    if not valid_records:
        _write_csv(output_dir / "data_quality.csv", quality_rows)
        if malformed_rows or unsupported:
            _write_csv(output_dir / "malformed_records.csv", [*malformed_rows, *unsupported])
        raise ValueError("no valid frames found for the selected decoder profile")

    byte_stats_rows: list[dict[str, Any]] = []
    all_stats = compute_byte_statistics(valid_records, session="all")
    byte_stats_rows.extend(row.to_dict() for row in all_stats)
    records_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in valid_records:
        records_by_file[record["source_file"]].append(record)
    for source_file, records in records_by_file.items():
        byte_stats_rows.extend(
            row.to_dict()
            for row in compute_byte_statistics(records, session=Path(source_file).name)
        )

    matrix = correlation_matrix(valid_records)
    formula_matches = [
        match.to_dict()
        for match in infer_formula_matches(valid_records)
    ] if compare_existing_fields else []
    mapping_rows = _mapping_rows(formula_matches)
    injector_rows = injector_scale_summary(valid_records)
    plausibility_rows = [issue.to_dict() for issue in evaluate_plausibility(valid_records)]
    aggregate_quality = _aggregate_quality([row for row in quality_rows if row.get("records_seen", 0) > 0])
    readiness = readiness_gate(
        quality_summary=aggregate_quality,
        formula_matches=formula_matches,
        profile_id=profile_id,
    )

    safe_ml_candidate_fields = [
        "rpm",
        "tps_voltage_candidate",
        "iat_c_candidate",
        "battery_v_candidate",
    ]
    unresolved_fields = [
        "ect",
        "map_scale",
        "injector_ms_scale",
        "ignition_raw_candidate",
        "speed_or_signal_raw_candidate",
        "bytes_19_22",
        "vehicle_scope_metadata",
    ]
    summary = {
        "profile_id": profile_id,
        "input_files": [str(path) for path in input_files],
        "unsupported_inputs": unsupported,
        "totals": aggregate_quality,
        "sessions": quality_rows,
        "existing_decoder_mappings": mapping_rows,
        "candidate_corrected_mappings": _candidate_mapping_summary(),
        "safe_ml_candidate_fields": safe_ml_candidate_fields,
        "unresolved_fields": unresolved_fields,
        "plausibility_issues": plausibility_rows,
        "readiness": readiness,
    }

    profile = _profile_with_evidence(
        profile_id=profile_id,
        input_files=input_files,
        totals=aggregate_quality,
        formula_matches=mapping_rows,
    )
    profile_dir = output_dir.parent / "profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)

    _write_csv(output_dir / "normalized_frames.csv", _normalized_frame_rows(normalized_records))
    _write_csv(output_dir / "byte_statistics.csv", byte_stats_rows)
    _write_csv(output_dir / "data_quality.csv", quality_rows)
    _write_csv(output_dir / "formula_matches.csv", mapping_rows)
    _write_csv(output_dir / "field_mapping_comparison.csv", mapping_rows)
    _write_csv(output_dir / "correlation_matrix.csv", matrix)
    _write_csv(output_dir / "injector_scale_candidates.csv", injector_rows)
    _write_csv(output_dir / "plausibility_issues.csv", plausibility_rows)
    _write_csv(
        output_dir / "change_points.csv",
        change_point_summary(valid_records, session="all", top_n=100),
    )
    _write_csv(
        output_dir / "frozen_signal_summary.csv",
        frozen_signal_summary(valid_records, session="all"),
    )
    if malformed_rows or unsupported:
        _write_csv(output_dir / "malformed_records.csv", [*malformed_rows, *unsupported])

    (output_dir / "audit_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output_dir / "audit_summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    (profile_dir / f"{profile_id}.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    _write_ground_truth_plan(output_dir / "ground_truth_test_plan.md")

    if generate_plots:
        for source_file, records in records_by_file.items():
            session_name = _safe_name(Path(source_file).name)
            for byte_index in BYTE_ATLAS_RANGE:
                write_timeseries_svg(
                    records,
                    byte_index=byte_index,
                    title=f"{Path(source_file).name} byte {byte_index}",
                    output_path=plots_dir / f"{session_name}_b{byte_index:02d}.svg",
                )
        write_correlation_heatmap_svg(
            matrix,
            output_path=plots_dir / "byte_correlation_matrix.svg",
            title="Byte-to-byte correlation matrix",
        )

    if include_calibration:
        if input_dir is None:
            raise ValueError("--include-calibration requires --input-dir so sessions can be discovered recursively")
        summary["calibration"] = run_calibration_audit(
            input_dir=input_dir,
            profile_id=profile_id,
            output_dir=output_dir,
            strict_checksum=strict_checksum,
            allow_legacy_29_byte=allow_legacy_29_byte,
            generate_plots=generate_plots,
            max_records=max_records,
        )

    return summary
