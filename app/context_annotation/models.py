from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ANNOTATION_SCHEMA_VERSION = "context-annotation-v1"
ANNOTATION_SCHEMA_VERSION_V2 = "context-annotation-v2"
TAXONOMY_VERSION_V2 = "context-taxonomy-v2"
RULE_VERSION = "ctx2-rules-v1"
OPERATING_VALUES = (
    "engine_off", "stationary_running", "moving_accelerating",
    "moving_decelerating", "moving_steady", "moving_variable", "unknown",
)
THERMAL_PHASE_VALUES = ("warming", "stable", "unknown")
EVENT_VALUES = ("throttle_burst", "repeated_stop_go", "engine_start", "engine_stop")
OperatingContext = Literal[
    "engine_off", "stationary_running", "moving_accelerating",
    "moving_decelerating", "moving_steady", "moving_variable", "unknown",
]
ThermalPhase = Literal["warming", "stable", "unknown"]
EventKind = Literal["throttle_burst", "repeated_stop_go", "engine_start", "engine_stop"]
CURATED_CORPUS_SCHEMA_VERSION = "context-curated-corpus-v1"
CAMPAIGN_STATUS_SCHEMA_VERSION = "context-annotation-campaign-status-v1"
FREEZE_SCHEMA_VERSION = "context-dataset-freeze-v1"
DEFAULT_DATASET_ID = "ecu-context-v1"

ENGINE_VALUES = ("off", "running", "unknown")
THROTTLE_VALUES = ("closed", "low", "mid", "high", "unknown")
DYNAMICS_VALUES = ("steady", "opening", "closing", "transition", "unknown")
THERMAL_VALUES = ("cold", "warming", "stable", "unknown")
CAPTURE_TYPE_VALUES = ("road", "stationary", "calibration", "unknown")
START_THERMAL_VALUES = ("cold", "warm", "unknown")


class ContextLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["off", "running", "unknown"] = "unknown"
    throttle: Literal["closed", "low", "mid", "high", "unknown"] = "unknown"
    dynamics: Literal["steady", "opening", "closing", "transition", "unknown"] = "unknown"
    thermal: Literal["cold", "warming", "stable", "unknown"] = "unknown"


class SessionLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_type: Literal["road", "stationary", "calibration", "unknown"] = "unknown"
    start_thermal_state: Literal["cold", "warm", "unknown"] = "unknown"
    notes: str = ""
    route_type: str | None = None
    route_notes: str = ""
    known_traffic_conditions: str | None = None


class OperatingLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operating_context: OperatingContext = "unknown"
    thermal_phase: ThermalPhase = "unknown"


class HumanConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1)
    confirmed_at: str
    corrections: dict[str, object] = Field(default_factory=dict)


class BulkEndorsement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    approval_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_at: str = Field(min_length=1)
    approval_source: Literal["data_collector"] = "data_collector"
    approval_type: Literal["bulk_operator_endorsement"] = "bulk_operator_endorsement"
    individual_interval_review: Literal[False] = False


class ReviewedProvenance(BaseModel):
    origin: Literal["human", "auto_suggested"] = "human"
    review_status: Literal["accepted"] = "accepted"
    suggestion_id: str | None = None
    rule_version: str | None = None
    evidence: dict[str, object] = Field(default_factory=dict)
    evidence_quality: Literal["strong", "limited", "insufficient"] | None = None
    reason: str = ""
    original_proposal: dict[str, object] = Field(default_factory=dict)
    human_confirmation: HumanConfirmation | None = None
    bulk_endorsement: BulkEndorsement | None = None
    confirmation_history: list[HumanConfirmation] = Field(default_factory=list)

    @model_validator(mode="after")
    def confirmed_auto_origin(self) -> "ReviewedProvenance":
        if self.origin == "auto_suggested" and not (
            (self.human_confirmation or self.bulk_endorsement) and self.suggestion_id and self.rule_version and self.original_proposal
        ):
            raise ValueError("accepted auto suggestions require human confirmation or explicit bulk endorsement and original provenance")
        return self


class SourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    sha256: str
    session_id: str | None = None
    decoder_provenance: dict[str, object] = Field(default_factory=dict)
    source_session_id: str | None = None
    derivation_type: str | None = None
    augmentation_id: str | None = None


class ContextSegment(ReviewedProvenance):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    start_s: float
    end_s: float
    labels: ContextLabels | OperatingLabels = Field(default_factory=ContextLabels)
    taxonomy_version: Literal["context-taxonomy-v1", "context-taxonomy-v2"] = "context-taxonomy-v1"
    notes: str = ""

    @field_validator("start_s", "end_s")
    @classmethod
    def finite_timestamp(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("timestamp must be finite")
        return float(value)

    @model_validator(mode="after")
    def valid_range(self) -> "ContextSegment":
        if self.start_s < 0:
            raise ValueError("start_s must be non-negative")
        if self.start_s >= self.end_s:
            raise ValueError("start_s must be less than end_s")
        if isinstance(self.labels, OperatingLabels) != (self.taxonomy_version == TAXONOMY_VERSION_V2):
            raise ValueError("segment labels must match the explicit taxonomy_version")
        return self


class ContextEvent(ReviewedProvenance):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(ge=0, allow_inf_nan=False)
    event: EventKind
    notes: str = ""

    @model_validator(mode="after")
    def valid_range(self) -> "ContextEvent":
        if self.end_s <= self.start_s:
            raise ValueError("event start_s must be less than end_s")
        return self


class ContextAnnotationDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["context-annotation-v1", "context-annotation-v2"] = ANNOTATION_SCHEMA_VERSION
    session_id: str
    source: SourceProvenance
    session_labels: SessionLabels = Field(default_factory=SessionLabels)
    segments: list[ContextSegment] = Field(default_factory=list)
    events: list[ContextEvent] = Field(default_factory=list)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def unique_segment_ids(self) -> "ContextAnnotationDocument":
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("segment IDs must be unique")
        event_ids = [event.id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event IDs must be unique")
        if self.schema_version == ANNOTATION_SCHEMA_VERSION and (
            self.events or any(isinstance(segment.labels, OperatingLabels) for segment in self.segments)
        ):
            raise ValueError("v2 labels/events require an explicit context-annotation-v2 document")
        return self


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(ge=0, allow_inf_nan=False)
    operating_context: OperatingContext | None = None
    thermal_phase: ThermalPhase | None = None
    event: EventKind | None = None
    origin: Literal["auto_suggested"] = "auto_suggested"
    rule_version: str = RULE_VERSION
    evidence: dict[str, object] = Field(default_factory=dict)
    evidence_quality: Literal["strong", "limited", "insufficient"]
    reason: str
    review_status: Literal["pending", "uncertain", "accepted", "rejected"] = "pending"
    original_proposal: dict[str, object] = Field(default_factory=dict)
    human_confirmation: HumanConfirmation | None = None
    bulk_endorsement: BulkEndorsement | None = None
    review_history: list[dict[str, object]] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def valid_proposal(self) -> "Suggestion":
        if self.bulk_endorsement is not None and self.review_status != "accepted":
            raise ValueError("bulk endorsement applies only to accepted suggestions")
        if self.end_s <= self.start_s:
            raise ValueError("suggestion start_s must be less than end_s")
        if self.event is not None:
            if self.operating_context is not None or self.thermal_phase is not None:
                raise ValueError("events and operating segments are separate")
        elif self.operating_context is None or self.thermal_phase is None:
            raise ValueError("operating suggestions require both v2 labels")
        return self


class SuggestionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["context-suggestions-v2"] = "context-suggestions-v2"
    taxonomy_version: Literal["context-taxonomy-v2"] = TAXONOMY_VERSION_V2
    session_id: str
    source: SourceProvenance
    rule_version: str = RULE_VERSION
    config: dict[str, object]
    config_sha256: str
    signal_provenance: dict[str, object]
    timestamp_integrity: dict[str, object]
    segments: list[Suggestion] = Field(default_factory=list)
    events: list[Suggestion] = Field(default_factory=list)
    metrics: dict[str, object] = Field(default_factory=dict)
    generated_at: str

    @model_validator(mode="after")
    def valid_collections(self) -> "SuggestionDocument":
        items = self.segments + self.events
        if len({item.id for item in items}) != len(items):
            raise ValueError("suggestion IDs must be unique")
        if any(s.event is not None for s in self.segments) or any(s.event is None for s in self.events):
            raise ValueError("events and segments must use separate collections")
        return self


class SignalPoint(BaseModel):
    t: float
    y: float | None


class SignalSeries(BaseModel):
    key: str
    label: str
    unit: str
    available_count: int
    total_count: int
    points: list[SignalPoint]


class SessionSource(BaseModel):
    source_id: str
    filename: str
    path: str
    sha256: str
    is_directory: bool = False
    expected_sha256: str | None = None
    manifest_session_id: str | None = None
    manifest_reason: str | None = None
    belongs_to_curated_corpus: bool = False


class CuratedCorpusEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    source_path: str
    source_sha256: str
    reason_included: str
    origin: dict[str, object] = Field(default_factory=dict)


class CuratedCorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["context-curated-corpus-v1"] = CURATED_CORPUS_SCHEMA_VERSION
    dataset_id: str = DEFAULT_DATASET_ID
    independent_captured_sessions: int
    source_root: str = "data/telemetry"
    created_at: str
    selection_rule: str
    excluded_sessions: list[dict[str, object]] = Field(default_factory=list)
    sessions: list[CuratedCorpusEntry]

    @model_validator(mode="after")
    def validate_manifest(self) -> "CuratedCorpusManifest":
        session_ids = [entry.session_id for entry in self.sessions]
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("curated corpus session IDs must be unique")
        source_hashes = [entry.source_sha256 for entry in self.sessions]
        if len(source_hashes) != len(set(source_hashes)):
            raise ValueError("curated corpus source hashes must be unique")
        if self.independent_captured_sessions != len(self.sessions):
            raise ValueError("independent_captured_sessions must equal the curated session count")
        return self


class RawCorpusManifest(CuratedCorpusManifest):
    schema_version: Literal["context-raw-corpus-v1"] = "context-raw-corpus-v1"
    independent_captured_sessions: None = None
    source_count: int
    archive_sha256: str
    capture_independence: dict[str, object]
    partition_policy: Literal["quarantine_until_physical_capture_groups_verified"] = "quarantine_until_physical_capture_groups_verified"

    @model_validator(mode="after")
    def validate_manifest(self) -> "RawCorpusManifest":
        if self.source_count != len(self.sessions):
            raise ValueError("source_count must equal the RAW input count, not an independence estimate")
        if len({entry.session_id for entry in self.sessions}) != self.source_count:
            raise ValueError("RAW session IDs must be unique")
        if len({entry.source_sha256 for entry in self.sessions}) != self.source_count:
            raise ValueError("RAW source hashes must be unique")
        return self


class AttestedRawCorpusManifest(RawCorpusManifest):
    schema_version: Literal["context-raw-corpus-v2"] = "context-raw-corpus-v2"
    operator_attested_independent_captures: int
    machine_verified_independent_captures: None = None
    operator_attestation: dict[str, str]
    operator_evaluation_policy: Literal["operator_attested_capture_groups_v1"] = "operator_attested_capture_groups_v1"

    @model_validator(mode="after")
    def validate_attested_groups(self) -> "AttestedRawCorpusManifest":
        if self.operator_attested_independent_captures != self.source_count:
            raise ValueError("operator-attested count must match the exact pinned originals")
        groups = [entry.origin.get("physical_capture_group_id") for entry in self.sessions]
        if not all(groups) or len(set(groups)) != self.source_count:
            raise ValueError("every operator-attested original needs its own physical capture group")
        if any(entry.origin.get("evaluation_eligible") or entry.origin.get("technical_capture_identity_verified")
               for entry in self.sessions):
            raise ValueError("operator attestation cannot enable the strict machine-verified policy")
        return self


class LoadedSession(BaseModel):
    source: SessionSource
    status: Literal[
        "ok",
        "parse_error",
        "unsupported",
        "missing_required_signals",
        "source_missing",
        "source_hash_mismatch",
    ]
    error: str | None = None
    session_id: str
    duration_s: float = 0.0
    sample_count: int = 0
    available_signals: dict[str, int] = Field(default_factory=dict)
    signal_units: dict[str, str] = Field(default_factory=dict)
    decoder_provenance: dict[str, object] = Field(default_factory=dict)
    capture_time: str | None = None
    telemetry_schema_version: str | None = None
    source_format: str | None = None
    research_signal_provenance: dict[str, object] = Field(default_factory=dict)


class SessionSummary(BaseModel):
    source_id: str
    filename: str
    session_id: str
    duration_s: float
    sample_count: int
    available_signals: dict[str, int]
    annotation_coverage: float
    annotated_duration_s: float
    status: str
    parse_status: str
    source_changed: bool = False
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    manifest_reason: str | None = None
    error: str | None = None
    capture_time: str | None = None


class CorpusSummary(BaseModel):
    total_sessions: int = 0
    fully_annotated: int = 0
    partially_annotated: int = 0
    unannotated: int = 0
    parse_failures: int = 0
    source_changed: int = 0
