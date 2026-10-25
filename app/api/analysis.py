from __future__ import annotations

import hashlib
import json
import re
from time import monotonic

from fastapi import APIRouter, HTTPException, Query, Request

from app.domain.telemetry import CanonicalTelemetrySession
from app.schemas import PaginatedRecords
from app.services.analysis_service import AnalysisService, AnalysisTimeoutError
from app.api.raw import BoundedRawRoute
from app.utils.ids import new_analysis_run_id


router = APIRouter(prefix="/api/v1", tags=["analysis"])


class BoundedCanonicalRoute(BoundedRawRoute):
    body_limit_setting = "max_upload_bytes"


canonical_router = APIRouter(prefix="/api/v1", tags=["analysis"], route_class=BoundedCanonicalRoute)


@canonical_router.post("/analysis")
def analyze_canonical_session(request: Request, payload: CanonicalTelemetrySession) -> dict:
    settings = request.app.state.settings
    repository = request.app.state.repository
    key = request.headers.get("Idempotency-Key")
    cache_path = None
    reserved = None
    fingerprint = None
    if key:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise HTTPException(422, detail="Invalid idempotency key")
        identity = json.dumps([payload.device_id, payload.session_id, key])
        cache_path = settings.data_dir / "canonical_request_idempotency" / (hashlib.sha256(identity.encode()).hexdigest()+".json")
        fingerprint = hashlib.sha256(json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if cached["input_sha256"] != fingerprint:
                raise HTTPException(409, detail={"code":"RAW_IDEMPOTENCY_CONFLICT"})
            if cached.get("response"):
                return cached["response"]
            reserved = cached["analysis_run_id"]
            completed = repository.read_analysis(reserved)
            if completed and completed.get("request_summary"):
                return completed["request_summary"]
        else:
            reserved = new_analysis_run_id()
            repository._write_json(cache_path, {"input_sha256":fingerprint,"analysis_run_id":reserved})
    try:
        output = AnalysisService(settings, repository=repository).analyze(payload, process_with_model=True, persist=True,
            deadline=monotonic()+settings.raw_analysis_timeout_seconds, analysis_run_id=reserved)
    except AnalysisTimeoutError as exc:
        raise HTTPException(504, detail={"code":"RAW_ANALYSIS_TIMEOUT","budget_seconds":settings.raw_analysis_timeout_seconds}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if cache_path:
        repository._write_json(cache_path, {"input_sha256":fingerprint,"analysis_run_id":reserved,"response":output.summary})
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

