from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Iterable

from app.audit.byte_statistics import quantile


@dataclass(slots=True)
class PlausibilityIssue:
    field: str
    severity: str
    message: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _candidate_values(records: list[dict[str, Any]], field_name: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = record.get("candidate", {}).get(field_name)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def _jump_count(values: list[float], threshold: float) -> int:
    return sum(1 for index in range(1, len(values)) if abs(values[index] - values[index - 1]) > threshold)


def evaluate_plausibility(records: Iterable[dict[str, Any]]) -> list[PlausibilityIssue]:
    usable = [record for record in records if record.get("candidate")]
    issues: list[PlausibilityIssue] = []
    if not usable:
        return [
            PlausibilityIssue(
                field="dataset",
                severity="error",
                message="no candidate-decoded records available for plausibility checks",
                count=0,
            )
        ]

    rpm = _candidate_values(usable, "rpm")
    if any(value < 0 or value > 16000 for value in rpm):
        issues.append(
            PlausibilityIssue(
                field="rpm",
                severity="error",
                message="RPM outside 0-16000 physical plausibility range",
                count=sum(1 for value in rpm if value < 0 or value > 16000),
            )
        )
    rpm_jumps = _jump_count(rpm, 3500)
    if rpm_jumps:
        issues.append(
            PlausibilityIssue(
                field="rpm",
                severity="warning",
                message="large one-frame RPM jumps detected; flagged but not discarded",
                count=rpm_jumps,
            )
        )

    tps = _candidate_values(usable, "tps_voltage_candidate")
    if any(value < 0 or value > 5 for value in tps):
        issues.append(
            PlausibilityIssue(
                field="tps_voltage",
                severity="error",
                message="TPS voltage outside 0-5 V range",
                count=sum(1 for value in tps if value < 0 or value > 5),
            )
        )

    battery = _candidate_values(usable, "battery_v_candidate")
    if any(value < 6 or value > 18 for value in battery):
        issues.append(
            PlausibilityIssue(
                field="battery",
                severity="error",
                message="battery voltage outside 6-18 V range",
                count=sum(1 for value in battery if value < 6 or value > 18),
            )
        )

    iat = _candidate_values(usable, "iat_c_candidate")
    if any(value < -40 or value > 120 for value in iat):
        issues.append(
            PlausibilityIssue(
                field="iat",
                severity="warning",
                message="IAT candidate outside broad plausible temperature range",
                count=sum(1 for value in iat if value < -40 or value > 120),
            )
        )
    iat_jumps = _jump_count(iat, 8)
    if iat_jumps:
        issues.append(
            PlausibilityIssue(
                field="iat",
                severity="warning",
                message="IAT candidate has one-frame jumps that need environment validation",
                count=iat_jumps,
            )
        )

    ect_unavailable = sum(
        1 for record in usable if record.get("candidate", {}).get("ect_status") == "unavailable_or_unsupported"
    )
    if ect_unavailable:
        issues.append(
            PlausibilityIssue(
                field="ect",
                severity="warning",
                message="ECT candidate pair is FF/FF sentinel in records; channel is unavailable or unsupported in these logs",
                count=ect_unavailable,
            )
        )

    map_voltage = _candidate_values(usable, "map_voltage_candidate")
    if any(value < 0 or value > 5 for value in map_voltage):
        issues.append(
            PlausibilityIssue(
                field="map",
                severity="error",
                message="MAP voltage candidate outside 0-5 V range",
                count=sum(1 for value in map_voltage if value < 0 or value > 5),
            )
        )
    if map_voltage:
        issues.append(
            PlausibilityIssue(
                field="map",
                severity="info",
                message="MAP engineering byte is not assumed to be kPa; scale remains unverified",
                count=len(map_voltage),
            )
        )

    speed = _candidate_values(usable, "speed_or_signal_raw_candidate")
    if speed:
        zeroish_rpm_speed = [
            value
            for value, rpm_value in zip(speed, rpm, strict=False)
            if rpm_value < 100
        ]
        if zeroish_rpm_speed and fmean(zeroish_rpm_speed) > 10:
            issues.append(
                PlausibilityIssue(
                    field="speed_or_signal",
                    severity="warning",
                    message="byte 18 remains non-zero at RPM near zero; do not call it speed without ground truth",
                    count=len(zeroish_rpm_speed),
                )
            )

    injector = _candidate_values(usable, "injector_raw_candidate")
    if injector and max(injector) > 0:
        issues.append(
            PlausibilityIssue(
                field="injector",
                severity="info",
                message=(
                    "injector raw varies with operation, but ms scaling is unverified; "
                    f"raw p95={quantile(injector, 0.95):.1f}"
                ),
                count=len(injector),
            )
        )

    return issues


def readiness_gate(
    *,
    quality_summary: dict[str, Any],
    formula_matches: list[dict[str, Any]],
    profile_id: str,
) -> dict[str, Any]:
    reasons: list[str] = []

    total_valid = int(quality_summary.get("valid_frames", 0))
    total_records = int(quality_summary.get("records_seen", 0))
    checksum_failures = int(quality_summary.get("checksum_failures", 0))
    header_failures = int(quality_summary.get("header_failures", 0))
    length_failures = int(quality_summary.get("length_failures", 0))
    malformed_records = int(quality_summary.get("malformed_records", 0))
    time_regressions = int(quality_summary.get("time_regressions", 0))
    checksum_failure_ratio = checksum_failures / total_records if total_records else 1.0

    if total_valid == 0:
        reasons.append("no structurally valid frames were available")
    if length_failures or header_failures:
        reasons.append("raw framing is not fully consistent")
    if checksum_failure_ratio > 0.01:
        reasons.append(f"checksum failure ratio is excessive ({checksum_failure_ratio:.4%})")
    if not profile_id:
        reasons.append("decoder profile is not identified")
    if malformed_records:
        reasons.append(f"malformed input records were found ({malformed_records})")
    if time_regressions:
        reasons.append(f"device_time_ms regressions were found ({time_regressions})")

    match_by_field = {row["decoded_field"]: row for row in formula_matches}
    iat_match = match_by_field.get("iat_c")
    ect_match = match_by_field.get("ect_c")
    if iat_match and iat_match.get("best_raw_source") == "byte 12":
        reasons.append("existing iat_c is sourced from byte 12, which this profile treats as MAP voltage candidate")
    if ect_match and ect_match.get("best_raw_source") == "byte 13":
        reasons.append("existing ect_c is sourced from byte 13, which this profile treats as MAP engineering raw candidate")

    reasons.append("vehicle/ECU identification metadata and ground-truth sensor references are missing")
    reasons.append("temperature and sensor offsets remain ambiguous until controlled validation")
    reasons.append("trusted feature fields for Isolation Forest have not been approved under the new profile")

    return {
        "ready_for_ml": False if reasons else True,
        "reasons": reasons,
    }

