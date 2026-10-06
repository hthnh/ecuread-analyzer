from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.audit.calibration import (
    analyze_tps_sweep,
    analyze_warmup,
    discover_calibration_sessions,
    frames_for_event_pair,
    load_calibration_session,
    load_events_csv,
    run_calibration_audit,
)
from app.ecu.profiles.honda_keihin_71_17 import expected_checksum_byte


def make_frame(
    *,
    rpm: int = 0,
    byte6: int = 25,
    byte7: int = 0,
    byte10: int = 122,
    byte11: int = 76,
    byte12: int = 122,
    byte13: int = 76,
    byte14: int = 125,
    injector: int = 0,
    byte17: int = 88,
    byte18: int = 129,
) -> str:
    frame = [
        0x02,
        0x18,
        0x71,
        0x17,
        (rpm >> 8) & 0xFF,
        rpm & 0xFF,
        byte6,
        byte7,
        0xFF,
        0xFF,
        byte10,
        byte11,
        byte12,
        byte13,
        byte14,
        (injector >> 8) & 0xFF,
        injector & 0xFF,
        byte17,
        byte18,
        0,
        0,
        0,
        0,
        0,
    ]
    frame[-1] = expected_checksum_byte(frame[:-1])
    return "".join(f"{byte:02X}" for byte in frame)


def write_session(
    root: Path,
    *,
    session_id: str,
    scenario_id: str,
    events: list[tuple[str, int]],
    frames: list[tuple[int, str]],
    environment: dict | None = None,
) -> Path:
    session_dir = root / "calibration" / "vehicle_1" / session_id
    session_dir.mkdir(parents=True)
    metadata = {
        "session_id": session_id,
        "scenario_id": scenario_id,
        "vehicle_id": "vehicle_1",
        "environment": environment or {},
        "vehicle": {"manufacturer": "Honda", "model": "SH Mode"},
    }
    summary = {
        "duration_seconds": (frames[-1][0] - frames[0][0]) / 1000 if frames else 0,
        "malformed_lines": 0,
    }
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (session_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (session_dir / "notes.txt").write_text("", encoding="utf-8")
    event_lines = ["event_index,host_time,elapsed_ms,event_type,event_name,note"]
    for index, (name, elapsed_ms) in enumerate(events, start=1):
        event_lines.append(f"{index},2026-08-14T00:00:00Z,{elapsed_ms},manual,{name},")
    (session_dir / "events.csv").write_text("\n".join(event_lines), encoding="utf-8")
    raw_lines = []
    for index, (elapsed_ms, raw_hex) in enumerate(frames, start=1):
        raw_lines.append(
            json.dumps(
                {
                    "capture_seq": index,
                    "device_time_ms": elapsed_ms,
                    "host_monotonic_ms": elapsed_ms,
                    "raw_hex": raw_hex,
                    "raw_length": 24,
                    "scenario_id": scenario_id,
                    "session_id": session_id,
                    "vehicle_id": "vehicle_1",
                }
            )
        )
    (session_dir / "raw_frames.jsonl").write_text("\n".join(raw_lines), encoding="utf-8")
    return session_dir


def test_calibration_session_discovery_and_raw_frames_schema(tmp_path: Path) -> None:
    session_dir = write_session(
        tmp_path,
        session_id="cal_tps",
        scenario_id="cal_02_tps_sweep",
        events=[("capture_start", 0), ("tps_closed_begin", 0), ("tps_closed_end", 100)],
        frames=[(0, make_frame()), (100, make_frame(byte6=30, byte7=4))],
    )
    assert discover_calibration_sessions(tmp_path) == [session_dir]
    session = load_calibration_session(session_dir)
    assert session.scenario_id == "cal_02_tps_sweep"
    assert len(session.records) == 2
    assert session.valid_records[0]["elapsed_ms"] == 0
    assert session.valid_records[0]["frame"][6] == 25


def test_events_csv_parsing_and_frame_event_alignment(tmp_path: Path) -> None:
    session_dir = write_session(
        tmp_path,
        session_id="cal_align",
        scenario_id="cal_02_tps_sweep",
        events=[("tps_closed_begin", 100), ("tps_closed_end", 300)],
        frames=[
            (50, make_frame(byte7=9)),
            (150, make_frame(byte7=0)),
            (250, make_frame(byte7=0)),
            (350, make_frame(byte7=20)),
        ],
    )
    events = load_events_csv(session_dir / "events.csv")
    assert [event.event_name for event in events] == ["tps_closed_begin", "tps_closed_end"]
    session = load_calibration_session(session_dir)
    aligned = frames_for_event_pair(session, "tps_closed_begin", "tps_closed_end")
    assert [record["elapsed_ms"] for record in aligned] == [150, 250]


def test_tps_endpoint_extraction_prefers_calibrated_percent(tmp_path: Path) -> None:
    session_dir = write_session(
        tmp_path,
        session_id="cal_tps",
        scenario_id="cal_02_tps_sweep",
        events=[
            ("tps_closed_begin", 0),
            ("tps_closed_end", 100),
            ("tps_sweep_1_open_begin", 100),
            ("tps_sweep_1_full", 300),
            ("tps_sweep_1_close_begin", 300),
            ("tps_sweep_1_end", 500),
            ("tps_plateau_full_begin", 300),
            ("tps_plateau_full_end", 400),
        ],
        frames=[
            (0, make_frame(byte6=25, byte7=0)),
            (100, make_frame(byte6=25, byte7=0)),
            (200, make_frame(byte6=125, byte7=78)),
            (300, make_frame(byte6=225, byte7=156)),
            (400, make_frame(byte6=225, byte7=156)),
            (500, make_frame(byte6=25, byte7=0)),
        ],
    )
    result = analyze_tps_sweep(load_calibration_session(session_dir))
    assert result["observed_closed_byte7"] == 0
    assert result["observed_open_byte7"] == 156
    assert result["old_formula"]["full_percent"] == pytest.approx(61.1764705882)
    assert result["calibrated_formula"]["full_percent"] == 100.0
    assert result["recommendation"] == "endpoint-calibrated scaling preferred"


def test_engine_start_and_warmup_trend(tmp_path: Path) -> None:
    session_dir = write_session(
        tmp_path,
        session_id="cal_warm",
        scenario_id="cal_03_cold_start_warmup",
        environment={"ambient_temperature_c": 34.5, "engine_cold_confirmed": True},
        events=[
            ("key_on", 0),
            ("engine_started", 1000),
            ("warmup_1min", 60000),
            ("warmup_15min", 900000),
        ],
        frames=[
            (0, make_frame(rpm=0, byte11=76, byte12=122, byte13=76, injector=0)),
            (500, make_frame(rpm=0, byte11=76, byte12=122, byte13=76, injector=0)),
            (1000, make_frame(rpm=1400, byte11=76, byte12=122, byte13=76, injector=900)),
            (60000, make_frame(rpm=1300, byte11=76, byte12=110, byte13=83, injector=600)),
            (900000, make_frame(rpm=1350, byte11=78, byte12=33, byte13=129, injector=520)),
        ],
    )
    result = analyze_warmup(load_calibration_session(session_dir))
    assert result["first_nonzero_rpm_ms"] == 1000
    assert result["iat_ambient_error_c"] == pytest.approx(1.5)
    assert result["ect_ambient_error_c"] == pytest.approx(1.5)
    byte12 = next(row for row in result["thermal_candidates"] if row["byte_index"] == 12)
    assert byte12["total_change"] < 0


def test_calibration_audit_writes_v02_profile(tmp_path: Path) -> None:
    (tmp_path / "_vehicles").mkdir()
    (tmp_path / "_vehicles" / "vehicle_1.json").write_text(
        json.dumps(
            {
                "vehicle_id": "vehicle_1",
                "manufacturer": "Honda",
                "model": "SH Mode",
                "generation": "4-valve",
                "cooling_type": "liquid",
                "ecu_protocol": "Honda/Keihin K-Line",
            }
        ),
        encoding="utf-8",
    )
    write_session(
        tmp_path,
        session_id="cal_key",
        scenario_id="cal_01_cold_key_on",
        environment={"ambient_temperature_c": 34.5, "external_battery_v": 12.75},
        events=[("key_on", 0), ("capture_end", 1000)],
        frames=[(0, make_frame(byte14=125)), (1000, make_frame(byte14=125))],
    )
    write_session(
        tmp_path,
        session_id="cal_tps",
        scenario_id="cal_02_tps_sweep",
        events=[
            ("tps_closed_begin", 0),
            ("tps_closed_end", 100),
            ("tps_sweep_1_open_begin", 100),
            ("tps_sweep_1_full", 300),
            ("tps_sweep_1_close_begin", 300),
            ("tps_sweep_1_end", 500),
            ("tps_plateau_full_begin", 300),
            ("tps_plateau_full_end", 400),
        ],
        frames=[
            (0, make_frame(byte6=25, byte7=0)),
            (100, make_frame(byte6=25, byte7=0)),
            (200, make_frame(byte6=125, byte7=78)),
            (300, make_frame(byte6=225, byte7=156)),
            (400, make_frame(byte6=225, byte7=156)),
        ],
    )
    write_session(
        tmp_path,
        session_id="cal_warm",
        scenario_id="cal_03_cold_start_warmup",
        environment={"ambient_temperature_c": 34.5, "engine_cold_confirmed": True},
        events=[("key_on", 0), ("engine_started", 1000), ("warmup_15min", 900000)],
        frames=[
            (0, make_frame(rpm=0, byte11=76, byte12=122, byte13=76)),
            (1000, make_frame(rpm=1400, byte11=76, byte12=122, byte13=76, injector=900)),
            (900000, make_frame(rpm=1350, byte11=78, byte12=33, byte13=129, injector=520)),
        ],
    )

    output_dir = tmp_path / "reports"
    summary = run_calibration_audit(
        input_dir=tmp_path,
        profile_id="honda_keihin_71_17_v0.1",
        output_dir=output_dir,
        strict_checksum=True,
    )
    profile_path = tmp_path / "profiles" / "honda_keihin_71_17_v0.2.json"
    assert summary["ml_readiness"]["ready_for_ml"] is True
    assert profile_path.exists()
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["fields"]["ect"]["source_bytes"] == [12, 13]
    assert profile["fields"]["map_v01_hypothesis"]["identity_status"] == "REJECTED"
