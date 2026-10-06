from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    model_loaded: bool


class SessionInput(BaseModel):
    filename: str
    device_id: str | None = None
    vehicle_id: str | None = None
    sample_interval_ms: float | None = None
    sampling_rate_hz: float | None = None
    session_note: str | None = None
    firmware_version: str | None = None
    time_basis: str


class SessionStatistics(BaseModel):
    total_lines: int = 0
    frames_found: int = 0
    frames_parsed: int = 0
    frame_errors: int = 0
    checksum_valid_frames: int = 0
    checksum_invalid_frames: int = 0
    decoded_valid_frames: int = 0
    decoded_invalid_frames: int = 0
    windows_generated: int = 0
    anomaly_windows: int = 0


class SessionResultSummary(BaseModel):
    overall_status: str
    health_score: float | None = Field(default=None, ge=0, le=100)
    anomaly_ratio: float = Field(default=0.0, ge=0)
    most_unusual_features: list[str] = Field(default_factory=list)
    model_loaded: bool = False
    evidence_window_count: int | None = Field(default=None, ge=0)
    minimum_windows_for_status: int | None = Field(default=None, ge=0)
    evidence_sufficient: bool | None = None
    note: str


class SessionResponse(BaseModel):
    session_id: str
    status: str
    input: SessionInput
    statistics: SessionStatistics
    result: SessionResultSummary


class SessionListItem(BaseModel):
    session_id: str
    status: str
    filename: str | None = None
    created_at: str | None = None
    anomaly_ratio: float | None = None
    health_score: float | None = None


class PaginatedRecords(BaseModel):
    session_id: str
    limit: int
    offset: int
    count: int
    records: list[dict[str, Any]]


class ModelInfo(BaseModel):
    model_loaded: bool
    model_dir: str
    metadata: dict[str, Any] | None = None
