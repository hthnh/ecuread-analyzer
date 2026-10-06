from __future__ import annotations

import json

import pandas as pd
import pytest

from app.domain.model import FEATURE_SCHEMA_VERSION
from app.domain.telemetry import CanonicalTelemetrySession, SamplingMetadata, TelemetrySample
from app.ingestion.raw_ecu.adapter import RAW_COMPAT_DECODER_VERSION, import_raw_file
from app.ml.validation import MLInputValidationError, validate_canonical_session_for_ml
from app.repositories.file_repository import FileBackedRepository
from app.services.analysis_service import AnalysisService
from app.services.session_processor import SessionProcessor
from app.services.storage import StorageService
from app.services.training_service import train_from_raw_files


def make_canonical_session(count: int = 25, *, session_id: str = "sess_canonical") -> CanonicalTelemetrySession:
    return CanonicalTelemetrySession(
        session_id=session_id,
        vehicle_id="bike-001",
        device_id="xiao-ecu-01",
        ecu_profile_id="honda_keihin_legacy_29",
        decoder_id="honda_keihin_legacy_29",
        decoder_version=RAW_COMPAT_DECODER_VERSION,
        sampling=SamplingMetadata(sample_interval_ms=100),
        samples=[
            TelemetrySample(
                sequence=index,
                timestamp_ms=index * 100,
                rpm=1200 + index,
                tps_voltage=0.5,
                tps_raw_candidate=0,
                battery_voltage=12.8,
                iat_c=33,
                ect_c_candidate=52,
                map_raw=89,
                frame_valid=True,
                checksum_valid=True,
            )
            for index in range(count)
        ],
    )


def test_valid_canonical_payload(settings) -> None:
    validate_canonical_session_for_ml(make_canonical_session(), settings)


def test_missing_required_signal_is_rejected(settings) -> None:
    session = make_canonical_session()
    session.samples[0].rpm = None
    with pytest.raises(MLInputValidationError, match="required signal 'rpm'"):
        validate_canonical_session_for_ml(session, settings)


def test_malformed_sequence_is_rejected(settings) -> None:
    session = make_canonical_session()
    session.samples[2].sequence = 1
    with pytest.raises(MLInputValidationError, match="unique and monotonic"):
        validate_canonical_session_for_ml(session, settings)


def test_insufficient_samples_are_rejected(settings) -> None:
    with pytest.raises(MLInputValidationError, match="insufficient samples"):
        validate_canonical_session_for_ml(make_canonical_session(count=5), settings)


def test_raw_adapter_preserves_current_decoded_values(settings, fixture_204330) -> None:
    raw_import = import_raw_file(fixture_204330, settings, session_id="sess_raw", sample_interval_ms=100)
    first = raw_import.canonical_session.samples[0]
    assert first.sequence == 0
    assert first.timestamp_ms == 0
    assert first.rpm == 0
    assert first.tps_voltage == pytest.approx(0.48828125)
    assert first.battery_voltage == pytest.approx(12.9)
    assert first.iat_c == 33
    assert first.ect_c_candidate == 52
    assert first.map_raw == 89
    assert first.checksum_valid is True


def test_raw_and_predecoded_paths_produce_equivalent_inference(settings, fixture_204330, fixture_203744) -> None:
    repository = FileBackedRepository(settings)
    train_from_raw_files([fixture_204330], settings, sample_interval_ms=100, repository=repository)

    storage = StorageService(settings)
    storage.repository = repository
    raw_result = SessionProcessor(settings, storage).process(
        "sess_raw_equivalent",
        fixture_203744,
        fixture_203744.name,
        sample_interval_ms=100,
        process_with_model=True,
    )

    raw_import = import_raw_file(fixture_203744, settings, session_id="sess_predecoded", sample_interval_ms=100)
    canonical_result = AnalysisService(settings, repository=repository).analyze(
        raw_import.canonical_session,
        process_with_model=True,
        persist=True,
    )

    assert raw_result["result"]["anomaly_ratio"] == canonical_result.summary["anomaly_ratio"]
    assert raw_result["result"]["health_score"] == canonical_result.summary["health_score"]
    assert raw_result["result"]["overall_status"] == canonical_result.summary["overall_status"]
    raw_windows = pd.read_csv(settings.data_dir / "processed" / "sess_raw_equivalent" / "windows.csv")
    pd.testing.assert_series_equal(raw_windows["prediction"], canonical_result.windows["prediction"], check_names=False)


def test_feature_schema_mismatch_is_rejected(settings, fixture_204330) -> None:
    train_from_raw_files([fixture_204330], settings, sample_interval_ms=100)
    metadata_path = settings.model_dir / "model_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["feature_schema_version"] = "wrong-feature-schema"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    session = import_raw_file(fixture_204330, settings, session_id="sess_schema", sample_interval_ms=100).canonical_session
    with pytest.raises(ValueError, match="feature schema mismatch"):
        AnalysisService(settings).analyze(session, process_with_model=True, persist=False)


def test_decoder_provenance_is_recorded(settings, fixture_204330) -> None:
    metadata, _report = train_from_raw_files([fixture_204330], settings, sample_interval_ms=100)
    assert metadata["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert metadata["training_decoder_versions"] == [f"honda_keihin_legacy_29:{RAW_COMPAT_DECODER_VERSION}"]
