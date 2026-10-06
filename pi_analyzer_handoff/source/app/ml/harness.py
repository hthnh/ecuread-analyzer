from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import pandas as pd


DetectorStatus = Literal["ok", "skipped", "failed"]


@dataclass(frozen=True, slots=True)
class DetectorIdentity:
    detector_id: str
    model_name: str
    model_version: str | None
    model_family: str | None = None


@dataclass(frozen=True, slots=True)
class DetectorContext:
    telemetry_schema_version: str | None = None
    signal_columns: tuple[str, ...] | None = None
    minimum_windows_for_status: int | None = None


@dataclass(slots=True)
class DetectorResult:
    identity: DetectorIdentity
    status: DetectorStatus
    required_features: tuple[str, ...]
    windows: pd.DataFrame
    enabled: bool = True
    reason: str | None = None
    missing_features: tuple[str, ...] = ()
    anomaly_score_column: str | None = None
    score_direction: str | None = None
    prediction_column: str | None = None
    is_anomaly_column: str | None = None
    evidence_columns: tuple[str, ...] = ()
    scored_window_count: int = 0
    anomaly_window_count: int | None = None
    anomaly_ratio: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None

    def to_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "detector_id": self.identity.detector_id,
            "model_name": self.identity.model_name,
            "model_family": self.identity.model_family,
            "model_version": self.identity.model_version,
            "enabled": self.enabled,
            "status": self.status,
            "reason": self.reason,
            "required_features": list(self.required_features),
            "missing_features": list(self.missing_features),
            "scored_window_count": self.scored_window_count,
            "anomaly_window_count": self.anomaly_window_count,
            "anomaly_ratio": self.anomaly_ratio,
            "anomaly_score": {
                "column": self.anomaly_score_column,
                "direction": self.score_direction,
            },
            "prediction": {
                "column": self.prediction_column,
                "is_anomaly_column": self.is_anomaly_column,
            },
            "available_evidence": list(self.evidence_columns),
            "warnings": list(self.warnings),
        }
        if self.error_type or self.error_message:
            summary["error"] = {
                "type": self.error_type,
                "message": self.error_message,
            }
        return summary

    def window_payload(self, row_number: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "detector_id": self.identity.detector_id,
            "model_version": self.identity.model_version,
            "status": self.status,
            "reason": self.reason,
            "prediction": None,
            "is_anomaly": None,
            "anomaly_score": None,
            "evidence": {},
        }
        if self.missing_features:
            payload["missing_features"] = list(self.missing_features)
        if self.status != "ok" or row_number >= len(self.windows):
            return payload

        row = self.windows.iloc[row_number]
        if self.prediction_column and self.prediction_column in row:
            payload["prediction"] = _json_safe_value(row[self.prediction_column])
        if self.is_anomaly_column and self.is_anomaly_column in row:
            payload["is_anomaly"] = bool(row[self.is_anomaly_column])
        if self.anomaly_score_column and self.anomaly_score_column in row:
            payload["anomaly_score"] = _json_safe_value(row[self.anomaly_score_column])

        evidence: dict[str, Any] = {}
        for column in self.evidence_columns:
            if column in row:
                evidence[column] = _json_safe_value(row[column])
        payload["evidence"] = evidence
        return payload


class Detector(Protocol):
    @property
    def identity(self) -> DetectorIdentity:
        ...

    @property
    def enabled(self) -> bool:
        ...

    @property
    def required_features(self) -> tuple[str, ...]:
        ...

    def infer(self, feature_frame: pd.DataFrame, context: DetectorContext) -> DetectorResult:
        ...


@dataclass(slots=True)
class HarnessResult:
    results: list[DetectorResult]

    def summaries(self) -> list[dict[str, Any]]:
        return [result.to_summary() for result in self.results]

    def result_for(self, detector_id: str) -> DetectorResult | None:
        for result in self.results:
            if result.identity.detector_id == detector_id:
                return result
        return None

    def window_payloads(self, window_count: int) -> list[list[dict[str, Any]]]:
        return [
            [result.window_payload(row_number) for result in self.results]
            for row_number in range(window_count)
        ]


class ModelHarness:
    def __init__(self, detectors: list[Detector] | None = None):
        self._detectors: list[Detector] = []
        for detector in detectors or []:
            self.register(detector)

    def register(self, detector: Detector) -> None:
        self._detectors.append(detector)

    def run(self, feature_frame: pd.DataFrame, context: DetectorContext | None = None) -> HarnessResult:
        resolved_context = context or DetectorContext()
        results: list[DetectorResult] = []
        for detector in self._detectors:
            required_features = detector.required_features
            if not detector.enabled:
                results.append(
                    DetectorResult(
                        identity=detector.identity,
                        status="skipped",
                        enabled=False,
                        reason="disabled",
                        required_features=required_features,
                        windows=feature_frame.copy(),
                    )
                )
                continue

            missing = tuple(feature for feature in required_features if feature not in feature_frame.columns)
            if missing:
                results.append(
                    DetectorResult(
                        identity=detector.identity,
                        status="skipped",
                        reason="missing_features",
                        required_features=required_features,
                        missing_features=missing,
                        windows=feature_frame.copy(),
                    )
                )
                continue

            try:
                results.append(detector.infer(feature_frame, resolved_context))
            except Exception as exc:  # noqa: BLE001 - failures are isolated per detector.
                results.append(
                    DetectorResult(
                        identity=detector.identity,
                        status="failed",
                        reason="exception",
                        required_features=required_features,
                        windows=feature_frame.copy(),
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
        return HarnessResult(results=results)


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if hasattr(value, "item"):
        return value.item()
    if pd.isna(value):
        return None
    return value
