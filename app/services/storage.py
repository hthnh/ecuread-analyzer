from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings


SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(filename: str | None) -> str:
    if not filename:
        return "upload.txt"
    name = Path(filename).name
    name = SAFE_FILENAME_RE.sub("_", name).strip("._")
    return name or "upload.txt"


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


class StorageService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_directories()

    def session_raw_dir(self, session_id: str) -> Path:
        return self.settings.raw_dir / session_id

    def session_processed_dir(self, session_id: str) -> Path:
        return self.settings.processed_dir / session_id

    def session_result_dir(self, session_id: str) -> Path:
        return self.settings.results_dir / session_id

    @property
    def staging_dir(self) -> Path:
        return self.settings.data_dir / "staging" / "uploads"

    def save_raw_upload(self, session_id: str, filename: str | None, content: bytes) -> Path:
        if len(content) > self.settings.max_upload_bytes:
            raise ValueError(f"upload exceeds configured limit of {self.settings.max_upload_mb} MB")
        directory = self.session_raw_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        raw_path = directory / "original.txt"
        if raw_path.exists():
            raise FileExistsError(f"raw upload already exists for {session_id}")
        tmp_path = directory / ".original.txt.tmp"
        tmp_path.write_bytes(content)
        os.replace(tmp_path, raw_path)

        meta = {
            "original_filename": safe_filename(filename),
            "stored_as": "original.txt",
            "size_bytes": len(content),
        }
        self.write_json(directory / "upload_metadata.json", meta)
        return raw_path

    def stage_raw_upload(self, filename: str | None, content: bytes) -> Path:
        if len(content) > self.settings.max_upload_bytes:
            raise ValueError(f"upload exceeds configured limit of {self.settings.max_upload_mb} MB")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        directory = self.staging_dir / f"upload_{stamp}_{secrets.token_hex(6)}"
        directory.mkdir(parents=True, exist_ok=False)
        extension = Path(safe_filename(filename)).suffix or ".tmp"
        staged_path = directory / f"original{extension}"
        tmp_path = directory / f".{staged_path.name}.tmp"
        tmp_path.write_bytes(content)
        os.replace(tmp_path, staged_path)
        return staged_path

    def cleanup_staged_upload(self, staged_path: str | Path) -> None:
        path = Path(staged_path)
        shutil.rmtree(path.parent, ignore_errors=True)

    def commit_staged_upload(
        self,
        session_id: str,
        filename: str | None,
        staged_path: str | Path,
        *,
        sha256: str | None = None,
    ) -> Path:
        directory = self.session_raw_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        raw_path = directory / "original.txt"
        if raw_path.exists():
            raise FileExistsError(f"raw upload already exists for {session_id}")
        staged_path = Path(staged_path)
        staged_parent = staged_path.parent
        os.replace(staged_path, raw_path)

        meta = {
            "original_filename": safe_filename(filename),
            "stored_as": "original.txt",
            "size_bytes": raw_path.stat().st_size,
            "sha256": sha256,
        }
        self.write_json(directory / "upload_metadata.json", meta)
        shutil.rmtree(staged_parent, ignore_errors=True)
        return raw_path

    def write_frames(self, session_id: str, frame_data: pd.DataFrame) -> Path:
        directory = self.session_processed_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "frames.csv"
        tmp_path = directory / ".frames.csv.tmp"
        frame_data.to_csv(tmp_path, index=False)
        os.replace(tmp_path, path)
        return path

    def write_windows(self, session_id: str, window_data: pd.DataFrame) -> Path:
        directory = self.session_processed_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "windows.csv"
        tmp_path = directory / ".windows.csv.tmp"
        window_data.to_csv(tmp_path, index=False)
        os.replace(tmp_path, path)
        return path

    def write_result(self, session_id: str, result: dict[str, Any]) -> Path:
        directory = self.session_result_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "result.json"
        self.write_json(path, result)
        return path

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        os.replace(tmp_path, path)

    def read_result(self, session_id: str) -> dict[str, Any] | None:
        path = self.session_result_dir(session_id) / "result.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        result_paths = sorted(self.settings.results_dir.glob("*/result.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        sessions: list[dict[str, Any]] = []
        for path in result_paths[:limit]:
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            sessions.append(
                {
                    "session_id": result.get("session_id", path.parent.name),
                    "status": result.get("status", "unknown"),
                    "filename": result.get("input", {}).get("filename"),
                    "created_at": result.get("created_at"),
                    "anomaly_ratio": result.get("result", {}).get("anomaly_ratio"),
                    "health_score": result.get("result", {}).get("health_score"),
                }
            )
        return sessions

    def read_csv_page(self, session_id: str, filename: str, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
        path = self.session_processed_dir(session_id) / filename
        if not path.exists():
            raise FileNotFoundError(f"{filename} was not found for session {session_id}")
        dataframe = pd.read_csv(path)
        total = len(dataframe)
        page = dataframe.iloc[offset : offset + limit].where(pd.notna(dataframe), None)
        return total, page.to_dict(orient="records")
