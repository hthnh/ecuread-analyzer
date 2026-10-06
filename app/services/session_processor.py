from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from app.config import Settings
from app.ingestion.raw_ecu.adapter import import_raw_file, parse_result_to_canonical_session
from app.ingestion.raw_ecu.parser import ParseResult
from app.services.analysis_service import AnalysisService, build_feature_frame
from app.services.storage import StorageService, safe_filename


logger = logging.getLogger(__name__)


def determine_time_basis(sample_interval_ms: float | None, sampling_rate_hz: float | None) -> str:
    if sample_interval_ms is not None:
        return "relative_time_ms"
    if sampling_rate_hz is not None:
        return "sampling_rate_hz"
    return "sample_index"


def determine_result_time_basis(
    sample_interval_ms: float | None,
    sampling_rate_hz: float | None,
    raw_statistics: dict[str, Any] | None = None,
) -> str:
    if raw_statistics and int(raw_statistics.get("timestamped_frames", 0) or 0) > 0:
        return "source_timestamp_ms"
    return determine_time_basis(sample_interval_ms, sampling_rate_hz)


def relative_time_for_frame(
    frame_index: int,
    sample_interval_ms: float | None,
    sampling_rate_hz: float | None,
) -> float | None:
    if sample_interval_ms is not None:
        return frame_index * sample_interval_ms
    if sampling_rate_hz is not None and sampling_rate_hz > 0:
        return frame_index * (1000.0 / sampling_rate_hz)
    return None


def build_frames_dataframe(
    parse_result: ParseResult,
    settings: Settings,
    sample_interval_ms: float | None = None,
    sampling_rate_hz: float | None = None,
) -> pd.DataFrame:
    raw_import = parse_result_to_canonical_session(
        parse_result,
        settings,
        sample_interval_ms=sample_interval_ms,
        sampling_rate_hz=sampling_rate_hz,
    )
    return raw_import.frame_data


def validate_raw_import_for_processing(raw_import) -> None:
    if raw_import.statistics["frames_found"] == 0:
        if raw_import.statistics.get("source_format") == "esp_jsonl":
            raise ValueError("empty JSONL upload or no JSONL records found")
        raise ValueError("no RAW frames found in upload")
    if raw_import.statistics.get("source_format") == "esp_jsonl" and raw_import.statistics["frame_errors"] > 0:
        failed = raw_import.frame_data[raw_import.frame_data["parse_ok"] == False]  # noqa: E712
        examples = [
            f"line {int(row.line_number)}: {row.parse_error}"
            for row in failed.head(3).itertuples(index=False)
        ]
        suffix = "; ".join(examples)
        raise ValueError(f"malformed JSONL upload: {suffix}")


class SessionProcessor:
    def __init__(self, settings: Settings, storage: StorageService):
        self.settings = settings
        self.storage = storage

    def process(
        self,
        session_id: str,
        raw_path: str | Path,
        original_filename: str,
        *,
        device_id: str | None = None,
        vehicle_id: str | None = None,
        sample_interval_ms: float | None = None,
        sampling_rate_hz: float | None = None,
        session_note: str | None = None,
        firmware_version: str | None = None,
        client_session_id: str | None = None,
        upload_sha256: str | None = None,
        process_with_model: bool = True,
    ) -> dict[str, Any]:
        logger.info("session created", extra={"session_id": session_id})
        raw_import = import_raw_file(
            raw_path,
            self.settings,
            session_id=session_id,
            device_id=device_id,
            vehicle_id=vehicle_id,
            sample_interval_ms=sample_interval_ms,
            sampling_rate_hz=sampling_rate_hz,
            session_note=session_note,
            firmware_version=firmware_version,
        )
        return self.process_import(
            session_id,
            raw_path,
            original_filename,
            raw_import,
            device_id=device_id,
            vehicle_id=vehicle_id,
            sample_interval_ms=sample_interval_ms,
            sampling_rate_hz=sampling_rate_hz,
            session_note=session_note,
            firmware_version=firmware_version,
            client_session_id=client_session_id,
            upload_sha256=upload_sha256,
            process_with_model=process_with_model,
        )

    def process_import(
        self,
        session_id: str,
        raw_path: str | Path,
        original_filename: str,
        raw_import,
        *,
        device_id: str | None = None,
        vehicle_id: str | None = None,
        sample_interval_ms: float | None = None,
        sampling_rate_hz: float | None = None,
        session_note: str | None = None,
        firmware_version: str | None = None,
        client_session_id: str | None = None,
        upload_sha256: str | None = None,
        process_with_model: bool = True,
    ) -> dict[str, Any]:
        started = perf_counter()
        logger.info(
            "frames parsed",
            extra={
                "session_id": session_id,
                "frames_found": raw_import.statistics["frames_found"],
                "frame_errors": raw_import.statistics["frame_errors"],
            },
        )

        validate_raw_import_for_processing(raw_import)

        frame_data = raw_import.frame_data
        checksum_failures = int((~frame_data["checksum_valid"].fillna(False).astype(bool)).sum())
        validation_failures = int((~frame_data["is_valid"].fillna(False).astype(bool)).sum())
        logger.info("checksum failures", extra={"session_id": session_id, "checksum_failures": checksum_failures})
        logger.info("validation failures", extra={"session_id": session_id, "validation_failures": validation_failures})

        repository = getattr(self.storage, "repository", None)
        analysis = AnalysisService(self.settings, repository=repository).analyze(
            raw_import.canonical_session,
            process_with_model=process_with_model,
            persist=True,
        )
        if repository is not None:
            repository.write_raw_artifact(session_id, "ecu_raw_log", raw_path)
        feature_frame = analysis.feature_frame
        logger.info("windows generated", extra={"session_id": session_id, "windows": len(feature_frame)})
        logger.info(
            "inference completed",
            extra={
                "session_id": session_id,
                "model_loaded": analysis.summary["model_loaded"],
                "anomaly_windows": analysis.summary["anomaly_window_count"],
            },
        )

        windows_output = analysis.windows
        self.storage.write_frames(session_id, frame_data)
        self.storage.write_windows(session_id, windows_output)

        decoded_valid_frames = int(frame_data["ml_eligible"].fillna(False).astype(bool).sum())
        decoded_invalid_frames = int(raw_import.statistics["frames_parsed"] - int(frame_data["is_valid"].fillna(False).sum()))
        time_basis = determine_result_time_basis(sample_interval_ms, sampling_rate_hz, raw_import.statistics)
        created_at = datetime.now(UTC).isoformat()
        result = {
            "session_id": session_id,
            "analysis_run_id": analysis.analysis_run_id,
            "created_at": created_at,
            "status": "processed",
            "input": {
                "filename": safe_filename(original_filename),
                "device_id": device_id,
                "vehicle_id": vehicle_id,
                "sample_interval_ms": sample_interval_ms,
                "sampling_rate_hz": sampling_rate_hz,
                "session_note": session_note,
                "firmware_version": firmware_version,
                "client_session_id": client_session_id,
                "upload_sha256": upload_sha256,
                "source_format": raw_import.statistics.get("source_format"),
                "time_basis": time_basis,
            },
            "statistics": {
                **raw_import.statistics,
                "decoded_valid_frames": decoded_valid_frames,
                "decoded_invalid_frames": decoded_invalid_frames,
                "windows_generated": int(len(feature_frame)),
                "anomaly_windows": analysis.summary["anomaly_window_count"],
            },
            "result": {
                "overall_status": analysis.summary["overall_status"],
                "health_score": analysis.summary["health_score"],
                "anomaly_ratio": analysis.summary["anomaly_ratio"],
                "most_unusual_features": analysis.summary["most_unusual_features"],
                "model_loaded": analysis.summary["model_loaded"],
                "model_version": analysis.summary["model_version"],
                "feature_schema_version": analysis.summary["feature_schema_version"],
                "evidence_window_count": analysis.summary["evidence_window_count"],
                "minimum_windows_for_status": analysis.summary["minimum_windows_for_status"],
                "evidence_sufficient": analysis.summary["evidence_sufficient"],
                "warnings": analysis.summary["warnings"],
                "note": analysis.summary["note"],
            },
            "diagnostic_evidence": analysis.summary["diagnostic_evidence"],
            "artifacts": {
                "raw": str(self.storage.session_raw_dir(session_id) / "original.txt"),
                "frames": str(self.storage.session_processed_dir(session_id) / "frames.csv"),
                "windows": str(self.storage.session_processed_dir(session_id) / "windows.csv"),
                "result": str(self.storage.session_result_dir(session_id) / "result.json"),
            },
            "processing_duration_seconds": round(perf_counter() - started, 4),
        }
        self.storage.write_result(session_id, result)
        logger.info(
            "processing duration",
            extra={"session_id": session_id, "duration_seconds": result["processing_duration_seconds"]},
        )
        return result
