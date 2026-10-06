from __future__ import annotations

from app.ecu.parser import parse_log_file
from app.ml.training import train_isolation_forest
from app.services.session_processor import SessionProcessor, build_feature_frame, build_frames_dataframe
from app.services.storage import StorageService


def test_end_to_end_train_then_infer(settings, fixture_204330, fixture_203744) -> None:
    training_parse = parse_log_file(fixture_204330)
    training_frames = build_frames_dataframe(training_parse, settings, sample_interval_ms=100)
    training_features = build_feature_frame(training_frames, settings)
    metadata = train_isolation_forest(training_features, [fixture_204330.name], settings)
    assert metadata["feature_names"]

    storage = StorageService(settings)
    processor = SessionProcessor(settings, storage)
    result = processor.process(
        "sess_test_pipeline",
        fixture_203744,
        fixture_203744.name,
        sample_interval_ms=100,
        process_with_model=True,
    )
    assert result["statistics"]["frames_found"] > 0
    assert result["statistics"]["checksum_valid_frames"] > 0
    assert result["statistics"]["decoded_valid_frames"] > 0
    assert result["statistics"]["windows_generated"] > 0
    assert result["result"]["model_loaded"] is True
    assert "anomaly_ratio" in result["result"]

