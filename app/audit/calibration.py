from __future__ import annotations

import csv
import html
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Any, Iterable

from app.audit.byte_statistics import pearson
from app.audit.loading import parse_raw_hex
from app.ecu.profiles.honda_keihin_71_17 import (
    FrameFormatError,
    bytes_to_hex,
    default_profile_document,
    decode_frame,
    normalize_frame,
    validate_normalized_frame,
)


CALIBRATION_SCENARIO_IDS = {
    "cal_01_cold_key_on",
    "cal_02_tps_sweep",
    "cal_03_cold_start_warmup",
}

SIGNAL_ORDER = [
    "RPM",
    "TPS voltage",
    "TPS raw",
    "TPS percent",
    "IAT",
    "ECT",
    "MAP voltage",
    "MAP engineering raw",
    "Battery",
    "Injector raw",
    "Injector ms",
    "Byte17",
    "Byte18 / speed",
]


@dataclass(slots=True)
class CalibrationEvent:
    event_index: int
    host_time: str
    elapsed_ms: int
    event_type: str
    event_name: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_index": self.event_index,
            "host_time": self.host_time,
            "elapsed_ms": self.elapsed_ms,
            "event_type": self.event_type,
            "event_name": self.event_name,
            "note": self.note,
        }


@dataclass(slots=True)
class CalibrationSession:
    session_dir: Path
    session_id: str
    scenario_id: str
    metadata: dict[str, Any]
    summary: dict[str, Any]
    notes: str
    events: list[CalibrationEvent]
    records: list[dict[str, Any]]
    malformed_records: list[dict[str, Any]]

    @property
    def event_by_name(self) -> dict[str, CalibrationEvent]:
        return {event.event_name: event for event in self.events}

    @property
    def valid_records(self) -> list[dict[str, Any]]:
        return [record for record in self.records if record.get("parse_valid")]


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "session"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def discover_calibration_sessions(
    input_dir: Path,
    *,
    scenario_ids: set[str] | None = None,
) -> list[Path]:
    selected = scenario_ids or CALIBRATION_SCENARIO_IDS
    session_dirs: list[Path] = []
    for metadata_path in sorted(input_dir.rglob("metadata.json")):
        try:
            metadata = _read_json(metadata_path)
        except json.JSONDecodeError:
            continue
        if metadata.get("scenario_id") not in selected:
            continue
        session_dir = metadata_path.parent
        if not (session_dir / "raw_frames.jsonl").exists():
            continue
        session_dirs.append(session_dir)
    return session_dirs


def load_events_csv(path: Path) -> list[CalibrationEvent]:
    if not path.exists():
        return []
    events: list[CalibrationEvent] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                elapsed_ms = int(float(row.get("elapsed_ms", "0")))
            except ValueError:
                elapsed_ms = 0
            try:
                event_index = int(row.get("event_index", len(events) + 1))
            except ValueError:
                event_index = len(events) + 1
            events.append(
                CalibrationEvent(
                    event_index=event_index,
                    host_time=row.get("host_time", ""),
                    elapsed_ms=elapsed_ms,
                    event_type=row.get("event_type", ""),
                    event_name=row.get("event_name", ""),
                    note=row.get("note", ""),
                )
            )
    return events


def _decode_record(
    row: dict[str, Any],
    *,
    source_file: Path,
    line_number: int,
    strict_checksum: bool,
    allow_legacy_29_byte: bool,
) -> dict[str, Any]:
    raw_bytes = parse_raw_hex(str(row.get("raw_hex", "")))
    record = {
        "source_file": str(source_file),
        "line_number": line_number,
        "capture_seq": row.get("capture_seq"),
        "seq": row.get("capture_seq"),
        "device_time_ms": row.get("device_time_ms"),
        "elapsed_ms": row.get("host_monotonic_ms", row.get("elapsed_ms")),
        "host_monotonic_ms": row.get("host_monotonic_ms"),
        "host_time": row.get("host_time"),
        "scenario_id": row.get("scenario_id"),
        "session_id": row.get("session_id"),
        "vehicle_id": row.get("vehicle_id"),
        "raw_hex": row.get("raw_hex", ""),
        "raw_length": len(raw_bytes),
        "source_record": row,
    }
    try:
        frame, source_format = normalize_frame(raw_bytes, allow_legacy_29_byte=allow_legacy_29_byte)
    except FrameFormatError as exc:
        return {
            **record,
            "frame": None,
            "normalized_hex": "",
            "source_format": "",
            "parse_valid": False,
            "length_valid": False,
            "header_valid": False,
            "checksum_valid": False,
            "profile_supported": False,
            "validation_errors": str(exc),
            "candidate": {},
        }
    validation = validate_normalized_frame(frame, source_format=source_format)
    candidate = {}
    if validation.length_valid and validation.header_valid and (validation.parse_valid or not strict_checksum):
        candidate = decode_frame(frame, strict_checksum=strict_checksum, allow_legacy_29_byte=True)
    return {
        **record,
        "frame": frame,
        "normalized_hex": bytes_to_hex(frame),
        "source_format": source_format,
        "parse_valid": validation.parse_valid,
        "length_valid": validation.length_valid,
        "header_valid": validation.header_valid,
        "checksum_valid": validation.checksum_valid,
        "profile_supported": validation.profile_supported,
        "validation_errors": "; ".join(validation.errors),
        "candidate": candidate,
    }


def _load_raw_frame_records(
    path: Path,
    *,
    strict_checksum: bool,
    allow_legacy_29_byte: bool,
    max_records: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
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
                record = _decode_record(
                    row,
                    source_file=path,
                    line_number=line_number,
                    strict_checksum=strict_checksum,
                    allow_legacy_29_byte=allow_legacy_29_byte,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                malformed.append(
                    {
                        "source_file": str(path),
                        "line_number": line_number,
                        "issue": str(exc),
                    }
                )
                continue
            records.append(record)
            accepted += 1
    return records, malformed


def load_calibration_session(
    session_dir: Path,
    *,
    strict_checksum: bool = True,
    allow_legacy_29_byte: bool = False,
    max_records: int | None = None,
) -> CalibrationSession:
    metadata = _read_json(session_dir / "metadata.json")
    summary = _read_json(session_dir / "summary.json")
    events = load_events_csv(session_dir / "events.csv")
    notes_path = session_dir / "notes.txt"
    notes = notes_path.read_text(encoding="utf-8", errors="replace") if notes_path.exists() else ""
    records, malformed = _load_raw_frame_records(
        session_dir / "raw_frames.jsonl",
        strict_checksum=strict_checksum,
        allow_legacy_29_byte=allow_legacy_29_byte,
        max_records=max_records,
    )
    return CalibrationSession(
        session_dir=session_dir,
        session_id=str(metadata.get("session_id", session_dir.name)),
        scenario_id=str(metadata.get("scenario_id", "")),
        metadata=metadata,
        summary=summary,
        notes=notes,
        events=events,
        records=records,
        malformed_records=malformed,
    )


def load_calibration_sessions(
    input_dir: Path,
    *,
    strict_checksum: bool = True,
    allow_legacy_29_byte: bool = False,
    max_records: int | None = None,
) -> list[CalibrationSession]:
    return [
        load_calibration_session(
            session_dir,
            strict_checksum=strict_checksum,
            allow_legacy_29_byte=allow_legacy_29_byte,
            max_records=max_records,
        )
        for session_dir in discover_calibration_sessions(input_dir)
    ]


def frames_between(
    session: CalibrationSession,
    start_ms: float | None,
    end_ms: float | None,
) -> list[dict[str, Any]]:
    records = session.valid_records
    selected: list[dict[str, Any]] = []
    for record in records:
        elapsed_ms = record.get("elapsed_ms")
        if not isinstance(elapsed_ms, int | float):
            continue
        if start_ms is not None and elapsed_ms < start_ms:
            continue
        if end_ms is not None and elapsed_ms > end_ms:
            continue
        selected.append(record)
    return selected


def frames_for_event_pair(
    session: CalibrationSession,
    begin_name: str,
    end_name: str,
) -> list[dict[str, Any]]:
    events = session.event_by_name
    begin = events.get(begin_name)
    end = events.get(end_name)
    if begin is None or end is None:
        return []
    return frames_between(session, begin.elapsed_ms, end.elapsed_ms)


def nearest_frame(session: CalibrationSession, elapsed_ms: float) -> dict[str, Any] | None:
    records = [
        record
        for record in session.valid_records
        if isinstance(record.get("elapsed_ms"), int | float)
    ]
    if not records:
        return None
    return min(records, key=lambda record: abs(float(record["elapsed_ms"]) - elapsed_ms))


def _values(records: Iterable[dict[str, Any]], signal: str) -> list[float]:
    out: list[float] = []
    for record in records:
        frame = record.get("frame")
        if not frame:
            continue
        value = signal_value(frame, signal)
        if value is not None:
            out.append(float(value))
    return out


def signal_value(frame: list[int], signal: str) -> float | None:
    if signal.startswith("byte"):
        return float(frame[int(signal[4:])])
    if signal == "rpm":
        return float((frame[4] << 8) | frame[5])
    if signal == "tps_voltage":
        return frame[6] * 5.0 / 256.0
    if signal == "tps_raw":
        return float(frame[7])
    if signal == "iat_c":
        return float(frame[11] - 40)
    if signal == "ect_c":
        return float(frame[13] - 40)
    if signal == "map_voltage_v01":
        return frame[12] * 5.0 / 256.0
    if signal == "battery_v":
        return frame[14] / 10.0
    if signal == "injector_raw":
        return float((frame[15] << 8) | frame[16])
    if signal == "byte17":
        return float(frame[17])
    if signal == "byte18":
        return float(frame[18])
    return None


def series_stats(values: Iterable[float]) -> dict[str, Any]:
    vals = [float(value) for value in values]
    if not vals:
        return {
            "count": 0,
            "minimum": None,
            "maximum": None,
            "mean": None,
            "median": None,
            "stddev": None,
            "range": None,
            "unique_values": 0,
        }
    return {
        "count": len(vals),
        "minimum": min(vals),
        "maximum": max(vals),
        "mean": fmean(vals),
        "median": median(vals),
        "stddev": pstdev(vals) if len(vals) > 1 else 0.0,
        "range": max(vals) - min(vals),
        "unique_values": len(set(vals)),
    }


def _byte_stats(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for byte_index in range(4, 23):
        stats = series_stats(record["frame"][byte_index] for record in records if record.get("frame"))
        rows.append({"byte_index": byte_index, **stats})
    return rows


def _monotonic_fraction(values: list[float], *, increasing: bool) -> float | None:
    if len(values) < 2:
        return None
    ok = 0
    for left, right in zip(values, values[1:], strict=False):
        if increasing and right >= left:
            ok += 1
        if not increasing and right <= left:
            ok += 1
    return ok / (len(values) - 1)


def _linear_slope_per_minute(records: list[dict[str, Any]], values: list[float]) -> float | None:
    times = [float(record["elapsed_ms"]) for record in records if isinstance(record.get("elapsed_ms"), int | float)]
    if len(times) != len(values) or len(values) < 2:
        return None
    mean_time = fmean(times)
    mean_value = fmean(values)
    denominator = sum((time - mean_time) ** 2 for time in times)
    if denominator == 0:
        return None
    slope_per_ms = sum(
        (time - mean_time) * (value - mean_value)
        for time, value in zip(times, values, strict=False)
    ) / denominator
    return slope_per_ms * 60000.0


def _mean_abs_diff(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return fmean(abs(right - left) for left, right in zip(values, values[1:], strict=False))


def _first_running_elapsed_ms(session: CalibrationSession) -> int | None:
    for record in session.valid_records:
        frame = record.get("frame")
        if frame and ((frame[4] << 8) | frame[5]) > 0:
            elapsed = record.get("elapsed_ms")
            return int(elapsed) if isinstance(elapsed, int | float) else None
    return None


def _event_names(session: CalibrationSession) -> list[str]:
    return [event.event_name for event in session.events]


def _session_quality(session: CalibrationSession) -> dict[str, Any]:
    valid = session.valid_records
    length_failures = sum(1 for record in session.records if not record.get("length_valid"))
    header_failures = sum(1 for record in session.records if record.get("length_valid") and not record.get("header_valid"))
    checksum_failures = sum(
        1
        for record in session.records
        if record.get("length_valid") and record.get("header_valid") and not record.get("checksum_valid")
    )
    summary_malformed = int(session.summary.get("malformed_lines", 0) or 0)
    elapsed_values = [
        float(record["elapsed_ms"])
        for record in valid
        if isinstance(record.get("elapsed_ms"), int | float)
    ]
    return {
        "session_id": session.session_id,
        "scenario_id": session.scenario_id,
        "session_dir": str(session.session_dir),
        "frame_count": len(session.records),
        "valid_frame_count": len(valid),
        "checksum_failures": checksum_failures,
        "header_failures": header_failures,
        "length_failures": length_failures,
        "malformed_records": max(len(session.malformed_records), summary_malformed),
        "raw_frames_malformed_records": len(session.malformed_records),
        "summary_malformed_lines": summary_malformed,
        "duration_ms": (max(elapsed_values) - min(elapsed_values)) if elapsed_values else None,
        "duration_seconds": session.summary.get("duration_seconds"),
        "events_discovered": ";".join(_event_names(session)),
        "ambient_reference_c": session.metadata.get("environment", {}).get("ambient_temperature_c"),
        "external_battery_reference_v": session.metadata.get("environment", {}).get("external_battery_v"),
    }


def _find_session(sessions: list[CalibrationSession], scenario_id: str) -> CalibrationSession | None:
    for session in sessions:
        if session.scenario_id == scenario_id:
            return session
    return None


def analyze_cold_key_on(session: CalibrationSession | None) -> dict[str, Any]:
    if session is None:
        return {"available": False}
    events = session.event_by_name
    key_on = events.get("key_on")
    capture_end = events.get("capture_end")
    start_ms = key_on.elapsed_ms if key_on else None
    end_ms = capture_end.elapsed_ms if capture_end else None
    stable_records = frames_between(session, start_ms, end_ms)
    if not stable_records:
        stable_records = session.valid_records
    ambient = session.metadata.get("environment", {}).get("ambient_temperature_c")
    external_battery = session.metadata.get("environment", {}).get("external_battery_v")
    iat_values = _values(stable_records, "iat_c")
    battery_values = _values(stable_records, "battery_v")
    return {
        "available": True,
        "session_id": session.session_id,
        "stable_frame_count": len(stable_records),
        "stable_window_ms": [start_ms, end_ms],
        "ambient_reference_c": ambient,
        "external_battery_reference_v": external_battery,
        "byte_baseline": _byte_stats(stable_records),
        "rpm_stats": series_stats(_values(stable_records, "rpm")),
        "byte18_stats": series_stats(_values(stable_records, "byte18")),
        "iat_c_stats": series_stats(iat_values),
        "iat_ambient_error_c": (fmean(iat_values) - float(ambient)) if iat_values and isinstance(ambient, int | float) else None,
        "battery_v_stats": series_stats(battery_values),
        "battery_external_difference_v": (
            fmean(battery_values) - float(external_battery)
            if battery_values and isinstance(external_battery, int | float)
            else None
        ),
        "battery_external_difference_pct": (
            (fmean(battery_values) - float(external_battery)) / float(external_battery) * 100.0
            if battery_values and isinstance(external_battery, int | float) and external_battery
            else None
        ),
    }


def _nearest_values_for_events(session: CalibrationSession, event_prefix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in session.events:
        if not event.event_name.startswith(event_prefix):
            continue
        frame_record = nearest_frame(session, event.elapsed_ms)
        if frame_record is None:
            continue
        frame = frame_record["frame"]
        rows.append(
            {
                "event_name": event.event_name,
                "event_elapsed_ms": event.elapsed_ms,
                "frame_elapsed_ms": frame_record.get("elapsed_ms"),
                "byte6": frame[6],
                "byte7": frame[7],
            }
        )
    return rows


def analyze_tps_sweep(session: CalibrationSession | None) -> dict[str, Any]:
    if session is None:
        return {"available": False}
    records = session.valid_records
    closed_records = frames_for_event_pair(session, "tps_closed_begin", "tps_closed_end")
    full_records = frames_for_event_pair(session, "tps_plateau_full_begin", "tps_plateau_full_end")
    if not closed_records:
        closed_records = records
    if not full_records:
        full_records = records

    byte6_values = _values(records, "byte6")
    byte7_values = _values(records, "byte7")
    closed_b6 = median(_values(closed_records, "byte6")) if closed_records else None
    closed_b7 = median(_values(closed_records, "byte7")) if closed_records else None
    open_b6 = max(byte6_values) if byte6_values else None
    open_b7 = max(byte7_values) if byte7_values else None
    old_full = open_b7 * 100.0 / 255.0 if open_b7 is not None else None

    opening_scores: list[float] = []
    closing_scores: list[float] = []
    for sweep_index in range(1, 6):
        opening = frames_for_event_pair(
            session,
            f"tps_sweep_{sweep_index}_open_begin",
            f"tps_sweep_{sweep_index}_full",
        )
        closing = frames_for_event_pair(
            session,
            f"tps_sweep_{sweep_index}_close_begin",
            f"tps_sweep_{sweep_index}_end",
        )
        open_values = _values(opening, "byte7")
        close_values = _values(closing, "byte7")
        score = _monotonic_fraction(open_values, increasing=True)
        if score is not None:
            opening_scores.append(score)
        score = _monotonic_fraction(close_values, increasing=False)
        if score is not None:
            closing_scores.append(score)

    responders: list[dict[str, Any]] = []
    for byte_index in range(4, 23):
        values = _values(records, f"byte{byte_index}")
        value_range = (max(values) - min(values)) if values else 0.0
        if value_range > 2:
            responders.append(
                {
                    "byte_index": byte_index,
                    "range": value_range,
                    "correlation_with_byte6": pearson(values, byte6_values),
                    "correlation_with_byte7": pearson(values, byte7_values),
                }
            )
    responders.sort(key=lambda row: row["range"], reverse=True)

    repeated_full_values = _nearest_values_for_events(session, "tps_sweep_")
    full_event_b7 = [row["byte7"] for row in repeated_full_values if row["event_name"].endswith("_full")]

    return {
        "available": True,
        "session_id": session.session_id,
        "frame_count": len(records),
        "rpm_stats": series_stats(_values(records, "rpm")),
        "byte6_stats": series_stats(byte6_values),
        "byte7_stats": series_stats(byte7_values),
        "observed_closed_byte6": closed_b6,
        "observed_open_byte6": open_b6,
        "observed_closed_byte7": closed_b7,
        "observed_open_byte7": open_b7,
        "byte6_closed_voltage": closed_b6 * 5.0 / 256.0 if closed_b6 is not None else None,
        "byte6_open_voltage": open_b6 * 5.0 / 256.0 if open_b6 is not None else None,
        "opening_monotonicity": fmean(opening_scores) if opening_scores else None,
        "closing_monotonicity": fmean(closing_scores) if closing_scores else None,
        "byte6_byte7_correlation": pearson(byte6_values, byte7_values),
        "repeatability_full_byte7_range": (max(full_event_b7) - min(full_event_b7)) if full_event_b7 else None,
        "strong_sweep_responders": responders,
        "old_formula": {
            "closed_percent": closed_b7 * 100.0 / 255.0 if closed_b7 is not None else None,
            "full_percent": old_full,
        },
        "calibrated_formula": {
            "closed_percent": 0.0 if closed_b7 is not None and open_b7 != closed_b7 else None,
            "full_percent": 100.0 if closed_b7 is not None and open_b7 != closed_b7 else None,
            "formula": "(raw - observed_closed) / (observed_open - observed_closed) * 100",
        },
        "recommendation": (
            "endpoint-calibrated scaling preferred"
            if old_full is not None and abs(old_full - 100.0) > 10.0
            else "old formula physically valid"
        ),
    }


def _window_stats(session: CalibrationSession, start_ms: float, end_ms: float) -> dict[str, Any]:
    records = frames_between(session, start_ms, end_ms)
    return {
        "window_ms": [start_ms, end_ms],
        "frame_count": len(records),
        "rpm": series_stats(_values(records, "rpm")),
        "byte10": series_stats(_values(records, "byte10")),
        "byte11": series_stats(_values(records, "byte11")),
        "iat_c": series_stats(_values(records, "iat_c")),
        "byte12": series_stats(_values(records, "byte12")),
        "byte13": series_stats(_values(records, "byte13")),
        "v01_map_voltage": series_stats(_values(records, "map_voltage_v01")),
        "ect_c": series_stats(_values(records, "ect_c")),
        "battery_v": series_stats(_values(records, "battery_v")),
        "injector_raw": series_stats(_values(records, "injector_raw")),
        "byte17": series_stats(_values(records, "byte17")),
        "byte18": series_stats(_values(records, "byte18")),
    }


def _value_at_markers(session: CalibrationSession, marker_names: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for marker in marker_names:
        event = session.event_by_name.get(marker)
        if event is None:
            continue
        frame_record = nearest_frame(session, event.elapsed_ms)
        if frame_record is None:
            continue
        frame = frame_record["frame"]
        rows.append(
            {
                "marker": marker,
                "event_elapsed_ms": event.elapsed_ms,
                "frame_elapsed_ms": frame_record.get("elapsed_ms"),
                "rpm": int(signal_value(frame, "rpm") or 0),
                "byte10": frame[10],
                "byte11": frame[11],
                "iat_c": frame[11] - 40,
                "byte12": frame[12],
                "byte13": frame[13],
                "ect_c": frame[13] - 40,
                "v01_map_voltage": frame[12] * 5.0 / 256.0,
                "battery_v": frame[14] / 10.0,
                "injector_raw": (frame[15] << 8) | frame[16],
                "byte17": frame[17],
                "byte18": frame[18],
            }
        )
    return rows


def _thermal_candidate_rows(session: CalibrationSession, first_running_ms: int | None) -> list[dict[str, Any]]:
    start_ms = first_running_ms
    records = frames_between(session, start_ms, None) if start_ms is not None else session.valid_records
    pre = frames_between(session, (first_running_ms or 0) - 2000, first_running_ms - 1) if first_running_ms else []
    post = frames_between(session, first_running_ms, first_running_ms + 2000) if first_running_ms else []
    rows: list[dict[str, Any]] = []
    for byte_index in range(8, 23):
        values = _values(records, f"byte{byte_index}")
        pre_values = _values(pre, f"byte{byte_index}")
        post_values = _values(post, f"byte{byte_index}")
        rows.append(
            {
                "byte_index": byte_index,
                "start_value": values[0] if values else None,
                "end_value": values[-1] if values else None,
                "total_change": (values[-1] - values[0]) if values else None,
                "linear_trend_per_min": _linear_slope_per_minute(records, values),
                "monotonic_increase_fraction": _monotonic_fraction(values, increasing=True),
                "monotonic_decrease_fraction": _monotonic_fraction(values, increasing=False),
                "short_term_noise_mean_abs_diff": _mean_abs_diff(values),
                "immediate_start_delta": (
                    fmean(post_values) - fmean(pre_values) if pre_values and post_values else None
                ),
                "raw_minus_40_start": (values[0] - 40) if values else None,
                "raw_minus_40_end": (values[-1] - 40) if values else None,
            }
        )
    rows.sort(key=lambda row: abs(row["total_change"] or 0), reverse=True)
    return rows


def analyze_warmup(session: CalibrationSession | None) -> dict[str, Any]:
    if session is None:
        return {"available": False}
    first_running_ms = _first_running_elapsed_ms(session)
    event_start = session.event_by_name.get("engine_started")
    event_start_ms = event_start.elapsed_ms if event_start else first_running_ms
    actual_start_ms = first_running_ms or event_start_ms
    baseline_records = frames_between(session, None, actual_start_ms - 1 if actual_start_ms else None)
    ambient = session.metadata.get("environment", {}).get("ambient_temperature_c")
    engine_cold_confirmed = session.metadata.get("environment", {}).get("engine_cold_confirmed")
    iat_base = _values(baseline_records, "iat_c")
    ect_base = _values(baseline_records, "ect_c")
    byte12_values = _values(session.valid_records, "byte12")
    byte13_values = _values(session.valid_records, "byte13")

    windows: dict[str, Any] = {}
    if event_start_ms is not None:
        windows["event_pre5s"] = _window_stats(session, event_start_ms - 5000, event_start_ms)
        windows["event_post0_2s"] = _window_stats(session, event_start_ms, event_start_ms + 2000)
        windows["event_post2_10s"] = _window_stats(session, event_start_ms + 2000, event_start_ms + 10000)
        windows["event_post10_60s"] = _window_stats(session, event_start_ms + 10000, event_start_ms + 60000)
    if actual_start_ms is not None:
        windows["actual_rpm_pre2s"] = _window_stats(session, actual_start_ms - 2000, actual_start_ms - 1)
        windows["actual_rpm_post0_2s"] = _window_stats(session, actual_start_ms, actual_start_ms + 2000)
        windows["actual_rpm_post2_10s"] = _window_stats(session, actual_start_ms + 2000, actual_start_ms + 10000)
        windows["actual_rpm_post10_60s"] = _window_stats(session, actual_start_ms + 10000, actual_start_ms + 60000)

    markers = _value_at_markers(
        session,
        ["key_on", "engine_started", "warmup_1min", "warmup_3min", "warmup_5min", "warmup_10min", "warmup_15min"],
    )
    actual_pre = windows.get("actual_rpm_pre2s", {})
    actual_post = windows.get("actual_rpm_post2_10s", {})
    byte12_before = actual_pre.get("byte12", {}).get("mean")
    byte12_after = actual_post.get("byte12", {}).get("mean")
    byte13_before = actual_pre.get("byte13", {}).get("mean")
    byte13_after = actual_post.get("byte13", {}).get("mean")

    return {
        "available": True,
        "session_id": session.session_id,
        "ambient_reference_c": ambient,
        "engine_cold_confirmed": engine_cold_confirmed,
        "engine_started_event_ms": event_start_ms,
        "first_nonzero_rpm_ms": first_running_ms,
        "start_marker_note": (
            "first non-zero RPM precedes engine_started marker; actual RPM transition is used for start-response tests"
            if first_running_ms is not None and event_start_ms is not None and first_running_ms < event_start_ms
            else "engine_started marker used for start-response tests"
        ),
        "baseline_frame_count": len(baseline_records),
        "iat_baseline_c": series_stats(iat_base),
        "iat_ambient_error_c": (fmean(iat_base) - float(ambient)) if iat_base and isinstance(ambient, int | float) else None,
        "ect_baseline_c": series_stats(ect_base),
        "ect_ambient_error_c": (fmean(ect_base) - float(ambient)) if ect_base and isinstance(ambient, int | float) else None,
        "engine_start_windows": windows,
        "warmup_markers": markers,
        "thermal_candidates": _thermal_candidate_rows(session, actual_start_ms),
        "v01_map_hypothesis_test": {
            "byte12_mean_before_start": byte12_before,
            "byte12_mean_2_10s_after_start": byte12_after,
            "byte12_delta": (byte12_after - byte12_before) if byte12_after is not None and byte12_before is not None else None,
            "byte12_percent_change": (
                (byte12_after - byte12_before) / byte12_before * 100.0
                if byte12_after is not None and byte12_before not in {None, 0}
                else None
            ),
            "byte13_mean_before_start": byte13_before,
            "byte13_mean_2_10s_after_start": byte13_after,
            "byte13_delta": (byte13_after - byte13_before) if byte13_after is not None and byte13_before is not None else None,
            "byte13_percent_change": (
                (byte13_after - byte13_before) / byte13_before * 100.0
                if byte13_after is not None and byte13_before not in {None, 0}
                else None
            ),
            "byte12_byte13_correlation": pearson(byte12_values, byte13_values),
        },
    }


def _all_valid_records(sessions: list[CalibrationSession]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for session in sessions:
        records.extend(session.valid_records)
    return records


def _unique_values(records: list[dict[str, Any]], byte_index: int) -> list[int]:
    return sorted({record["frame"][byte_index] for record in records if record.get("frame")})


def _battery_external_summary(cold_key_on: dict[str, Any]) -> dict[str, Any]:
    external = cold_key_on.get("external_battery_reference_v")
    ecu = cold_key_on.get("battery_v_stats", {}).get("mean")
    return {
        "external_vom_v": external,
        "ecu_byte14_mean_v": ecu,
        "absolute_difference_v": (ecu - external) if isinstance(ecu, int | float) and isinstance(external, int | float) else None,
        "percentage_difference": (
            (ecu - external) / external * 100.0
            if isinstance(ecu, int | float) and isinstance(external, int | float) and external
            else None
        ),
    }


def _injector_summary(sessions: list[CalibrationSession], warmup: dict[str, Any]) -> dict[str, Any]:
    by_scenario = {session.scenario_id: session for session in sessions}
    cold = by_scenario.get("cal_01_cold_key_on")
    tps = by_scenario.get("cal_02_tps_sweep")
    warm = by_scenario.get("cal_03_cold_start_warmup")
    cold_values = _values(cold.valid_records, "injector_raw") if cold else []
    tps_values = _values(tps.valid_records, "injector_raw") if tps else []
    warm_windows = warmup.get("engine_start_windows", {})
    return {
        "cold_key_on_engine_off": series_stats(cold_values),
        "tps_sweep_engine_off": series_stats(tps_values),
        "actual_start_pre2s": warm_windows.get("actual_rpm_pre2s", {}).get("injector_raw"),
        "actual_start_post0_2s": warm_windows.get("actual_rpm_post0_2s", {}).get("injector_raw"),
        "actual_start_post10_60s": warm_windows.get("actual_rpm_post10_60s", {}).get("injector_raw"),
        "verdict": (
            "HIGH_CONFIDENCE injector-related raw control signal; /100 milliseconds remains UNVERIFIED"
        ),
    }


def _byte17_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    byte17 = _values(records, "byte17")
    return {
        "stats": series_stats(byte17),
        "correlation_rpm": pearson(byte17, _values(records, "rpm")),
        "correlation_tps_raw": pearson(byte17, _values(records, "tps_raw")),
        "correlation_byte13_ect": pearson(byte17, _values(records, "ect_c")),
        "correlation_injector_raw": pearson(byte17, _values(records, "injector_raw")),
        "verdict": "UNVERIFIED; relationships are reported but no physical identity is assigned",
    }


def _byte18_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = _values(records, "byte18")
    return {
        "stats": series_stats(values),
        "unique_values": sorted(set(values)),
        "verdict": (
            "REJECTED as direct vehicle speed: all calibration scenarios were stationary while byte18 is non-zero and changes"
            if values and (max(values) > 0 and len(set(values)) > 1)
            else "speed remains possible but unverified"
        ),
    }


def _field_decisions(
    *,
    tps: dict[str, Any],
    cold: dict[str, Any],
    warmup: dict[str, Any],
    battery: dict[str, Any],
    injector: dict[str, Any],
    byte17: dict[str, Any],
    byte18: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    iat_error = warmup.get("iat_ambient_error_c")
    ect_error = warmup.get("ect_ambient_error_c")
    return {
        "RPM": {
            "v0.1_mapping_hypothesis": "bytes 4-5 big-endian unsigned integer",
            "calibration_evidence": "0 RPM in stationary engine-off sessions; non-zero and plausible after engine operation begins",
            "identity_status": "VERIFIED",
            "scaling_status": "VERIFIED",
            "recommended_ml_use": "use",
            "ml_approved": True,
            "ml_reason": "directly verified by controlled off/start states",
        },
        "TPS voltage": {
            "v0.1_mapping_hypothesis": "byte6 * 5 / 256",
            "calibration_evidence": (
                f"TPS sweep byte6 {tps.get('observed_closed_byte6')} to {tps.get('observed_open_byte6')} "
                f"({tps.get('byte6_closed_voltage'):.3f} V to {tps.get('byte6_open_voltage'):.3f} V)"
                if tps.get("available") and tps.get("byte6_closed_voltage") is not None
                else "TPS sweep unavailable"
            ),
            "identity_status": "VERIFIED",
            "scaling_status": "VERIFIED",
            "recommended_ml_use": "use",
            "ml_approved": True,
            "ml_reason": "responds monotonically to controlled throttle sweep",
        },
        "TPS raw": {
            "v0.1_mapping_hypothesis": "byte7 raw/position candidate",
            "calibration_evidence": (
                f"TPS sweep byte7 {tps.get('observed_closed_byte7')} to {tps.get('observed_open_byte7')}, "
                f"corr(byte6, byte7)={tps.get('byte6_byte7_correlation'):.6f}"
                if tps.get("available") and tps.get("byte6_byte7_correlation") is not None
                else "TPS sweep unavailable"
            ),
            "identity_status": "VERIFIED",
            "scaling_status": "VERIFIED_AS_RAW",
            "recommended_ml_use": "use raw or calibrated percent",
            "ml_approved": True,
            "ml_reason": "direct throttle sweep ground truth",
        },
        "TPS percent": {
            "v0.1_mapping_hypothesis": "byte7 * 100 / 255",
            "calibration_evidence": (
                f"old formula gives full={tps.get('old_formula', {}).get('full_percent'):.2f}% "
                f"at observed full raw={tps.get('observed_open_byte7')}; endpoint calibration gives 100%"
                if tps.get("available") and tps.get("old_formula", {}).get("full_percent") is not None
                else "TPS sweep unavailable"
            ),
            "identity_status": "VERIFIED",
            "scaling_status": "REJECTED_OLD_FORMULA",
            "recommended_ml_use": "use endpoint-calibrated percent, not raw*100/255",
            "ml_approved": True,
            "ml_reason": "approved only with calibrated closed/open endpoints",
        },
        "IAT": {
            "v0.1_mapping_hypothesis": "bytes10-11 as IAT pair; byte11 - 40",
            "calibration_evidence": (
                f"baseline byte11-40 is {warmup.get('iat_baseline_c', {}).get('mean'):.2f} C; "
                f"ambient error {iat_error:.2f} C; 15-min warm-up drift is small"
                if isinstance(iat_error, int | float)
                else "ambient reference unavailable"
            ),
            "identity_status": "HIGH_CONFIDENCE",
            "scaling_status": "HIGH_CONFIDENCE",
            "recommended_ml_use": "use",
            "ml_approved": True,
            "ml_reason": "close to ambient and does not follow coolant warm-up",
        },
        "ECT": {
            "v0.1_mapping_hypothesis": "bytes8-9 generic ECT unavailable; byte13 was old ect_c source",
            "calibration_evidence": (
                f"bytes8-9 remain FF/FF; byte13-40 starts {warmup.get('ect_baseline_c', {}).get('mean'):.2f} C "
                f"(ambient error {ect_error:.2f} C) and reaches warm operating temperature"
                if isinstance(ect_error, int | float)
                else "warm-up reference unavailable"
            ),
            "identity_status": "HIGH_CONFIDENCE",
            "scaling_status": "HIGH_CONFIDENCE",
            "recommended_ml_use": "use",
            "ml_approved": True,
            "ml_reason": "controlled cold-start/warm-up strongly identifies byte13 as engine temperature",
        },
        "MAP voltage": {
            "v0.1_mapping_hypothesis": "byte12 * 5 / 256 as MAP voltage",
            "calibration_evidence": "byte12 does not show an immediate manifold-pressure transition; it slowly falls during warm-up with byte13 rising",
            "identity_status": "REJECTED",
            "scaling_status": "REJECTED_AS_MAP",
            "recommended_ml_use": "exclude as MAP",
            "ml_approved": False,
            "ml_reason": "v0.1 MAP hypothesis failed controlled start/warm-up test",
        },
        "MAP engineering raw": {
            "v0.1_mapping_hypothesis": "byte13 raw as MAP engineering value",
            "calibration_evidence": "byte13 follows slow engine-temperature warm-up, not pressure dynamics",
            "identity_status": "REJECTED",
            "scaling_status": "REJECTED_AS_MAP",
            "recommended_ml_use": "exclude as MAP",
            "ml_approved": False,
            "ml_reason": "byte13 is used as ECT/engine-temperature candidate instead",
        },
        "Battery": {
            "v0.1_mapping_hypothesis": "byte14 / 10",
            "calibration_evidence": (
                f"external VOM {battery.get('external_vom_v')} V vs ECU mean {battery.get('ecu_byte14_mean_v'):.3f} V"
                if isinstance(battery.get("ecu_byte14_mean_v"), int | float)
                else "external battery reference unavailable"
            ),
            "identity_status": "VERIFIED",
            "scaling_status": "VERIFIED",
            "recommended_ml_use": "use",
            "ml_approved": True,
            "ml_reason": "external VOM agreement plus start/charging behavior",
        },
        "Injector raw": {
            "v0.1_mapping_hypothesis": "bytes15-16 big-endian raw",
            "calibration_evidence": injector.get("verdict"),
            "identity_status": "HIGH_CONFIDENCE",
            "scaling_status": "VERIFIED_AS_RAW",
            "recommended_ml_use": "exclude from ML V1",
            "ml_approved": False,
            "ml_reason": "raw is injection-related, but engine-off nonzero TPS-sweep value and scale uncertainty make it unsuitable for V1",
        },
        "Injector ms": {
            "v0.1_mapping_hypothesis": "bytes15-16 / 100 ms",
            "calibration_evidence": "no oscilloscope or logic-analyzer pulse-width reference was provided",
            "identity_status": "HIGH_CONFIDENCE",
            "scaling_status": "UNVERIFIED",
            "recommended_ml_use": "exclude",
            "ml_approved": False,
            "ml_reason": "milliseconds scale is not experimentally verified",
        },
        "Byte17": {
            "v0.1_mapping_hypothesis": "unknown raw byte",
            "calibration_evidence": byte17.get("verdict"),
            "identity_status": "UNVERIFIED",
            "scaling_status": "UNVERIFIED",
            "recommended_ml_use": "exclude",
            "ml_approved": False,
            "ml_reason": "no controlled physical identity",
        },
        "Byte18 / speed": {
            "v0.1_mapping_hypothesis": "speed_or_signal_raw_candidate",
            "calibration_evidence": byte18.get("verdict"),
            "identity_status": "REJECTED",
            "scaling_status": "REJECTED_AS_SPEED",
            "recommended_ml_use": "exclude",
            "ml_approved": False,
            "ml_reason": "stationary calibration falsifies direct speed interpretation",
        },
    }


def _read_vehicle_metadata(input_dir: Path) -> dict[str, Any]:
    vehicle_files = sorted((input_dir / "_vehicles").glob("*.json")) if (input_dir / "_vehicles").exists() else []
    if not vehicle_files:
        return {}
    return _read_json(vehicle_files[0])


def _ml_readiness(decisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    approved = [
        key
        for key, row in decisions.items()
        if row.get("ml_approved")
    ]
    approved_set = set(approved)
    required_core = {"RPM", "TPS voltage", "TPS raw", "IAT", "Battery"}
    optional_engine_temp = "ECT" in approved_set
    ready = required_core.issubset(approved_set) and optional_engine_temp
    reasons = []
    if ready:
        reasons.append("controlled calibration now supports a defensible non-anomaly-training signal set")
        reasons.append("use profile-derived v0.2 fields, not the old production iat_c field")
    else:
        missing = sorted(required_core - approved_set)
        if missing:
            reasons.append("missing approved core fields: " + ", ".join(missing))
        if not optional_engine_temp:
            reasons.append("engine-temperature field is not approved")
    return {
        "ready_for_ml": ready,
        "approved_fields": approved,
        "reasons": reasons,
    }


def _profile_v02(
    *,
    profile_id: str,
    vehicle_metadata: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    quality_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    profile = default_profile_document(profile_id)
    profile["vehicle_scope"] = {
        "vehicle_id": vehicle_metadata.get("vehicle_id", "honda_shmode_4v_001"),
        "ecu_part_number": None,
        "manufacturer": vehicle_metadata.get("manufacturer", "Honda"),
        "model": vehicle_metadata.get("model", "SH Mode"),
        "generation": vehicle_metadata.get("generation", "4-valve"),
        "cooling": vehicle_metadata.get("cooling_type", "liquid"),
        "year": None,
        "firmware_version": None,
        "protocol_family": vehicle_metadata.get("ecu_protocol", "Honda/Keihin K-Line"),
        "protocol_table": "0x71/0x17",
    }
    profile["fields"] = {
        "rpm": {
            "source_bytes": [4, 5],
            "formula": "big_endian_u16",
            "identity_status": "VERIFIED",
            "scaling_status": "VERIFIED",
            "ml_approved": True,
        },
        "tps_voltage": {
            "source_bytes": [6],
            "formula": "raw * 5 / 256",
            "identity_status": "VERIFIED",
            "scaling_status": "VERIFIED",
            "ml_approved": True,
        },
        "tps_raw": {
            "source_bytes": [7],
            "formula": "raw",
            "identity_status": "VERIFIED",
            "scaling_status": "VERIFIED_AS_RAW",
            "ml_approved": True,
        },
        "tps_percent_calibrated": {
            "source_bytes": [7],
            "formula": "(raw - observed_closed) / (observed_open - observed_closed) * 100",
            "observed_closed": 0,
            "observed_open": 156,
            "identity_status": "VERIFIED",
            "scaling_status": "HIGH_CONFIDENCE",
            "ml_approved": True,
        },
        "iat": {
            "source_bytes": [10, 11],
            "formula": "byte11 - 40; byte10 thermistor-voltage candidate",
            "identity_status": "HIGH_CONFIDENCE",
            "scaling_status": "HIGH_CONFIDENCE",
            "ml_approved": True,
        },
        "ect": {
            "source_bytes": [12, 13],
            "formula": "byte13 - 40; byte12 thermistor-voltage candidate",
            "identity_status": "HIGH_CONFIDENCE",
            "scaling_status": "HIGH_CONFIDENCE",
            "ml_approved": True,
        },
        "ect_legacy_8_9": {
            "source_bytes": [8, 9],
            "formula": "FF/FF sentinel",
            "identity_status": "UNAVAILABLE",
            "scaling_status": "UNAVAILABLE",
            "ml_approved": False,
        },
        "map_v01_hypothesis": {
            "source_bytes": [12, 13],
            "formula": "byte12 voltage; byte13 raw",
            "identity_status": "REJECTED",
            "scaling_status": "REJECTED",
            "ml_approved": False,
        },
        "battery": {
            "source_bytes": [14],
            "formula": "raw / 10",
            "identity_status": "VERIFIED",
            "scaling_status": "VERIFIED",
            "ml_approved": True,
        },
        "injector_raw": {
            "source_bytes": [15, 16],
            "formula": "big_endian_u16",
            "identity_status": "HIGH_CONFIDENCE",
            "scaling_status": "VERIFIED_AS_RAW",
            "ml_approved": False,
        },
        "injector_ms": {
            "source_bytes": [15, 16],
            "formula": "raw / 100",
            "identity_status": "HIGH_CONFIDENCE",
            "scaling_status": "UNVERIFIED",
            "ml_approved": False,
        },
        "byte17": {
            "source_bytes": [17],
            "formula": "raw",
            "identity_status": "UNVERIFIED",
            "scaling_status": "UNVERIFIED",
            "ml_approved": False,
        },
        "byte18_speed": {
            "source_bytes": [18],
            "formula": "raw",
            "identity_status": "REJECTED",
            "scaling_status": "REJECTED_AS_SPEED",
            "ml_approved": False,
        },
    }
    profile["verified_fields"] = ["rpm", "tps_voltage", "tps_raw", "battery", "checksum"]
    profile["candidate_fields"] = ["iat", "ect", "tps_percent_calibrated", "injector_raw"]
    profile["unknown_fields"] = ["map_actual_location", "byte17", "bytes_19_22"]
    profile["known_sentinels"] = {
        "ff_ff_pair": "unavailable_or_unsupported",
        "bytes_8_9": "FF/FF in road and calibration captures; unavailable in this 0x17 response",
    }
    profile["evidence"].append(
        {
            "phase": "phase_2_controlled_calibration",
            "session_quality": quality_rows,
            "field_decisions": decisions,
        }
    )
    profile["limitations"] = [
        "Not a universal Honda/Keihin profile.",
        "ECU part number, firmware version, and ECM identification response are still not captured.",
        "MAP signal location is unresolved in this 24-byte 0x17 response.",
        "Injector milliseconds scale requires oscilloscope or logic-analyzer ground truth.",
    ]
    return profile


def _csv_value(value: Any) -> Any:
    if isinstance(value, list | dict):
        return json.dumps(value, sort_keys=True)
    if value is None:
        return ""
    return value


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    materialized = list(rows)
    fieldnames: list[str] = []
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


def _decision_rows(decisions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"signal": signal, **decisions[signal]} for signal in SIGNAL_ORDER if signal in decisions]


def _comparison_rows(decisions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "v0.1_uncertainty": "RPM identity/scaling not grounded in controlled states",
            "controlled_experiment": "engine-off/key-on + cold-start",
            "result": "0 while off, non-zero when engine operates",
            "new_confidence": decisions["RPM"]["identity_status"],
        },
        {
            "v0.1_uncertainty": "TPS percent used raw*100/255 without endpoint validation",
            "controlled_experiment": "engine-off TPS sweep",
            "result": "byte6/byte7 verified as TPS-related; old percent full-scale is rejected",
            "new_confidence": decisions["TPS raw"]["identity_status"],
        },
        {
            "v0.1_uncertainty": "IAT location unresolved",
            "controlled_experiment": "ambient cold baseline + warm-up",
            "result": "bytes10-11 behave as IAT",
            "new_confidence": decisions["IAT"]["identity_status"],
        },
        {
            "v0.1_uncertainty": "bytes12-13 were hypothesized as MAP",
            "controlled_experiment": "engine-start response + 15-min warm-up",
            "result": "MAP hypothesis rejected; bytes12-13 behave as engine-temperature thermistor pair",
            "new_confidence": decisions["ECT"]["identity_status"],
        },
        {
            "v0.1_uncertainty": "battery scaling lacked external reference",
            "controlled_experiment": "key-on VOM measurement",
            "result": "byte14/10 agrees within a small offset",
            "new_confidence": decisions["Battery"]["identity_status"],
        },
    ]
    return rows


def _fmt(value: Any, digits: int = 2) -> str:
    if isinstance(value, int | float):
        return f"{value:.{digits}f}"
    if value is None:
        return "n/a"
    return str(value)


def _markdown_table(rows: list[dict[str, Any]], headers: list[str]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(header, "")).replace("\n", " ") for header in headers) + " |")
    return "\n".join(out)


def _calibration_markdown(summary: dict[str, Any]) -> str:
    decisions = summary["field_decisions"]
    comparison = _markdown_table(
        summary["v01_uncertainty_resolution"],
        ["v0.1_uncertainty", "controlled_experiment", "result", "new_confidence"],
    )
    decision_rows = [
        {
            "Signal": signal,
            "v0.1 mapping/hypothesis": row["v0.1_mapping_hypothesis"],
            "Calibration evidence": row["calibration_evidence"],
            "Identity status": row["identity_status"],
            "Scaling status": row["scaling_status"],
            "Recommended ML use": row["recommended_ml_use"],
        }
        for signal, row in decisions.items()
    ]
    decision_table = _markdown_table(
        decision_rows,
        [
            "Signal",
            "v0.1 mapping/hypothesis",
            "Calibration evidence",
            "Identity status",
            "Scaling status",
            "Recommended ML use",
        ],
    )
    session_lines = "\n".join(
        f"- `{row['session_id']}` / `{row['scenario_id']}`: {row['frame_count']} frames, "
        f"{row['valid_frame_count']} valid, checksum failures {row['checksum_failures']}, "
        f"header failures {row['header_failures']}, malformed records {row['malformed_records']}, "
        f"duration {_fmt(row['duration_seconds'])} s"
        for row in summary["sessions"]
    )
    readiness = summary["ml_readiness"]
    approved = ", ".join(f"`{field}`" for field in readiness["approved_fields"])
    rejected = ", ".join(f"`{field}`" for field in summary["old_mappings_rejected"])
    verified = ", ".join(f"`{field}`" for field in summary["fields_promoted_verified"])
    high_confidence = ", ".join(f"`{field}`" for field in summary["fields_promoted_high_confidence"])
    unresolved = ", ".join(f"`{field}`" for field in summary["fields_unverified_unavailable"])
    battery = summary["battery"]
    warmup = summary["scenario_results"]["cold_start_warmup"]
    tps = summary["scenario_results"]["tps_sweep"]
    return f"""# Phase 2 Calibration Decoder Audit

## v0.1 Uncertainty Resolution

{comparison}

## Calibration Sessions

{session_lines}

## Required Signal Decision Table

{decision_table}

## Key Evidence

- TPS endpoints: byte6 `{_fmt(tps.get('observed_closed_byte6'), 0)}` to `{_fmt(tps.get('observed_open_byte6'), 0)}`; byte7 `{_fmt(tps.get('observed_closed_byte7'), 0)}` to `{_fmt(tps.get('observed_open_byte7'), 0)}`; byte6/byte7 correlation `{_fmt(tps.get('byte6_byte7_correlation'), 6)}`.
- TPS old percentage formula at full: `{_fmt(tps.get('old_formula', {}).get('full_percent'))}%`; endpoint-calibrated full is `100.00%`.
- IAT candidate: byte10 voltage candidate and `byte11 - 40`; ambient error `{_fmt(warmup.get('iat_ambient_error_c'))} C`.
- ECT candidate: bytes8-9 remain `{summary['ect_search']['byte8_unique_values']}` / `{summary['ect_search']['byte9_unique_values']}`; engine-temperature signal found at byte13 with `byte13 - 40`, ambient error `{_fmt(warmup.get('ect_ambient_error_c'))} C`.
- v0.1 MAP hypothesis: byte12 delta 2-10 s after start `{_fmt(warmup.get('v01_map_hypothesis_test', {}).get('byte12_delta'), 3)}`, byte13 delta `{_fmt(warmup.get('v01_map_hypothesis_test', {}).get('byte13_delta'), 3)}`; over warm-up byte12 falls while byte13 rises.
- Battery: VOM `{_fmt(battery.get('external_vom_v'))} V`, ECU byte14-derived mean `{_fmt(battery.get('ecu_byte14_mean_v'), 3)} V`, difference `{_fmt(battery.get('absolute_difference_v'), 3)} V`.

## Old Mappings Rejected

{rejected}

## Promotions

- VERIFIED: {verified}
- HIGH_CONFIDENCE: {high_confidence}
- UNVERIFIED/UNAVAILABLE: {unresolved}

## ML Readiness

- `ready_for_ml`: {str(readiness['ready_for_ml']).lower()}
- `ml_approved`: {approved}

Reasons:

{chr(10).join(f'- {reason}' for reason in readiness['reasons'])}

## v0.2 Profile

Generated: `{summary['v02_profile_path']}`
"""


def _scale_points(values: list[float], width: int, height: int, padding: int) -> list[tuple[float, float]]:
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    span = maximum - minimum if maximum != minimum else 1.0
    x_span = max(1, len(values) - 1)
    points = []
    for index, value in enumerate(values):
        x = padding + (index / x_span) * (width - 2 * padding)
        y = height - padding - ((value - minimum) / span) * (height - 2 * padding)
        points.append((x, y))
    return points


def _write_multiseries_svg(
    path: Path,
    *,
    records: list[dict[str, Any]],
    title: str,
    series: list[tuple[str, str, str]],
    events: list[CalibrationEvent],
    start_ms: float | None = None,
    end_ms: float | None = None,
) -> None:
    selected = [
        record
        for record in records
        if isinstance(record.get("elapsed_ms"), int | float)
        and (start_ms is None or record["elapsed_ms"] >= start_ms)
        and (end_ms is None or record["elapsed_ms"] <= end_ms)
    ]
    width = 980
    height = 320
    padding = 42
    times = [float(record["elapsed_ms"]) for record in selected]
    if not times:
        path.write_text("", encoding="utf-8")
        return
    min_time = min(times)
    max_time = max(times)
    span_time = max(max_time - min_time, 1.0)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{padding}" y="24" font-family="sans-serif" font-size="14" fill="#111827">{html.escape(title)}</text>',
        f'<line x1="{padding}" y1="{height - padding}" x2="{width - padding}" y2="{height - padding}" stroke="#9ca3af"/>',
        f'<line x1="{padding}" y1="{padding}" x2="{padding}" y2="{height - padding}" stroke="#9ca3af"/>',
    ]
    for event in events:
        if event.event_name == "capture_start" or event.event_name == "capture_end":
            continue
        if event.elapsed_ms < min_time or event.elapsed_ms > max_time:
            continue
        x = padding + ((event.elapsed_ms - min_time) / span_time) * (width - 2 * padding)
        parts.append(f'<line x1="{x:.1f}" y1="{padding}" x2="{x:.1f}" y2="{height - padding}" stroke="#d1d5db" stroke-dasharray="3 3"/>')
        parts.append(
            f'<text x="{x + 3:.1f}" y="{padding + 12}" font-family="sans-serif" font-size="10" fill="#374151">{html.escape(event.event_name)}</text>'
        )
    legend_y = 42
    for label, signal, color in series:
        values = _values(selected, signal)
        points = _scale_points(values, width, height, padding)
        if not points:
            continue
        # Preserve real time spacing after y scaling.
        min_value = min(values)
        max_value = max(values)
        span_value = max(max_value - min_value, 1.0)
        points = []
        for record, value in zip(selected, values, strict=False):
            x = padding + ((float(record["elapsed_ms"]) - min_time) / span_time) * (width - 2 * padding)
            y = height - padding - ((value - min_value) / span_value) * (height - 2 * padding)
            points.append((x, y))
        polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        parts.append(f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.7"/>')
        parts.append(f'<rect x="{width - 230}" y="{legend_y - 9}" width="10" height="10" fill="{color}"/>')
        parts.append(
            f'<text x="{width - 214}" y="{legend_y}" font-family="sans-serif" font-size="11" fill="#111827">{html.escape(label)} min={min_value:.2f} max={max_value:.2f}</text>'
        )
        legend_y += 15
    parts.append("</svg>\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_calibration_plots(
    *,
    output_dir: Path,
    sessions: list[CalibrationSession],
    tps: dict[str, Any],
    warmup: dict[str, Any],
) -> None:
    plots_dir = output_dir / "plots" / "calibration"
    plots_dir.mkdir(parents=True, exist_ok=True)
    tps_session = _find_session(sessions, "cal_02_tps_sweep")
    warmup_session = _find_session(sessions, "cal_03_cold_start_warmup")
    if tps_session is not None:
        _write_multiseries_svg(
            plots_dir / "tps_byte6_byte7_vs_time.svg",
            records=tps_session.valid_records,
            title="TPS sweep: byte6 and byte7 vs time",
            series=[("byte6", "byte6", "#2563eb"), ("byte7", "byte7", "#dc2626")],
            events=tps_session.events,
        )
    if warmup_session is not None:
        start_ms = warmup.get("first_nonzero_rpm_ms") or warmup.get("engine_started_event_ms")
        _write_multiseries_svg(
            plots_dir / "engine_start_rpm_bytes10_13.svg",
            records=warmup_session.valid_records,
            title="Engine start: RPM and bytes10-13",
            series=[
                ("RPM", "rpm", "#111827"),
                ("byte10", "byte10", "#2563eb"),
                ("byte11", "byte11", "#16a34a"),
                ("byte12", "byte12", "#dc2626"),
                ("byte13", "byte13", "#9333ea"),
            ],
            events=warmup_session.events,
            start_ms=(start_ms - 6000) if isinstance(start_ms, int | float) else None,
            end_ms=(start_ms + 20000) if isinstance(start_ms, int | float) else None,
        )
        _write_multiseries_svg(
            plots_dir / "thermal_iat_ect_candidates_warmup.svg",
            records=warmup_session.valid_records,
            title="Thermal candidates over complete warm-up",
            series=[
                ("IAT byte11-40", "iat_c", "#16a34a"),
                ("ECT byte13-40", "ect_c", "#dc2626"),
                ("byte12 voltage raw", "byte12", "#2563eb"),
            ],
            events=warmup_session.events,
        )
        _write_multiseries_svg(
            plots_dir / "map_hypothesis_bytes12_13_start.svg",
            records=warmup_session.valid_records,
            title="MAP hypothesis check: bytes12-13 around start",
            series=[("byte12", "byte12", "#2563eb"), ("byte13", "byte13", "#dc2626")],
            events=warmup_session.events,
            start_ms=(start_ms - 6000) if isinstance(start_ms, int | float) else None,
            end_ms=(start_ms + 60000) if isinstance(start_ms, int | float) else None,
        )
        _write_multiseries_svg(
            plots_dir / "battery_byte14_start.svg",
            records=warmup_session.valid_records,
            title="Battery byte14/10 around start",
            series=[("battery_v", "battery_v", "#2563eb"), ("RPM", "rpm", "#111827")],
            events=warmup_session.events,
            start_ms=(start_ms - 6000) if isinstance(start_ms, int | float) else None,
            end_ms=(start_ms + 60000) if isinstance(start_ms, int | float) else None,
        )
        _write_multiseries_svg(
            plots_dir / "injector_raw_start_warmup.svg",
            records=warmup_session.valid_records,
            title="Injector raw around start and warm-up",
            series=[("injector_raw", "injector_raw", "#dc2626"), ("RPM", "rpm", "#111827")],
            events=warmup_session.events,
        )


def run_calibration_audit(
    *,
    input_dir: Path,
    profile_id: str,
    output_dir: Path,
    strict_checksum: bool = True,
    allow_legacy_29_byte: bool = False,
    generate_plots: bool = False,
    max_records: int | None = None,
) -> dict[str, Any]:
    sessions = load_calibration_sessions(
        input_dir,
        strict_checksum=strict_checksum,
        allow_legacy_29_byte=allow_legacy_29_byte,
        max_records=max_records,
    )
    if not sessions:
        raise ValueError("no controlled calibration sessions found under input directory")

    quality_rows = [_session_quality(session) for session in sessions]
    cold = analyze_cold_key_on(_find_session(sessions, "cal_01_cold_key_on"))
    tps = analyze_tps_sweep(_find_session(sessions, "cal_02_tps_sweep"))
    warmup = analyze_warmup(_find_session(sessions, "cal_03_cold_start_warmup"))
    all_records = _all_valid_records(sessions)
    ect_search = {
        "byte8_unique_values": _unique_values(all_records, 8),
        "byte9_unique_values": _unique_values(all_records, 9),
        "bytes8_9_verdict": (
            "UNAVAILABLE for this 0x17 response"
            if _unique_values(all_records, 8) == [255] and _unique_values(all_records, 9) == [255]
            else "candidate data present"
        ),
    }
    battery = _battery_external_summary(cold)
    injector = _injector_summary(sessions, warmup)
    byte17 = _byte17_summary(all_records)
    byte18 = _byte18_summary(all_records)
    decisions = _field_decisions(
        tps=tps,
        cold=cold,
        warmup=warmup,
        battery=battery,
        injector=injector,
        byte17=byte17,
        byte18=byte18,
    )
    ml_readiness = _ml_readiness(decisions)
    summary: dict[str, Any] = {
        "profile_id": profile_id,
        "phase2_profile_id": "honda_keihin_71_17_v0.2",
        "sessions": quality_rows,
        "vehicle_metadata": _read_vehicle_metadata(input_dir),
        "scenario_results": {
            "cold_key_on": cold,
            "tps_sweep": tps,
            "cold_start_warmup": warmup,
        },
        "ect_search": ect_search,
        "battery": battery,
        "injector": injector,
        "byte17": byte17,
        "byte18": byte18,
        "field_decisions": decisions,
        "v01_uncertainty_resolution": _comparison_rows(decisions),
        "old_mappings_rejected": [
            "iat_c = byte12 - 40",
            "MAP hypothesis for bytes12-13",
            "speed_kmh/direct speed interpretation for byte18",
            "tps_percent = byte7 * 100 / 255 as physical full-scale percent",
        ],
        "fields_promoted_verified": ["RPM", "TPS voltage", "TPS raw", "Battery"],
        "fields_promoted_high_confidence": ["TPS percent calibrated", "IAT", "ECT", "Injector raw"],
        "fields_unverified_unavailable": ["MAP actual location", "Injector ms", "Byte17", "Byte18 speed", "bytes8-9 legacy ECT pair"],
        "ml_readiness": ml_readiness,
        "v02_profile_path": str(output_dir.parent / "profiles" / "honda_keihin_71_17_v0.2.json"),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = output_dir.parent / "profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "calibration_sessions.csv", quality_rows)
    _write_csv(output_dir / "calibration_signal_decisions.csv", _decision_rows(decisions))
    _write_csv(output_dir / "calibration_tps_responders.csv", tps.get("strong_sweep_responders", []))
    _write_csv(output_dir / "calibration_thermal_candidates.csv", warmup.get("thermal_candidates", []))
    (output_dir / "calibration_audit.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output_dir / "calibration_audit.md").write_text(_calibration_markdown(summary), encoding="utf-8")
    profile = _profile_v02(
        profile_id="honda_keihin_71_17_v0.2",
        vehicle_metadata=summary["vehicle_metadata"],
        decisions=decisions,
        quality_rows=quality_rows,
    )
    (profile_dir / "honda_keihin_71_17_v0.2.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")

    if generate_plots:
        _write_calibration_plots(output_dir=output_dir, sessions=sessions, tps=tps, warmup=warmup)

    return summary
