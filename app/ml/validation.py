from __future__ import annotations

import math

from app.config import Settings
from app.domain.model import FEATURE_SCHEMA_VERSION
from app.domain.telemetry import CanonicalTelemetrySession, get_required_signal_columns


class MLInputValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def validate_canonical_session_for_ml(
    session: CanonicalTelemetrySession,
    settings: Settings,
    *,
    feature_schema_version: str = FEATURE_SCHEMA_VERSION,
) -> list[str]:
    errors: list[str] = []
    if feature_schema_version != FEATURE_SCHEMA_VERSION:
        errors.append(
            f"unsupported feature_schema_version {feature_schema_version!r}; "
            f"expected {FEATURE_SCHEMA_VERSION!r}"
        )

    if not session.samples:
        errors.append("samples must not be empty")
        raise MLInputValidationError(errors)

    if len(session.samples) < settings.window_size_samples:
        errors.append(
            f"insufficient samples: need at least {settings.window_size_samples}, got {len(session.samples)}"
        )

    sequences = [sample.sequence for sample in session.samples]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        errors.append("sample sequence values must be unique and monotonic increasing")

    timestamps = [sample.timestamp_ms for sample in session.samples if sample.timestamp_ms is not None]
    if timestamps and any(right < left for left, right in zip(timestamps, timestamps[1:], strict=False)):
        errors.append("timestamp_ms values must be monotonic increasing")

    eligible_samples = [sample for sample in session.samples if sample.frame_valid and sample.checksum_valid]
    if len(eligible_samples) < settings.window_size_samples:
        errors.append(
            f"insufficient ML-eligible samples: need at least {settings.window_size_samples}, "
            f"got {len(eligible_samples)}"
        )

    if eligible_samples:
        for signal_name in get_required_signal_columns(session.telemetry_schema_version):
            missing_count = 0
            for sample in eligible_samples:
                value = getattr(sample, signal_name)
                if _is_missing(value):
                    missing_count += 1
                    continue
                if not isinstance(value, int | float) or not math.isfinite(float(value)):
                    errors.append(f"{signal_name} must be finite numeric when present")
                    break
            if missing_count == len(eligible_samples):
                errors.append(f"required signal {signal_name!r} is missing from all eligible samples")
            elif missing_count:
                missing_ratio = missing_count / len(eligible_samples)
                errors.append(
                    f"required signal {signal_name!r} has missing_ratio={missing_ratio:.3f}; "
                    "current feature schema requires complete eligible samples"
                )

    if errors:
        raise MLInputValidationError(errors)
    return []
