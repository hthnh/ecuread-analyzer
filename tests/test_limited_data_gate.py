from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.config import MIN_SESSION_WINDOWS_FOR_STATUS
from app.domain.telemetry import (
    CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
    CanonicalTelemetrySession,
    SamplingMetadata,
    TelemetrySample,
    V2_SIGNAL_DEFINITIONS,
    V2_CORE_SIGNAL_COLUMNS,
)
from app.ecu.profiles.honda_keihin_71_17 import expected_checksum_byte
from app.main import create_app
from app.ml.inference import run_inference, status_from_scored_metrics
from app.ml.training import MODEL_FILENAME, SCALER_FILENAME, METADATA_FILENAME
from app.repositories.file_repository import FileBackedRepository
from app.services.analysis_service import AnalysisService


class IdentityScaler:
    def transform(self, values):
        return values


class FixedPredictionModel:
    def __init__(self, anomaly_count: int = 0, score: float = 0.0):
        self.anomaly_count = anomaly_count
        self.score = score

    def score_samples(self, values):
        return np.array([self.score] * len(values))

    def decision_function(self, values):
        return np.array([self.score] * len(values))

    def predict(self, values):
        return np.array([-1 if index < self.anomaly_count else 1 for index in range(len(values))])


def write_fixed_model(model_dir: Path, *, anomaly_count: int = 0, score: float = 0.0) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(FixedPredictionModel(anomaly_count=anomaly_count, score=score), model_dir / MODEL_FILENAME)
    joblib.dump(IdentityScaler(), model_dir / SCALER_FILENAME)
    metadata = {
        "model_version_id": "fixed-test-model",
        "version": "fixed-test-model",
        "feature_schema_version": "ecu-window-features-v1",
        "telemetry_schema_version": CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
        "signal_columns": V2_CORE_SIGNAL_COLUMNS,
        "training_decoder_versions": ["honda_keihin_71_17:1.0.0"],
        "feature_names": ["rpm_mean"],
        "feature_medians": {"rpm_mean": 0.0},
        "feature_iqrs": {"rpm_mean": 1.0},
        "training_score_p01": -1.0,
        "training_score_p05": -0.5,
        "training_score_median": 0.0,
    }
    (model_dir / METADATA_FILENAME).write_text(json.dumps(metadata), encoding="utf-8")


def feature_frame(window_count: int) -> pd.DataFrame:
    return pd.DataFrame({"rpm_mean": [0.0] * window_count})


def make_v2_session(sample_count: int, *, session_id: str = "limited-data-session") -> CanonicalTelemetrySession:
    return CanonicalTelemetrySession(
        session_id=session_id,
        vehicle_id="Honda SH Mode",
        device_id="esp32",
        ecu_profile_id="honda_keihin_71_17",
        decoder_id="honda_keihin_71_17",
        decoder_version="1.0.0",
        telemetry_schema_version=CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
        sampling=SamplingMetadata(sample_interval_ms=250),
        signal_definitions=V2_SIGNAL_DEFINITIONS.copy(),
        samples=[
            TelemetrySample(
                sequence=index,
                timestamp_ms=index * 250,
                rpm=1500 + index,
                tps_voltage=0.5,
                tps_raw=0,
                battery_voltage=14.5,
                iat_c=40,
                ect_c=90,
                frame_valid=True,
                checksum_valid=True,
            )
            for index in range(sample_count)
        ],
    )


def native_frame(rpm: int = 1500) -> list[int]:
    frame = [
        0x02,
        0x18,
        0x71,
        0x17,
        (rpm >> 8) & 0xFF,
        rpm & 0xFF,
        0x1A,
        0x00,
        0x00,
        0x00,
        0x90,
        0x50,
        0x50,
        0x82,
        0x91,
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


def test_status_precedence_and_window_boundaries(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    write_fixed_model(model_dir, anomaly_count=0)

    assert run_inference(feature_frame(0), model_dir).overall_status == "no_windows"
    assert run_inference(feature_frame(1), model_dir).overall_status == "limited_data"
    assert run_inference(feature_frame(9), model_dir).overall_status == "limited_data"
    assert run_inference(feature_frame(10), model_dir).overall_status == "ok"
    assert run_inference(feature_frame(11), model_dir).overall_status == "ok"

    unavailable = run_inference(feature_frame(1), tmp_path / "missing-model")
    assert unavailable.overall_status == "model_unavailable"


def test_strong_short_session_preserves_raw_anomaly_metrics(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    write_fixed_model(model_dir, anomaly_count=7)

    output = run_inference(feature_frame(7), model_dir)

    assert output.overall_status == "limited_data"
    assert output.anomaly_ratio == 1.0
    assert output.anomaly_windows == 7
    assert output.evidence_window_count == 7
    assert output.minimum_windows_for_status == MIN_SESSION_WINDOWS_FOR_STATUS
    assert output.evidence_sufficient is False
    assert output.windows["is_anomaly"].tolist() == [True] * 7


def test_clean_short_session_is_limited_data(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    write_fixed_model(model_dir, anomaly_count=0)

    output = run_inference(feature_frame(7), model_dir)

    assert output.overall_status == "limited_data"
    assert output.anomaly_ratio == 0.0
    assert output.anomaly_windows == 0
    assert output.windows["is_anomaly"].tolist() == [False] * 7


def test_long_session_status_mapping_thresholds_unchanged() -> None:
    assert status_from_scored_metrics(window_count=10, anomaly_ratio=0.0, health_score=100.0) == "ok"
    assert status_from_scored_metrics(window_count=10, anomaly_ratio=0.02, health_score=100.0) == "monitor"
    assert status_from_scored_metrics(window_count=10, anomaly_ratio=0.0, health_score=79.99) == "monitor"
    assert status_from_scored_metrics(window_count=10, anomaly_ratio=0.15, health_score=100.0) == "attention"
    assert status_from_scored_metrics(window_count=10, anomaly_ratio=0.0, health_score=49.99) == "attention"


def test_analysis_service_not_scored_preserves_special_status(settings, tmp_path: Path) -> None:
    output = AnalysisService(settings, model_dir=tmp_path / "missing").analyze(
        make_v2_session(20),
        process_with_model=False,
        persist=False,
    )

    assert output.summary["window_count"] == 1
    assert output.summary["overall_status"] == "not_scored"
    assert output.summary["evidence_sufficient"] is False


def test_analysis_api_can_return_limited_data(settings) -> None:
    write_fixed_model(settings.model_dir, anomaly_count=1)
    client = TestClient(create_app(settings))

    response = client.post("/api/v1/analysis", json=make_v2_session(20).model_dump(mode="json"))

    assert response.status_code == 200
    body = response.json()
    assert body["overall_status"] == "limited_data"
    assert body["window_count"] == 1
    assert body["anomaly_ratio"] == 1.0
    assert body["anomaly_window_count"] == 1
    assert body["minimum_windows_for_status"] == MIN_SESSION_WINDOWS_FOR_STATUS
    assert body["evidence_sufficient"] is False


def test_raw_decode_analyze_api_preserves_limited_data(settings) -> None:
    write_fixed_model(settings.model_dir / "honda_keihin_71_17_v2", anomaly_count=1)
    client = TestClient(create_app(settings))
    records = [
        {"sequence": index, "timestamp_ms": index * 250, "raw_hex": raw_hex(native_frame(1500 + index))}
        for index in range(20)
    ]

    response = client.post(
        "/api/v1/raw/decode-analyze",
        json={
            "session_id": "raw-limited-data",
            "device_id": "esp32",
            "raw_representation": "native24_table17",
            "sampling": {"sample_interval_ms": 250},
            "records": records,
            "process_with_model": True,
        },
    )

    assert response.status_code == 200
    analysis = response.json()["analysis"]
    assert analysis["overall_status"] == "limited_data"
    assert analysis["window_count"] == 1
    assert analysis["anomaly_ratio"] == 1.0
    assert analysis["anomaly_window_count"] == 1


def test_limited_data_is_persisted_in_analysis_run(settings, tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    write_fixed_model(model_dir, anomaly_count=1)
    repository = FileBackedRepository(settings)

    output = AnalysisService(settings, repository=repository, model_dir=model_dir).analyze(
        make_v2_session(20, session_id="persist-limited-data"),
        process_with_model=True,
        persist=True,
    )

    persisted = repository.read_analysis(output.analysis_run_id)
    assert persisted is not None
    assert persisted["overall_status"] == "limited_data"
    assert persisted["anomaly_ratio"] == 1.0
    assert persisted["evidence_window_count"] == 1
