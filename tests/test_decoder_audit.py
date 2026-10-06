from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app.audit.formula_search import infer_formula_matches
from app.audit.report import run_audit


EXAMPLE_HEX = "0218711700141900FFFF60571F846E03ED9E7C0000000061"
SECOND_HEX = "0218711700141900FFFF794C7A4C6906629E8000000000B9"


def frame_from_hex(raw_hex: str) -> list[int]:
    return list(bytes.fromhex(raw_hex))


def test_formula_search_finds_existing_temperature_offsets() -> None:
    records = [
        {
            "frame": frame_from_hex(EXAMPLE_HEX),
            "decoded_fields": {"iat_c": -9, "ect_c": 92},
        },
        {
            "frame": frame_from_hex(SECOND_HEX),
            "decoded_fields": {"iat_c": 82, "ect_c": 36},
        },
    ]
    matches = {
        match.decoded_field: match
        for match in infer_formula_matches(records, decoded_fields=["iat_c", "ect_c"], tolerance=0.0)
    }
    assert matches["iat_c"].best_raw_source == "byte 12"
    assert matches["iat_c"].best_transform == "raw - 40"
    assert matches["iat_c"].exact_match_ratio == pytest.approx(1.0)
    assert matches["ect_c"].best_raw_source == "byte 13"
    assert matches["ect_c"].best_transform == "raw - 40"
    assert matches["ect_c"].exact_match_ratio == pytest.approx(1.0)


def test_audit_reports_sequence_gap_time_regression_and_malformed_jsonl(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "reports"
    input_dir.mkdir()
    log_path = input_dir / "session.jsonl"
    rows = [
        {
            "seq": 1,
            "device_time_ms": 1000,
            "session_hash": "test",
            "raw_hex": EXAMPLE_HEX,
            "raw_length": 24,
            "rpm": 20,
            "tps_voltage": 0.488,
            "iat_c": -9,
            "ect_c": 92,
            "battery_v": 11.0,
            "injector_raw": 1005,
            "injector_ms": 10.05,
            "fuel_cut_inferred": False,
        },
        "{malformed json",
        {
            "seq": 3,
            "device_time_ms": 900,
            "session_hash": "test",
            "raw_hex": EXAMPLE_HEX,
            "raw_length": 24,
            "rpm": 20,
            "tps_voltage": 0.488,
            "iat_c": -9,
            "ect_c": 92,
            "battery_v": 11.0,
            "injector_raw": 1005,
            "injector_ms": 10.05,
            "fuel_cut_inferred": False,
        },
    ]
    log_path.write_text(
        "\n".join(json.dumps(row) if isinstance(row, dict) else row for row in rows),
        encoding="utf-8",
    )

    summary = run_audit(
        input_dir=input_dir,
        input_paths=[],
        profile_id="honda_keihin_71_17_v0.1",
        output_dir=output_dir,
        strict_checksum=True,
        compare_existing_fields=True,
    )

    quality = summary["sessions"][0]
    assert quality["valid_frames"] == 2
    assert quality["malformed_records"] == 1
    assert quality["seq_gap_count"] == 1
    assert quality["missing_seq_values"] == 1
    assert quality["time_regressions"] == 1
    assert summary["readiness"]["ready_for_ml"] is False

    with (output_dir / "data_quality.csv").open(encoding="utf-8", newline="") as handle:
        data_quality_rows = list(csv.DictReader(handle))
    assert data_quality_rows[0]["malformed_records"] == "1"


def test_audit_fails_when_no_valid_frames(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "bad.jsonl").write_text(
        json.dumps({"seq": 1, "device_time_ms": 1, "raw_hex": "0218", "raw_length": 2}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no valid frames"):
        run_audit(
            input_dir=input_dir,
            input_paths=[],
            profile_id="honda_keihin_71_17_v0.1",
            output_dir=output_dir,
            strict_checksum=True,
        )

