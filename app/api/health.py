from __future__ import annotations

from fastapi import APIRouter, Request

from app.ml.training import model_artifacts_exist
from app.schemas import HealthResponse


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        model_loaded=model_artifacts_exist(settings.model_dir),
    )

