from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


CANONICAL_TELEMETRY_SCHEMA_VERSION_V1 = "canonical-telemetry-v1"
CANONICAL_TELEMETRY_SCHEMA_VERSION_V2 = "canonical-telemetry-v2"
CANONICAL_TELEMETRY_SCHEMA_VERSION = CANONICAL_TELEMETRY_SCHEMA_VERSION_V1
SUPPORTED_TELEMETRY_SCHEMA_VERSIONS = {
    CANONICAL_TELEMETRY_SCHEMA_VERSION_V1,
    CANONICAL_TELEMETRY_SCHEMA_VERSION_V2,
}

V1_CORE_SIGNAL_COLUMNS = [
    "rpm",
    "tps_voltage",
    "tps_raw_candidate",
    "battery_voltage",
    "iat_c",
    "ect_c_candidate",
    "map_raw",
]
CORE_SIGNAL_COLUMNS = V1_CORE_SIGNAL_COLUMNS

V2_CORE_SIGNAL_COLUMNS = [
    "rpm",
    "tps_voltage",
    "tps_raw",
    "battery_voltage",
    "iat_c",
    "ect_c",
]

CANDIDATE_SIGNAL_COLUMNS = [
    "tps_raw_candidate",
    "ect_c_candidate",
    "map_raw",
]

V1_SIGNAL_DEFINITIONS: dict[str, dict[str, str]] = {
    "rpm": {"status": "high_confidence_candidate", "unit": "rpm"},
    "tps_voltage": {"status": "high_confidence_candidate", "unit": "V"},
    "tps_raw_candidate": {"status": "candidate", "unit": "raw"},
    "battery_voltage": {"status": "high_confidence_candidate", "unit": "V"},
    "iat_c": {"status": "high_confidence_candidate", "unit": "degC"},
    "ect_c_candidate": {"status": "candidate", "unit": "degC"},
    "map_raw": {"status": "candidate", "unit": "raw"},
}
SIGNAL_DEFINITIONS = V1_SIGNAL_DEFINITIONS

V2_SIGNAL_DEFINITIONS: dict[str, dict[str, str]] = {
    "rpm": {"status": "verified", "unit": "rpm"},
    "tps_voltage": {"status": "verified", "unit": "V"},
    "tps_raw": {"status": "verified_as_raw", "unit": "raw"},
    "battery_voltage": {"status": "verified", "unit": "V"},
    "iat_c": {"status": "provisional_high_confidence", "unit": "degC"},
    "ect_c": {"status": "provisional_high_confidence", "unit": "degC"},
}

REQUIRED_SIGNALS_BY_SCHEMA: dict[str, list[str]] = {
    CANONICAL_TELEMETRY_SCHEMA_VERSION_V1: V1_CORE_SIGNAL_COLUMNS,
    CANONICAL_TELEMETRY_SCHEMA_VERSION_V2: V2_CORE_SIGNAL_COLUMNS,
}


def get_required_signal_columns(telemetry_schema_version: str) -> list[str]:
    try:
        return REQUIRED_SIGNALS_BY_SCHEMA[telemetry_schema_version]
    except KeyError as exc:
        supported = ", ".join(sorted(REQUIRED_SIGNALS_BY_SCHEMA))
        raise ValueError(
            f"unsupported telemetry_schema_version {telemetry_schema_version!r}; "
            f"supported versions: {supported}"
        ) from exc


def get_signal_definitions(telemetry_schema_version: str) -> dict[str, dict[str, str]]:
    if telemetry_schema_version == CANONICAL_TELEMETRY_SCHEMA_VERSION_V2:
        return V2_SIGNAL_DEFINITIONS.copy()
    if telemetry_schema_version == CANONICAL_TELEMETRY_SCHEMA_VERSION_V1:
        return V1_SIGNAL_DEFINITIONS.copy()
    get_required_signal_columns(telemetry_schema_version)
    raise AssertionError("unreachable")


class SamplingMetadata(BaseModel):
    sample_interval_ms: float | None = Field(default=None, gt=0)
    sampling_rate_hz: float | None = Field(default=None, gt=0)

    @property
    def time_basis(self) -> str:
        if self.sample_interval_ms is not None:
            return "relative_time_ms"
        if self.sampling_rate_hz is not None:
            return "sampling_rate_hz"
        return "sample_index"


class TelemetrySample(BaseModel):
    sequence: int = Field(ge=0)
    timestamp_ms: float | None = None
    rpm: float | None = None
    tps_voltage: float | None = None
    tps_raw: float | None = None
    tps_raw_candidate: float | None = None
    battery_voltage: float | None = None
    iat_c: float | None = None
    ect_c: float | None = None
    ect_c_candidate: float | None = None
    map_raw: float | None = None
    frame_valid: bool = True
    checksum_valid: bool = True
    quality_flags: dict[str, Any] = Field(default_factory=dict)
    candidate_signals: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp_ms")
    @classmethod
    def timestamp_must_be_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("timestamp_ms must be non-negative")
        return value


class CanonicalTelemetrySession(BaseModel):
    session_id: str | None = None
    vehicle_id: str | None = None
    device_id: str | None = None
    firmware_version: str | None = None
    session_note: str | None = None
    ecu_profile_id: str | None = None
    decoder_id: str
    decoder_version: str
    telemetry_schema_version: str = CANONICAL_TELEMETRY_SCHEMA_VERSION
    sampling: SamplingMetadata = Field(default_factory=SamplingMetadata)
    samples: list[TelemetrySample]
    signal_definitions: dict[str, dict[str, str]] = Field(default_factory=lambda: SIGNAL_DEFINITIONS.copy())
    source_type: str = "canonical"

    @model_validator(mode="after")
    def require_supported_schema(self) -> "CanonicalTelemetrySession":
        if self.telemetry_schema_version not in SUPPORTED_TELEMETRY_SCHEMA_VERSIONS:
            supported = ", ".join(sorted(SUPPORTED_TELEMETRY_SCHEMA_VERSIONS))
            raise ValueError(
                f"unsupported telemetry_schema_version {self.telemetry_schema_version!r}; supported versions: {supported}"
            )
        if (
            self.telemetry_schema_version == CANONICAL_TELEMETRY_SCHEMA_VERSION_V2
            and self.signal_definitions == V1_SIGNAL_DEFINITIONS
        ):
            self.signal_definitions = V2_SIGNAL_DEFINITIONS.copy()
        return self

    @property
    def decoder_version_key(self) -> str:
        return f"{self.decoder_id}:{self.decoder_version}"

    def with_session_id(self, session_id: str) -> "CanonicalTelemetrySession":
        return self.model_copy(update={"session_id": session_id})
