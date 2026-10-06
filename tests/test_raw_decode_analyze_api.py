from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ecu.profiles.honda_keihin_71_17 import expected_checksum_byte
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def asymmetric_native_frame(
    *,
    rpm: int = 1500,
    sequence_delta: int = 0,
) -> list[int]:
    frame = [
        0x02,
        0x18,
        0x71,
        0x17,
        (rpm >> 8) & 0xFF,
        rpm & 0xFF,
        0x1A,
        0x02 + sequence_delta,
        0x00,
        0x00,
        0x90,
        0x4D,
        0x50,
        0x6A,
        0x7F,
        0x01,
        0x23,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
    ]
    frame[-1] = expected_checksum_byte(frame[:-1])
    return frame


def raw_hex(frame: list[int]) -> str:
    return "".join(f"{byte:02X}" for byte in frame)


def raw_payload(
    frame: list[int],
    *,
    raw_representation: str = "native24_table17",
    sequence: int = 0,
    timestamp_ms: float | None = 0,
    process_with_model: bool = False,
) -> dict:
    if raw_representation == "legacy29_ff5":
        frame = [0xFF] * 5 + frame
    return {
        "session_id": f"sess_raw_{raw_representation}_{sequence}",
        "device_id": "ecu-abc",
        "vehicle_id": None,
        "raw_representation": raw_representation,
        "sampling": {"sample_interval_ms": 250},
        "records": [
            {
                "sequence": sequence,
                "timestamp_ms": timestamp_ms,
                "raw_hex": raw_hex(frame),
            }
        ],
        "process_with_model": process_with_model,
    }


def post_raw(client: TestClient, payload: dict):
    return client.post("/api/v1/raw/decode-analyze", json=payload)


def core_sample(sample: dict) -> dict:
    return {
        key: sample[key]
        for key in (
            "rpm",
            "tps_voltage",
            "tps_raw",
            "battery_voltage",
            "iat_c",
            "ect_c",
            "frame_valid",
            "checksum_valid",
        )
    }


def test_raw_decode_analyze_legacy29_normalizes_to_native_v2(settings) -> None:
    client = TestClient(create_app(settings))
    response = post_raw(
        client,
        raw_payload(
            asymmetric_native_frame(),
            raw_representation="legacy29_ff5",
            sequence=7,
            timestamp_ms=1750,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    session = body["canonical_session"]
    sample = session["samples"][0]

    assert session["telemetry_schema_version"] == "canonical-telemetry-v2"
    assert session["ecu_profile_id"] == "honda_keihin_71_17"
    assert session["decoder_id"] == "honda_keihin_71_17"
    assert session["decoder_version"] == "1.0.0"
    assert session["sampling"] == {"sample_interval_ms": 250}
    assert session["source_type"] == "raw_decode_analyze"
    assert sample["sequence"] == 7
    assert sample["timestamp_ms"] == 1750
    assert sample["quality_flags"]["source_format"] == "legacy29_ff5"
    assert sample["rpm"] == 1500
    assert sample["tps_voltage"] == pytest.approx(0.5078125)
    assert sample["tps_raw"] == 2
    assert sample["iat_c"] == 37
    assert sample["ect_c"] == 66
    assert sample["battery_voltage"] == pytest.approx(12.7)
    assert sample["battery_voltage"] != pytest.approx(14.4)
    assert sample["ect_c"] != 40
    assert "map_raw" not in sample
    assert "tps_raw_candidate" not in sample
    assert "ect_c_candidate" not in sample
    assert "map" not in sample["candidate_signals"]

    analysis = body["analysis"]
    assert analysis["analysis_run_id"]
    assert analysis["telemetry_schema_version"] == "canonical-telemetry-v2"
    assert analysis["feature_schema_version"] == "ecu-window-features-v1"
    assert analysis["signal_columns"] == [
        "rpm",
        "tps_voltage",
        "tps_raw",
        "battery_voltage",
        "iat_c",
        "ect_c",
    ]
    assert analysis["window_count"] == 0
    assert analysis["overall_status"] in {"no_windows", "not_scored"}


def test_native24_and_legacy29_versions_converge_semantically(settings) -> None:
    client = TestClient(create_app(settings))
    frame = asymmetric_native_frame()
    native = post_raw(client, raw_payload(frame, raw_representation="native24_table17", sequence=2, timestamp_ms=500))
    legacy = post_raw(client, raw_payload(frame, raw_representation="legacy29_ff5", sequence=2, timestamp_ms=500))

    assert native.status_code == 200
    assert legacy.status_code == 200
    native_sample = native.json()["canonical_session"]["samples"][0]
    legacy_sample = legacy.json()["canonical_session"]["samples"][0]
    assert core_sample(native_sample) == core_sample(legacy_sample)
    assert native_sample["sequence"] == legacy_sample["sequence"] == 2
    assert native_sample["timestamp_ms"] == legacy_sample["timestamp_ms"] == 500


def test_raw_decode_analyze_rejects_bad_legacy_ff_prefix_as_invalid_sample(settings) -> None:
    client = TestClient(create_app(settings))
    frame = [0xFE, *([0xFF] * 4), *asymmetric_native_frame()]
    payload = raw_payload(asymmetric_native_frame(), raw_representation="legacy29_ff5")
    payload["records"][0]["raw_hex"] = raw_hex(frame)

    response = post_raw(client, payload)

    assert response.status_code == 200
    sample = response.json()["canonical_session"]["samples"][0]
    assert sample["frame_valid"] is False
    assert sample["checksum_valid"] is False
    assert "five leading FF bytes" in sample["quality_flags"]["validation_errors"][0]


def test_raw_decode_analyze_native_header_and_checksum_validation(settings) -> None:
    client = TestClient(create_app(settings))
    bad_header = asymmetric_native_frame()
    bad_header[0] = 0x03
    bad_header[-1] = expected_checksum_byte(bad_header[:-1])
    bad_header_response = post_raw(client, raw_payload(bad_header, raw_representation="native24_table17"))

    assert bad_header_response.status_code == 200
    bad_header_sample = bad_header_response.json()["canonical_session"]["samples"][0]
    assert bad_header_sample["frame_valid"] is False
    assert bad_header_sample["checksum_valid"] is True
    assert bad_header_sample["quality_flags"]["header_valid"] is False

    bad_checksum = asymmetric_native_frame()
    bad_checksum[-1] ^= 0x01
    bad_checksum_response = post_raw(client, raw_payload(bad_checksum, raw_representation="native24_table17"))

    assert bad_checksum_response.status_code == 200
    bad_checksum_sample = bad_checksum_response.json()["canonical_session"]["samples"][0]
    assert bad_checksum_sample["frame_valid"] is False
    assert bad_checksum_sample["checksum_valid"] is False
    assert "checksum failed" in bad_checksum_sample["quality_flags"]["validation_errors"][0]


def test_raw_decode_analyze_mixed_invalid_frames_preserve_sequence_and_timestamps(settings) -> None:
    client = TestClient(create_app(settings))
    valid_a = asymmetric_native_frame(rpm=1500)
    bad_checksum = asymmetric_native_frame(rpm=1600)
    bad_checksum[-1] ^= 0x01
    valid_b = asymmetric_native_frame(rpm=1700, sequence_delta=1)
    payload = {
        "session_id": "sess_raw_mixed",
        "device_id": "ecu-abc",
        "raw_representation": "native24_table17",
        "sampling": {"sample_interval_ms": 250},
        "records": [
            {"sequence": 10, "timestamp_ms": 2500, "raw_hex": raw_hex(valid_a)},
            {"sequence": 11, "timestamp_ms": 2750, "raw_hex": raw_hex(bad_checksum)},
            {"sequence": 12, "timestamp_ms": 3000, "raw_hex": raw_hex(valid_b)},
        ],
        "process_with_model": False,
    }

    response = post_raw(client, payload)

    assert response.status_code == 200
    samples = response.json()["canonical_session"]["samples"]
    assert [sample["sequence"] for sample in samples] == [10, 11, 12]
    assert [sample["timestamp_ms"] for sample in samples] == [2500, 2750, 3000]
    assert [sample["frame_valid"] for sample in samples] == [True, False, True]
    assert [sample["checksum_valid"] for sample in samples] == [True, False, True]
    assert response.json()["analysis"]["window_count"] == 0


def test_raw_decode_analyze_native24_bad_length_becomes_invalid_sample(settings) -> None:
    client = TestClient(create_app(settings))
    payload = raw_payload(asymmetric_native_frame()[:-1], raw_representation="native24_table17")

    response = post_raw(client, payload)

    assert response.status_code == 200
    sample = response.json()["canonical_session"]["samples"][0]
    assert sample["frame_valid"] is False
    assert "requires exactly 24 bytes" in sample["quality_flags"]["validation_errors"][0]


def test_checked_in_v2_model_metadata_matches_production_decoder() -> None:
    metadata_path = ROOT / "data" / "models" / "honda_keihin_71_17_v2" / "model_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["telemetry_schema_version"] == "canonical-telemetry-v2"
    assert metadata["decoder_id"] == "honda_keihin_71_17"
    assert metadata["decoder_version"] == "1.0.0"
    assert metadata["training_decoder_versions"] == ["honda_keihin_71_17:1.0.0"]
