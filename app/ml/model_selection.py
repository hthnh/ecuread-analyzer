"""Schema-aware artifact selection; native requests never use legacy defaults."""
from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.domain.telemetry import CANONICAL_TELEMETRY_SCHEMA_VERSION_V2
from app.ml.active_bundle import resolve_active_bundle


V2_MODEL_DIR_NAME = "honda_keihin_71_17_v2"


def model_dir_for_schema(settings: Settings, telemetry_schema_version: str) -> Path:
    if telemetry_schema_version != CANONICAL_TELEMETRY_SCHEMA_VERSION_V2:
        return settings.model_dir
    bundle = resolve_active_bundle(settings)
    if bundle is not None:
        return bundle.model_dir
    candidate = settings.model_dir / V2_MODEL_DIR_NAME
    if candidate.is_dir():
        return candidate
    metadata = settings.model_dir / "model_metadata.json"
    if metadata.is_file():
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        if isinstance(payload, dict) and payload.get("telemetry_schema_version") == CANONICAL_TELEMETRY_SCHEMA_VERSION_V2:
            return settings.model_dir
    # Missing native artifacts are an explicit model_unavailable result, not
    # permission to load the unrelated root legacy model.
    return candidate
