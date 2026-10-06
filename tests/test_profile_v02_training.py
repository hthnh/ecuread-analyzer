from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.ecu.profiles.honda_keihin_71_17 import expected_checksum_byte
from app.ml.features import extract_window_features, get_feature_names
from app.ml.profile_v02_training import (
    V02_ML_SIGNAL_COLUMNS,
    decode_v02_ml_fields,
    load_profile_v02_jsonl_file,
    train_profile_v02_from_jsonl_files,
)
from app.ml.windowing import create_windows


def make_frame(
    *,
    rpm: int = 1200,
    tps_raw: int = 0,
    tps_voltage_raw: int = 25,
    iat_raw: int = 86,
    ect_voltage_raw: int = 80,
    ect_raw: int = 130,
    battery_raw: int = 145,
    injector_raw: int = 600,
) -> str:
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
    return "".join(f"{byte:02X}" for byte in frame)


def write_jsonl(path: Path, count: int = 45) -> None:
    rows = []
    for index in range(count):
        rows.append(
            json.dumps(
                {
                    "elapsed_ms": index * 250,
                    "raw_hex": make_frame(
                        rpm=1200 + index * 10,
                        tps_raw=index % 80,
                        tps_voltage_raw=25 + index % 80,
                        iat_raw=86 + index % 2,
                        ect_raw=130 + index % 3,
                        injector_raw=600 + index,
                    ),
                }
            )
        )
    path.write_text("\n".join(rows), encoding="utf-8")


def test_decode_v02_ml_fields() -> None:
    frame = list(bytes.fromhex(make_frame(rpm=1300, tps_raw=78, tps_voltage_raw=125, iat_raw=87, ect_raw=129)))
    decoded = decode_v02_ml_fields(frame)
    assert decoded["rpm"] == 1300
    assert decoded["tps_voltage"] == pytest.approx(2.44140625)
    assert decoded["tps_raw"] == 78
    assert decoded["tps_raw_candidate"] == 78
    assert decoded["tps_percent_calibrated"] == pytest.approx(50.0)
    assert decoded["iat_c"] == 47
    assert decoded["ect_c"] == 89


def test_profile_v02_features_do_not_require_map_raw() -> None:
    frame_data = pd.DataFrame(
        {
            "frame_index": list(range(25)),
            "relative_time_ms": [index * 250 for index in range(25)],
            "checksum_valid": [True] * 25,
            "is_valid": [True] * 25,
            "rpm": [1200 + index for index in range(25)],
            "tps_voltage": [0.5 + index * 0.01 for index in range(25)],
            "tps_raw": list(range(25)),
            "tps_raw_candidate": list(range(25)),
            "tps_percent_calibrated": [index * 100 / 156 for index in range(25)],
            "iat_c": [46] * 25,
            "ect_c": [89] * 25,
            "battery_voltage": [14.5] * 25,
        }
    )
    windows = create_windows(frame_data, window_size_samples=20, step_size_samples=5)
    features = extract_window_features(frame_data, windows, signal_columns=V02_ML_SIGNAL_COLUMNS)
    feature_names = get_feature_names(V02_ML_SIGNAL_COLUMNS)
    assert "corr_rpm_map" not in feature_names
    assert "corr_tps_map" not in feature_names
    assert "rpm_per_tps_voltage" in feature_names
    assert not features[feature_names].isna().any().any()


def test_profile_v02_training_writes_model_and_reports(settings, tmp_path: Path) -> None:
    input_path = tmp_path / "session.jsonl"
    write_jsonl(input_path)
    model_dir = tmp_path / "models" / "v02"
    output_dir = tmp_path / "ml"

    metadata, report = train_profile_v02_from_jsonl_files(
        input_paths=[input_path],
        settings=settings,
        model_dir=model_dir,
        output_dir=output_dir,
        strict_checksum=True,
    )

    assert report["valid_frames"] == 45
    assert report["windows_generated"] > 0
    assert report["telemetry_schema_version"] == "canonical-telemetry-v2"
    assert report["decoder_id"] == "honda_keihin_71_17"
    assert report["decoder_version"] == "1.0.0"
    assert metadata["decoder_profile_id"] == "honda_keihin_71_17_v0.2"
    assert metadata["telemetry_schema_version"] == "canonical-telemetry-v2"
    assert metadata["decoder_id"] == "honda_keihin_71_17"
    assert metadata["decoder_version"] == "1.0.0"
    assert metadata["signal_columns"] == V02_ML_SIGNAL_COLUMNS
    assert (model_dir / "isolation_forest.joblib").exists()
    assert (model_dir / "robust_scaler.joblib").exists()
    assert (model_dir / "model_metadata.json").exists()
    assert (output_dir / "frames_v0.2.csv").exists()
    assert (output_dir / "features_v0.2.csv").exists()
