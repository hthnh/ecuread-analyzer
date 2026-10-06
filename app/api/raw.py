from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import Settings
from app.domain.telemetry import CanonicalTelemetrySession, SamplingMetadata
from app.ingestion.honda_0x17.adapter import (
    RAW_REPRESENTATION_LEGACY29_FF5,
    RAW_REPRESENTATION_NATIVE24_TABLE17,
    raw_records_to_canonical_session,
)
from app.services.analysis_service import AnalysisService


MAX_RAW_RECORDS = 5000
V2_MODEL_DIR_NAME = "honda_keihin_71_17_v2"

RawRepresentation = Literal["legacy29_ff5", "native24_table17"]

router = APIRouter(prefix="/api/v1/raw", tags=["raw"])


class RawDecodeAnalyzeRecord(BaseModel):
    sequence: int = Field(ge=0)
    timestamp_ms: float | None = Field(default=None, ge=0)
    raw_hex: str = Field(min_length=1, max_length=256)


class RawDecodeAnalyzeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    device_id: str | None = Field(default=None, max_length=128)
    vehicle_id: str | None = Field(default=None, max_length=128)
    firmware_version: str | None = Field(default=None, max_length=128)
    session_note: str | None = Field(default=None, max_length=512)
    raw_representation: RawRepresentation
    sampling: SamplingMetadata = Field(default_factory=SamplingMetadata)
    records: list[RawDecodeAnalyzeRecord] = Field(min_length=1, max_length=MAX_RAW_RECORDS)
    process_with_model: bool = True


class RawDecodeAnalyzeResponse(BaseModel):
    canonical_session: CanonicalTelemetrySession
    analysis: dict[str, Any]


def _v2_model_dir(settings: Settings) -> Path:
    candidate = settings.model_dir / V2_MODEL_DIR_NAME
    if candidate.exists():
        return candidate
    return settings.model_dir


@router.post("/decode-analyze", response_model=RawDecodeAnalyzeResponse, response_model_exclude_none=True)
def decode_analyze_raw_session(request: Request, payload: RawDecodeAnalyzeRequest) -> RawDecodeAnalyzeResponse:
    settings = request.app.state.settings
    repository = request.app.state.repository
    raw_representation = str(payload.raw_representation)
    if raw_representation not in {RAW_REPRESENTATION_LEGACY29_FF5, RAW_REPRESENTATION_NATIVE24_TABLE17}:
        raise HTTPException(status_code=422, detail=f"unsupported raw_representation {raw_representation!r}")

    raw_import = raw_records_to_canonical_session(
        [record.model_dump(mode="json") for record in payload.records],
        raw_representation=raw_representation,
        session_id=payload.session_id,
        device_id=payload.device_id,
        vehicle_id=payload.vehicle_id,
        sample_interval_ms=payload.sampling.sample_interval_ms,
        sampling_rate_hz=payload.sampling.sampling_rate_hz,
        session_note=payload.session_note,
        firmware_version=payload.firmware_version,
    )
    try:
        analysis = AnalysisService(
            settings,
            repository=repository,
            model_dir=_v2_model_dir(settings),
        ).analyze(
            raw_import.canonical_session,
            process_with_model=payload.process_with_model,
            persist=True,
            allow_empty_analysis=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RawDecodeAnalyzeResponse(
        canonical_session=raw_import.canonical_session,
        analysis=analysis.summary,
    )
