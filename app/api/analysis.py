from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.domain.telemetry import CanonicalTelemetrySession
from app.schemas import PaginatedRecords
from app.services.analysis_service import AnalysisService


router = APIRouter(prefix="/api/v1", tags=["analysis"])


@router.post("/analysis")
def analyze_canonical_session(request: Request, payload: CanonicalTelemetrySession) -> dict:
    settings = request.app.state.settings
    repository = request.app.state.repository
    try:
        output = AnalysisService(settings, repository=repository).analyze(payload, process_with_model=True, persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return output.summary


@router.get("/analyses/{analysis_run_id}")
def get_analysis(request: Request, analysis_run_id: str) -> dict:
    analysis = request.app.state.repository.read_analysis(analysis_run_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"analysis {analysis_run_id} was not found")
    return analysis


@router.get("/analyses/{analysis_run_id}/windows", response_model=PaginatedRecords)
def get_analysis_windows(
    request: Request,
    analysis_run_id: str,
    limit: int = Query(default=100, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedRecords:
    try:
        total, records = request.app.state.repository.read_analysis_windows_page(
            analysis_run_id,
            limit=limit,
            offset=offset,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PaginatedRecords(session_id=analysis_run_id, limit=limit, offset=offset, count=total, records=records)

