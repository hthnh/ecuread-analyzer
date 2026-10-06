from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import PaginatedRecords


router = APIRouter(prefix="/api/v1", tags=["sessions"])


@router.get("/sessions")
def list_sessions(request: Request, limit: int = Query(default=20, ge=1, le=200)) -> dict:
    repository = getattr(request.app.state, "repository", None)
    if repository is not None:
        return {"sessions": repository.list_sessions(limit=limit)}
    storage = request.app.state.storage
    return {"sessions": storage.list_sessions(limit=limit)}


@router.get("/sessions/{session_id}/frames", response_model=PaginatedRecords)
def get_session_frames(
    request: Request,
    session_id: str,
    limit: int = Query(default=100, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedRecords:
    storage = request.app.state.storage
    try:
        total, records = storage.read_csv_page(session_id, "frames.csv", limit=limit, offset=offset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PaginatedRecords(session_id=session_id, limit=limit, offset=offset, count=total, records=records)


@router.get("/sessions/{session_id}/windows", response_model=PaginatedRecords)
def get_session_windows(
    request: Request,
    session_id: str,
    limit: int = Query(default=100, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedRecords:
    storage = request.app.state.storage
    try:
        total, records = storage.read_csv_page(session_id, "windows.csv", limit=limit, offset=offset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PaginatedRecords(session_id=session_id, limit=limit, offset=offset, count=total, records=records)


@router.get("/sessions/{session_id}")
def get_session(request: Request, session_id: str) -> dict:
    storage = request.app.state.storage
    result = storage.read_result(session_id)
    if result is not None:
        return result

    repository = getattr(request.app.state, "repository", None)
    if repository is not None:
        session = repository.read_session(session_id)
        if session is not None:
            return session
    raise HTTPException(status_code=404, detail=f"session {session_id} was not found")


@router.get("/sessions/{session_id}/telemetry", response_model=PaginatedRecords)
def get_session_telemetry(
    request: Request,
    session_id: str,
    limit: int = Query(default=100, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedRecords:
    try:
        total, records = request.app.state.repository.read_telemetry_page(session_id, limit=limit, offset=offset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PaginatedRecords(session_id=session_id, limit=limit, offset=offset, count=total, records=records)


@router.get("/sessions/{session_id}/analyses")
def get_session_analyses(request: Request, session_id: str) -> dict:
    repository = request.app.state.repository
    if repository.read_session(session_id) is None and request.app.state.storage.read_result(session_id) is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} was not found")
    return {"session_id": session_id, "analyses": repository.list_analyses_for_session(session_id)}
