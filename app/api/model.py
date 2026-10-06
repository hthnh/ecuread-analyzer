from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.ml.training import METADATA_FILENAME, model_artifacts_exist
from app.schemas import ModelInfo
from app.ingestion.raw_ecu.adapter import import_raw_text
from app.services.training_service import train_from_canonical_sessions


router = APIRouter(prefix="/api/v1", tags=["model"])


@router.get("/model", response_model=ModelInfo)
def model_info(request: Request) -> ModelInfo:
    settings = request.app.state.settings
    metadata_path = settings.model_dir / METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else None
    return ModelInfo(
        model_loaded=model_artifacts_exist(settings.model_dir),
        model_dir=str(settings.model_dir),
        metadata=metadata,
    )


@router.post("/model/train")
async def train_model_api(
    request: Request,
    file: UploadFile = File(...),
    sample_interval_ms: float | None = Form(default=None),
    sampling_rate_hz: float | None = Form(default=None),
) -> dict:
    settings = request.app.state.settings
    if not settings.allow_model_training_api:
        raise HTTPException(status_code=403, detail="model training API is disabled")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    raw_import = import_raw_text(
        content.decode("utf-8", errors="replace"),
        settings,
        session_id="api-training-upload",
        sample_interval_ms=sample_interval_ms,
        sampling_rate_hz=sampling_rate_hz,
    )
    if raw_import.statistics["frames_found"] == 0:
        raise HTTPException(status_code=422, detail="no RAW frames found in upload")
    metadata = train_from_canonical_sessions(
        [raw_import.canonical_session],
        [file.filename or "api-upload"],
        settings,
        repository=getattr(request.app.state, "repository", None),
    )
    return {
        "status": "trained",
        "frames_found": raw_import.statistics["frames_found"],
        "windows_generated": metadata["training_window_count"],
        "feature_count": len(metadata["feature_names"]),
        "model_dir": str(settings.model_dir),
    }
