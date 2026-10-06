from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.telemetry import (
    CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
    CanonicalTelemetrySession,
    SamplingMetadata,
    TelemetrySample,
)
from app.ecu.profiles.honda_keihin_71_17 import (
    PRODUCTION_DECODER_VERSION,
    checksum_valid,
    expected_checksum_byte,
)
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file, native_frames_to_canonical_session
from app.ml.features import get_feature_names
from app.ml.profile_v02_training import V02_ML_SIGNAL_COLUMNS, train_profile_v02_from_jsonl_files
from app.ml.validation import MLInputValidationError, validate_canonical_session_for_ml
from app.services.analysis_service import AnalysisService
from app.services.training_service import train_from_raw_files


def make_native_frame(
    *,
    rpm: int = 1200,
    tps_raw: int = 8,
    tps_voltage_raw: int = 36,
    iat_raw: int = 86,
    ect_voltage_raw: int = 80,
    ect_raw: int = 130,
    battery_raw: int = 145,
    injector_raw: int = 600,
) -> list[int]:
    frame = [
        0x02,
        0x18,
        0x71,
        0x17,
        (rpm >> 8) & 0xFF,
        rpm & 0xFF,
        tps_voltage_raw,
        tps_raw,
        0xFF,
        0xFF,
        120,
        iat_raw,
        ect_voltage_raw,
        ect_raw,
        battery_raw,
        (injector_raw >> 8) & 0xFF,
        injector_raw & 0xFF,
        168,
        106,
        0,
        0,
        0,
        0,
        0,
    ]
    frame[-1] = expected_checksum_byte(frame[:-1])
    return frame


def write_native_jsonl(path: Path, count: int = 45) -> None:
    rows = []
    for index in range(count):
        frame = make_native_frame(
            rpm=1200 + index * 10,
            tps_raw=index % 80,
            tps_voltage_raw=25 + index % 80,
            iat_raw=86 + index % 2,
            ect_raw=130 + index % 3,
            battery_raw=145,
            injector_raw=600 + index,
        )
        rows.append(
            json.dumps(
                {
                    "elapsed_ms": index * 250,
                    "raw_hex": "".join(f"{byte:02X}" for byte in frame),
                    "raw_length": 24,
                }
            )
        )
    path.write_text("\n".join(rows), encoding="utf-8")


def test_native_24_byte_header_and_checksum_validation() -> None:
    frame = make_native_frame()
    assert len(frame) == 24
    assert frame[:4] == [0x02, 0x18, 0x71, 0x17]
    assert checksum_valid(frame)

    bad_header = list(frame)
    bad_header[2] = 0x72
    imported = native_frames_to_canonical_session([bad_header])
    sample = imported.canonical_session.samples[0]
    assert sample.frame_valid is False
    assert sample.quality_flags["header_valid"] is False

    bad_checksum = list(frame)
    bad_checksum[-1] ^= 0x01
    imported = native_frames_to_canonical_session([bad_checksum])
    sample = imported.canonical_session.samples[0]
    assert sample.frame_valid is False
    assert sample.checksum_valid is False


def test_native_import_builds_v2_canonical_and_candidate_signals(tmp_path: Path, settings) -> None:
    path = tmp_path / "native.jsonl"
    write_native_jsonl(path, count=25)

    imported = import_native_jsonl_file(
        path,
        session_id="sess_pi_native",
        device_id="pi5-001",
        vehicle_id="honda_shmode_4v_001",
        sample_interval_ms=250,
    )

    session = imported.canonical_session
    assert session.telemetry_schema_version == CANONICAL_TELEMETRY_SCHEMA_VERSION_V2
    assert session.ecu_profile_id == "honda_keihin_71_17"
    assert session.decoder_id == "honda_keihin_71_17"
    assert session.decoder_version == PRODUCTION_DECODER_VERSION
    assert session.source_type == "native_honda_0x17"
    assert imported.statistics["checksum_valid_frames"] == 25

    first = session.samples[0]
    assert first.tps_raw == 0
    assert first.tps_raw_candidate is None
    assert first.ect_c == 90
    assert first.ect_c_candidate is None
    assert first.map_raw is None
    assert "tps_percent_calibrated" in first.candidate_signals
    assert "injector_raw" in first.candidate_signals
    validate_canonical_session_for_ml(session, settings)


def test_v2_required_signal_validation_rejects_missing_core(settings) -> None:
    samples = [
        TelemetrySample(
            sequence=index,
            timestamp_ms=index * 100,
            rpm=1200 + index,
            tps_voltage=0.5,
            tps_raw=0,
            battery_voltage=14.5,
            iat_c=46,
            ect_c=90,
        )
        for index in range(25)
    ]
    samples[0].ect_c = None
    session = CanonicalTelemetrySession(
        session_id="sess_v2_missing",
        ecu_profile_id="honda_keihin_71_17",
        decoder_id="honda_keihin_71_17",
        decoder_version=PRODUCTION_DECODER_VERSION,
        telemetry_schema_version=CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
        sampling=SamplingMetadata(sample_interval_ms=100),
        samples=samples,
    )
    with pytest.raises(MLInputValidationError, match="required signal 'ect_c'"):
        validate_canonical_session_for_ml(session, settings)


def test_v2_windowing_features_and_inference(settings, tmp_path: Path) -> None:
    input_path = tmp_path / "native_training.jsonl"
    write_native_jsonl(input_path, count=60)
    model_dir = tmp_path / "models" / "pi-v2"
    output_dir = tmp_path / "training-output"

    metadata, report = train_profile_v02_from_jsonl_files(
        input_paths=[input_path],
        settings=settings,
        model_dir=model_dir,
        output_dir=output_dir,
        strict_checksum=True,
    )
    assert metadata["telemetry_schema_version"] == CANONICAL_TELEMETRY_SCHEMA_VERSION_V2
    assert metadata["signal_columns"] == V02_ML_SIGNAL_COLUMNS
    assert report["feature_count"] == len(get_feature_names(V02_ML_SIGNAL_COLUMNS))
    assert "corr_rpm_map" not in metadata["feature_names"]
    assert "corr_tps_map" not in metadata["feature_names"]

    imported = import_native_jsonl_file(input_path, session_id="sess_pi_infer", sample_interval_ms=250)
    output = AnalysisService(settings, model_dir=model_dir).analyze(
        imported.canonical_session,
        process_with_model=True,
        persist=False,
    )

    assert output.summary["telemetry_schema_version"] == CANONICAL_TELEMETRY_SCHEMA_VERSION_V2
    assert output.summary["model_loaded"] is True
    assert output.summary["window_count"] > 0
    assert output.summary["signal_columns"] == V02_ML_SIGNAL_COLUMNS
    assert output.summary["overall_status"] in {"ok", "monitor", "attention", "limited_data"}


def test_v1_model_rejects_v2_session(settings, fixture_204330, tmp_path: Path) -> None:
    train_from_raw_files([fixture_204330], settings, sample_interval_ms=100)
    path = tmp_path / "native.jsonl"
    write_native_jsonl(path, count=45)
    imported = import_native_jsonl_file(path, session_id="sess_v2_against_v1_model", sample_interval_ms=250)

    with pytest.raises(ValueError, match="telemetry schema mismatch|signal column mismatch|missing model features"):
        AnalysisService(settings).analyze(imported.canonical_session, process_with_model=True, persist=False)
