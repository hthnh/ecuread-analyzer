from __future__ import annotations

from fastapi import APIRouter, Request

from app.ml.training import model_artifacts_exist
from app.ml.active_bundle import resolve_active_bundle
from app.schemas import HealthResponse


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    bundle = resolve_active_bundle(settings)
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        model_loaded=model_artifacts_exist(bundle.model_dir if bundle else settings.model_dir),
    )

