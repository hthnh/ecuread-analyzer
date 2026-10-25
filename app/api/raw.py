from __future__ import annotations

import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
from time import monotonic
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from app.domain.telemetry import CanonicalTelemetrySession, SamplingMetadata
from app.ingestion.honda_0x17.adapter import (
    RAW_REPRESENTATION_LEGACY29_FF5,
    RAW_REPRESENTATION_NATIVE24_TABLE17,
    raw_records_to_canonical_session,
)
from app.services.analysis_service import AnalysisService, AnalysisTimeoutError, check_analysis_deadline
from app.utils.ids import new_analysis_run_id


MAX_RAW_RECORDS = 50_000


@contextmanager
def raw_request_slot(data_dir):
    """One RAW request across workers sharing the data volume; never queue bodies."""
    with (data_dir / ".raw-analysis.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HTTPException(429, detail={"code": "RAW_ANALYSIS_BUSY", "message": "Another RAW analysis is running. Retry with the same request key."}, headers={"Retry-After": "5"}) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class BoundedRawRoute(APIRoute):
    body_limit_setting = "raw_request_max_bytes"
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            settings = request.app.state.settings
            max_bytes = getattr(settings, self.body_limit_setting)
            with raw_request_slot(settings.data_dir):
                length = request.headers.get("content-length")
                if length is not None:
                    try:
                        if int(length) < 0:
                            raise ValueError
                    except ValueError as exc:
                        raise HTTPException(400, detail="Invalid Content-Length") from exc
                    if int(length) > max_bytes:
                        raise HTTPException(413, detail={"code": "RAW_REQUEST_TOO_LARGE", "max_bytes": max_bytes})
                body = bytearray()
                try:
                    async with asyncio.timeout(settings.raw_body_timeout_seconds):
                        async for chunk in request.stream():
                            if len(body) + len(chunk) > max_bytes:
                                raise HTTPException(413, detail={"code": "RAW_REQUEST_TOO_LARGE", "max_bytes": max_bytes})
                            body.extend(chunk)
                except TimeoutError as exc:
                    raise HTTPException(408, detail={"code": "RAW_BODY_TIMEOUT", "message": "RAW request body did not arrive within the upload time budget."}) from exc
                request._body = bytes(body)
                try:
                    return await handler(request)
                except RequestValidationError as exc:
                    # Do not echo thousands of RAW records in validation errors.
                    return JSONResponse(status_code=422, content={"detail": [
                        {key: error[key] for key in ("type", "loc", "msg") if key in error}
                        for error in exc.errors()
                    ]})

        return bounded

RawRepresentation = Literal["legacy29_ff5", "native24_table17"]

router = APIRouter(prefix="/api/v1/raw", tags=["raw"], route_class=BoundedRawRoute)


class RawDecodeAnalyzeRecord(BaseModel):
    sequence: int = Field(ge=0)
    timestamp_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    raw_hex: str = Field(min_length=1, max_length=256)


class RawDecodeAnalyzeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    device_id: str | None = Field(default=None, max_length=128)
    vehicle_id: str | None = Field(default=None, max_length=128)
    firmware_version: str | None = Field(default=None, max_length=128)
    session_note: str | None = Field(default=None, max_length=512)
    raw_representation: RawRepresentation
    sampling: SamplingMetadata = Field(default_factory=SamplingMetadata)
    records: list[RawDecodeAnalyzeRecord] = Field(min_length=1, max_length=MAX_RAW_RECORDS)
    process_with_model: bool = True
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class RawDecodeAnalyzeResponse(BaseModel):
    canonical_session: CanonicalTelemetrySession
    analysis: dict[str, Any]


@router.post("/decode-analyze", response_model=RawDecodeAnalyzeResponse, response_model_exclude_none=True)
def decode_analyze_raw_session(request: Request, payload: RawDecodeAnalyzeRequest) -> RawDecodeAnalyzeResponse:
    settings = request.app.state.settings
    repository = request.app.state.repository
    cache_path = None
    fingerprint = None
    cached = None
    reserved_run_id = None
    if payload.idempotency_key is not None:
        identity = json.dumps([payload.device_id, payload.session_id, payload.idempotency_key])
        cache_path = settings.data_dir / "raw_request_idempotency" / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
        fingerprint = hashlib.sha256(json.dumps(payload.model_dump(mode="json", exclude={"idempotency_key"}), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached["input_sha256"] != fingerprint:
                raise HTTPException(409, detail={"code": "RAW_IDEMPOTENCY_CONFLICT", "message": "This request key was already used with different RAW input."})
            if "response" in cached:
                return RawDecodeAnalyzeResponse.model_validate(cached["response"])
            reserved_run_id = cached["analysis_run_id"]
            completed = repository.read_analysis(reserved_run_id)
            if completed is not None and completed.get("request_summary"):
                response = RawDecodeAnalyzeResponse(
                    canonical_session=CanonicalTelemetrySession.model_validate(cached["canonical_session"]),
                    analysis=completed["request_summary"],
                )
                repository._write_json(cache_path, {"input_sha256": fingerprint, "response": response.model_dump(mode="json")})
                return response
    deadline = monotonic() + settings.raw_analysis_timeout_seconds
    raw_representation = str(payload.raw_representation)
    if raw_representation not in {RAW_REPRESENTATION_LEGACY29_FF5, RAW_REPRESENTATION_NATIVE24_TABLE17}:
        raise HTTPException(status_code=422, detail=f"unsupported raw_representation {raw_representation!r}")

    canonical = CanonicalTelemetrySession.model_validate(cached["canonical_session"]) if cached is not None else raw_records_to_canonical_session(
        [record.model_dump(mode="json") for record in payload.records],
        raw_representation=raw_representation,
        session_id=payload.session_id,
        device_id=payload.device_id,
        vehicle_id=payload.vehicle_id,
        sample_interval_ms=payload.sampling.sample_interval_ms,
        sampling_rate_hz=payload.sampling.sampling_rate_hz,
        session_note=payload.session_note,
        firmware_version=payload.firmware_version,
    ).canonical_session
    if cache_path is not None and cached is None:
        reserved_run_id = new_analysis_run_id()
        repository._write_json(cache_path, {
            "input_sha256": fingerprint,
            "analysis_run_id": reserved_run_id,
            "canonical_session": canonical.model_dump(mode="json"),
        })
    try:
        check_analysis_deadline(deadline, "RAW decoding")
        analysis = AnalysisService(
            settings,
            repository=repository,
        ).analyze(
            canonical,
            process_with_model=payload.process_with_model,
            persist=True,
            allow_empty_analysis=True,
            deadline=deadline,
            analysis_run_id=reserved_run_id,
        )
    except AnalysisTimeoutError as exc:
        raise HTTPException(504, detail={"code": "RAW_ANALYSIS_TIMEOUT", "message": str(exc), "budget_seconds": settings.raw_analysis_timeout_seconds}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = RawDecodeAnalyzeResponse(
        canonical_session=canonical,
        analysis=analysis.summary,
    )
    if cache_path is not None:
        repository._write_json(cache_path, {"input_sha256": fingerprint, "response": response.model_dump(mode="json")})
    return response
