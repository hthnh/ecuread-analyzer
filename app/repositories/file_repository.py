from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings
from app.domain.model import FEATURE_SCHEMA_VERSION, MODEL_REGISTRY_SCHEMA_VERSION
from app.domain.telemetry import CanonicalTelemetrySession
from app.utils.ids import new_artifact_id


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FileBackedRepository:
    """File-backed repository for local research, shaped like the target entities."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir / "entities"
        self.telemetry_root = settings.data_dir / "telemetry"
        self.analysis_root = settings.data_dir / "analyses"
        self.ensure_directories()

    def ensure_directories(self) -> None:
        for directory in (
            self.root / "sessions",
            self.root / "raw_artifacts",
            self.root / "decoder_versions",
            self.root / "model_versions",
            self.root / "upload_idempotency",
            self.telemetry_root,
            self.analysis_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def _upload_idempotency_path(self, device_id: str, client_session_id: str) -> Path:
        key = hashlib.sha256(f"{device_id}\0{client_session_id}".encode("utf-8")).hexdigest()
        return self.root / "upload_idempotency" / f"{key}.json"

    def read_upload_idempotency(self, device_id: str, client_session_id: str) -> dict[str, Any] | None:
        path = self._upload_idempotency_path(device_id, client_session_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_upload_idempotency(
        self,
        *,
        device_id: str,
        client_session_id: str,
        session_id: str,
        upload_sha256: str,
        original_filename: str | None,
    ) -> dict[str, Any]:
        path = self._upload_idempotency_path(device_id, client_session_id)
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        payload = {
            "id": path.stem,
            "device_id": device_id,
            "client_session_id": client_session_id,
            "session_id": session_id,
            "upload_sha256": upload_sha256,
            "original_filename": original_filename,
            "created_at": existing.get("created_at", _now()),
            "updated_at": _now(),
        }
        self._write_json(path, payload)
        return payload

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        os.replace(tmp_path, path)

    def _append_jsonl(self, path: Path, payloads: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for payload in payloads:
                handle.write(json.dumps(payload, default=_json_default) + "\n")
        os.replace(tmp_path, path)

    def upsert_decoder_version(self, session: CanonicalTelemetrySession) -> dict[str, Any]:
        decoder_key = f"{session.decoder_id}_{session.decoder_version}".replace("/", "_").replace(":", "_")
        path = self.root / "decoder_versions" / f"{decoder_key}.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        created_at = existing.get("created_at", _now())
        payload = {
            "id": decoder_key,
            "ecu_profile_id": session.ecu_profile_id,
            "decoder_id": session.decoder_id,
            "version": session.decoder_version,
            "decoder_hash": existing.get("decoder_hash"),
            "schema_version": session.telemetry_schema_version,
            "status": existing.get("status", "development"),
            "notes": existing.get("notes", "Registered from canonical telemetry provenance."),
            "created_at": created_at,
            "updated_at": _now(),
        }
        self._write_json(path, payload)
        return payload

    def upsert_session(
        self,
        session: CanonicalTelemetrySession,
        *,
        processing_status: str,
        raw_frame_count: int = 0,
        valid_frame_count: int = 0,
        invalid_frame_count: int = 0,
        checksum_error_count: int = 0,
    ) -> dict[str, Any]:
        if session.session_id is None:
            raise ValueError("session_id is required before persisting a session")

        path = self.root / "sessions" / f"{session.session_id}.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        created_at = existing.get("created_at", _now())
        payload = {
            "id": session.session_id,
            "session_id": session.session_id,
            "vehicle_id": session.vehicle_id,
            "device_id": session.device_id,
            "started_at": existing.get("started_at"),
            "ended_at": existing.get("ended_at"),
            "uploaded_at": existing.get("uploaded_at", _now()),
            "sample_interval_ms": session.sampling.sample_interval_ms,
            "sampling_rate_hz": session.sampling.sampling_rate_hz,
            "decoder_version_id": f"{session.decoder_id}_{session.decoder_version}".replace("/", "_").replace(":", "_"),
            "ecu_profile_id": session.ecu_profile_id,
            "decoder_id": session.decoder_id,
            "decoder_version": session.decoder_version,
            "telemetry_schema_version": session.telemetry_schema_version,
            "raw_frame_count": raw_frame_count,
            "valid_frame_count": valid_frame_count,
            "invalid_frame_count": invalid_frame_count,
            "checksum_error_count": checksum_error_count,
            "processing_status": processing_status,
            "status": processing_status,
            "session_note": session.session_note,
            "firmware_version": session.firmware_version,
            "source_type": session.source_type,
            "created_at": created_at,
            "updated_at": _now(),
        }
        self._write_json(path, payload)
        return payload

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        paths = sorted((self.root / "sessions").glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        return [json.loads(path.read_text(encoding="utf-8")) for path in paths[:limit]]

    def read_session(self, session_id: str) -> dict[str, Any] | None:
        path = self.root / "sessions" / f"{session_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_raw_artifact(self, session_id: str, artifact_type: str, storage_path: str | Path) -> dict[str, Any]:
        path = Path(storage_path)
        payload = {
            "id": new_artifact_id(),
            "session_id": session_id,
            "artifact_type": artifact_type,
            "storage_path": str(path),
            "sha256": file_sha256(path),
            "size_bytes": path.stat().st_size,
            "created_at": _now(),
        }
        self._write_json(self.root / "raw_artifacts" / f"{payload['id']}.json", payload)
        return payload

    def write_telemetry(self, session: CanonicalTelemetrySession) -> None:
        if session.session_id is None:
            raise ValueError("session_id is required before persisting telemetry")
        directory = self.telemetry_root / session.session_id
        metadata = {
            "session_id": session.session_id,
            "vehicle_id": session.vehicle_id,
            "device_id": session.device_id,
            "ecu_profile_id": session.ecu_profile_id,
            "decoder_id": session.decoder_id,
            "decoder_version": session.decoder_version,
            "telemetry_schema_version": session.telemetry_schema_version,
            "sampling": session.sampling.model_dump(mode="json"),
            "signal_definitions": session.signal_definitions,
            "sample_count": len(session.samples),
            "created_at": _now(),
        }
        self._write_json(directory / "metadata.json", metadata)
        self._append_jsonl(
            directory / "samples.jsonl",
            [sample.model_dump(mode="json") for sample in session.samples],
        )

    def read_telemetry_page(self, session_id: str, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
        path = self.telemetry_root / session_id / "samples.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"telemetry samples were not found for session {session_id}")
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return len(records), records[offset : offset + limit]

    def write_model_version(self, metadata: dict[str, Any]) -> dict[str, Any]:
        model_version_id = metadata.get("model_version_id") or metadata.get("version") or "unknown-model-version"
        payload = {
            "id": model_version_id,
            "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
            "model_name": metadata.get("model_name"),
            "model_family": metadata.get("model_family") or metadata.get("model_type"),
            "version": metadata.get("version") or metadata.get("model_version"),
            "artifact_path": str(self.settings.model_dir / "isolation_forest.joblib"),
            "scaler_artifact_path": str(self.settings.model_dir / "robust_scaler.joblib"),
            "artifact_sha256": (
                file_sha256(self.settings.model_dir / "isolation_forest.joblib")
                if (self.settings.model_dir / "isolation_forest.joblib").exists()
                else None
            ),
            "trained_at": metadata.get("created_at"),
            "training_session_count": len(metadata.get("training_sessions", [])),
            "training_window_count": metadata.get("training_window_count"),
            "feature_schema_version": metadata.get("feature_schema_version", FEATURE_SCHEMA_VERSION),
            "training_decoder_versions": metadata.get("training_decoder_versions", []),
            "hyperparameters_json": metadata.get("hyperparameters", {}),
            "training_metadata_json": metadata,
            "status": metadata.get("status", "active"),
            "created_at": _now(),
        }
        self._write_json(self.root / "model_versions" / f"{model_version_id}.json", payload)
        return payload

    def write_analysis_run(self, payload: dict[str, Any]) -> None:
        self._write_json(self.analysis_root / payload["id"] / "analysis_run.json", payload)

    def read_analysis(self, analysis_run_id: str) -> dict[str, Any] | None:
        path = self.analysis_root / analysis_run_id / "analysis_run.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_analyses_for_session(self, session_id: str) -> list[dict[str, Any]]:
        analyses: list[dict[str, Any]] = []
        for path in sorted(self.analysis_root.glob("*/analysis_run.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("session_id") == session_id:
                analyses.append(payload)
        return analyses

    def write_analysis_windows(self, analysis_run_id: str, windows: pd.DataFrame) -> None:
        directory = self.analysis_root / analysis_run_id
        minimal_columns = [
            "window_index",
            "start_frame_index",
            "end_frame_index",
            "duration_ms",
            "score_sample",
            "decision_score",
            "prediction",
            "is_anomaly",
            "window_health_score",
            "most_unusual_features",
            "detector_results",
        ]
        available_minimal = [column for column in minimal_columns if column in windows.columns]
        analysis_windows = windows[available_minimal].rename(
            columns={
                "start_frame_index": "start_sequence",
                "end_frame_index": "end_sequence",
            }
        )
        self._append_jsonl(
            directory / "analysis_windows.jsonl",
            analysis_windows.where(pd.notna(analysis_windows), None).to_dict(orient="records"),
        )

        feature_records: list[dict[str, Any]] = []
        metadata_columns = set(minimal_columns) | {
            "sample_count",
            "checksum_failure_ratio",
            "invalid_decoded_ratio",
        }
        for _, row in windows.iterrows():
            features_json = {
                column: row[column]
                for column in windows.columns
                if column not in metadata_columns and pd.notna(row[column])
            }
            feature_records.append(
                {
                    "window_index": int(row["window_index"]),
                    "feature_schema_version": FEATURE_SCHEMA_VERSION,
                    "features_json": features_json,
                }
            )
        self._append_jsonl(directory / "window_features.jsonl", feature_records)

    def read_analysis_windows_page(self, analysis_run_id: str, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
        path = self.analysis_root / analysis_run_id / "analysis_windows.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"analysis windows were not found for analysis {analysis_run_id}")
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return len(records), records[offset : offset + limit]
