"""Presentation enrichment of existing RCA evidence; never executes a detector or opens a cause gate."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

VERSION = "evidence-grounded-interpretation-v1"
FAMILIES = {"rpm_": "rpm", "tps_": "throttle", "battery_voltage_": "voltage", "ect_c_": "engine_temperature", "iat_c_": "intake_temperature"}
METRICS = {
    "rpm_median": ("rpm", "rpm"), "tps_voltage_median": ("throttle", "V"),
    "tps_raw_median": ("throttle", "raw"), "battery_voltage_min": ("voltage", "V"),
    "battery_voltage_median": ("voltage", "V"), "battery_voltage_mean": ("voltage", "V"),
    "ect_c_median": ("engine_temperature", "°C"), "iat_c_median": ("intake_temperature", "°C"),
    "ect_c_first": ("engine_temperature", "°C"), "ect_c_last": ("engine_temperature", "°C"), "ect_c_delta": ("engine_temperature", "°C"),
    "iat_c_first": ("intake_temperature", "°C"), "iat_c_last": ("intake_temperature", "°C"), "iat_c_delta": ("intake_temperature", "°C"),
    "speed_median": ("speed", "km/h"), "speed_rate": ("speed", "km/h/s"),
    "speed_rate_dispersion": ("speed", "km/h/s"),
}
CHECK_ORDER = {
    "external_supply_measurement": ("first", "voltage"), "voltage_context_compare": ("next", "voltage"),
    "speed_context_review": ("first", "speed"), "raw_frame_integrity": ("next", "integrity"),
    "known_stimulus_response": ("next", "integrity"), "feature_coverage": ("first", "coverage"),
    "rpm_tps_reproduction": ("next", "rpm_tps"), "rpm_tps_repeatability": ("next", "rpm_tps"),
    "iforest_feature_review": ("next", "if"), "detector_scope_review": ("review", "scope"),
    "no_evidence_boundary": ("review", "negative"),
}


def finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def witness(case: dict, detector_id: str) -> tuple[dict, dict]:
    event = case.get("provenance", {}).get("source_event") or {}
    row = (event.get("detector_representatives") or {}).get(detector_id) or event.get("representative_window") or {}
    evidence = case.get("review_presentation", {}).get("observable_evidence") or {}
    finding = next((d for d in row.get("detector_findings", []) if d.get("detector_id") == detector_id), None)
    return row, finding or (evidence.get("detector_findings", {}).get("by_detector") or {}).get(detector_id) or {}


def measurements(values: dict, *, families: list[str] | None = None) -> list[dict]:
    return [
        {"signal": signal, "feature": key, "value": finite(values[key]), "unit": unit, "scope": "representative_window"}
        for key, (signal, unit) in METRICS.items()
        if key in values and finite(values[key]) is not None and (families is None or signal in families)
    ]


def enrich_findings(case: dict, observations: list[dict], symptoms: list[dict], checks: list[dict]) -> None:
    event = case.get("provenance", {}).get("source_event") or {}
    observable = case.get("review_presentation", {}).get("observable_evidence") or {}
    run_id = case.get("provenance", {}).get("analysis_run_id")
    for item in [*observations, *symptoms]:
        identity = item.get("observation_id") or item.get("symptom_id")
        item["finding_id"] = f"{event.get('event_id') or case.get('case_id')}:{identity}"
        item["interpretation_version"] = VERSION
        # Relationship/feature-list messages belong in technical evidence, not ahead of measured findings.
        item["presentation_role"] = "technical"
        if identity in {"obs.v2.contextual_voltage_deviation", "sym.v2.contextual_supply_voltage_deviation"}:
            row, finding = witness(case, "contextual_battery_voltage")
            raw = (finding.get("evidence") or {}).get("contextual_evidence") or {}
            top = raw.get("top_deviation") or item.get("evidence", {}).get("top_deviation") or {}
            observed, reference = finite(top.get("observed")), finite(top.get("reference_median"))
            if observed is None or reference is None or top.get("feature") not in METRICS:
                continue
            delta = observed - reference
            item.update({
                "affected_signals": ["voltage"],
                "measurements": [{"signal": "voltage", "feature": top["feature"], "value": observed, "unit": "V", "scope": "representative_window"}],
                "reference_values": [{"value": reference, "unit": "V", "kind": "observed_contextual_median", "manufacturer_specification": False}],
                "deviation": {"difference": delta, "unit": "V", "robust_z": finite(top.get("robust_z")), "threshold": finite((finding.get("detector_local_threshold") or {}).get("value")), "robust_scale": finite(top.get("robust_scale"))},
                "operating_context": raw.get("operating_context") or observable.get("operating_context"),
                "supporting_detector_ids": ["contextual_battery_voltage"],
                "limiting_evidence": ["p2.limit.reference", "p2.limit.cause"],
                "additional_evidence_needed": ["external_voltage_measurement", "physical_electrical_inspection"],
            })
            item["presentation"] = {"key": "p2.sym.voltage" if identity.startswith("sym.") else "p2.obs.voltage", "parameters": {"feature": top["feature"], "observed": observed, "reference": reference, "difference": delta, "direction": "lower" if delta < 0 else "higher" if delta > 0 else "equal"}}
            item["statement"] = (f"Supply-voltage variation relative to the observed operating context requires independent electrical verification."
                if identity.startswith("sym.") else f"{top['feature']} was {observed:g} V versus an observed reference median of {reference:g} V (difference {delta:+g} V).")
        elif identity in {"obs.v2.contextual_speed_deviation", "obs.v2.rpm_tps_feature_involvement"}:
            row, finding = witness(case, "contextual_speed")
            raw = (finding.get("evidence") or {}).get("speed_evidence") or {}
            if finding.get("execution_status") != "ok" or finding.get("applicable") is not True or finding.get("finding") != "positive" or not raw.get("applicable"):
                continue
            item.update({"affected_signals": ["speed", "rpm", "throttle"], "measurements": measurements(raw.get("signals") or {}),
                "reference_values": [], "deviation": {"robust_deviations": deepcopy(raw.get("deviations") or {}), "threshold": finite(raw.get("threshold"))},
                "operating_context": raw.get("context"), "supporting_detector_ids": ["contextual_speed"],
                "limiting_evidence": ["p2.limit.speed", "p2.limit.reference"], "additional_evidence_needed": ["independent_speed_corroboration", "timestamp_continuity", "operator_operating_context"]})
            item["presentation"] = {"key": "p2.obs.speed_relationship" if raw.get("reason") == "unusual_speed_engine_relationship" else "p2.obs.speed_change", "parameters": {}}
            item["statement"] = "Recorded speed changes or the speed/RPM/throttle relationship differ from their observed operating reference; inspect the linked window values."
        elif identity == "obs.v2.iforest_outside_reference":
            row, finding = witness(case, "isolation_forest")
            features = item.get("evidence", {}).get("most_unusual_features") or []
            families = list(dict.fromkeys(family for feature in features for prefix, family in FAMILIES.items() if feature.startswith(prefix)))
            item.update({"affected_signals": families, "measurements": measurements(row.get("telemetry") or observable.get("telemetry") or {}, families=families),
                "reference_values": [], "operating_context": row.get("operating_context") if "operating_context" in row else observable.get("operating_context"), "supporting_detector_ids": ["isolation_forest"],
                "limiting_evidence": ["p2.limit.if", "p2.limit.cause"], "additional_evidence_needed": ["feature_value_comparison", "operator_operating_context"]})
            item["presentation"] = {"key": "p2.obs.if", "parameters": {"families": families}}
            item["statement"] = "Unusual-feature involvement in " + (", ".join(families) or "measured signals") + "; listed values describe the selected window, not a diagnosed component fault."
        elif identity == "obs.v2.aggregate_evidence" and event.get("evidence_state") in {"no_evidence", "insufficient_coverage"}:
            row = event.get("representative_window") or {}
            insufficient = event.get("evidence_state") == "insufficient_coverage"
            item.update({"affected_signals": [], "measurements": [], "reference_values": [], "operating_context": observable.get("operating_context"),
                "supporting_detector_ids": [], "limiting_evidence": ["p2.limit.coverage"] if insufficient else ["p2.limit.cause"],
                "additional_evidence_needed": ["required_signal_coverage"] if insufficient else ["independent_physical_validation"],
                "presentation": {"key": "p2.obs.coverage" if insufficient else "p2.obs.negative", "parameters": {}}})
        else:
            continue
        if item.get("presentation"):
            item["presentation_role"] = "primary"
            timing = observable.get("event_timing") or {}
            item["event_reference"] = {"analysis_run_id": run_id, "event_id": event.get("event_id"), "start_time_ms": event.get("start_time_ms", timing.get("start_time_ms")), "end_time_ms": event.get("end_time_ms", timing.get("end_time_ms")), "contributing_window_count": event.get("window_count", timing.get("window_count")),
                "representative_window": {key: row.get(key) for key in ("window_number", "window_index", "start_frame_index", "end_frame_index", "start_time_ms", "end_time_ms")}, "artifact": "analysis_windows.jsonl"}
            item["window_coverage"] = deepcopy(event.get("window_coverage") or {})
            item["contradictory_detector_ids"] = list(event.get("negative_detector_ids") or [])
            if item["contradictory_detector_ids"]:
                item["limiting_evidence"].append("p2.limit.disagreement")
            if event.get("coverage_status") != "full":
                item["limiting_evidence"].append("p2.limit.coverage")
            item["signal_provenance"] = deepcopy(case.get("provenance", {}).get("signal_provenance") or {})

    for check in checks:
        short = check["check_id"].removeprefix("check.v2.")
        if short not in CHECK_ORDER:
            continue
        priority, family = CHECK_ORDER[short]
        supported = [o for o in observations if o.get("presentation_role") == "primary" and (
            family == "voltage" and "voltage" in o.get("affected_signals", []) and "contextual_battery_voltage" in o.get("supporting_detector_ids", [])
            or family == "speed" and "contextual_speed" in o.get("supporting_detector_ids", [])
            or family in {"if", "rpm_tps", "integrity"} and "isolation_forest" in o.get("supporting_detector_ids", [])
            or family in {"scope", "coverage", "negative"})]
        check.update({"diagnostic_priority": priority, "priority_basis": "evidence_and_practical_diagnostic_value", "supporting_finding_ids": [o["finding_id"] for o in supported],
            "source_event_refs": [{"event_id": event.get("event_id"), "analysis_run_id": run_id}],
            "presentation": {"key": "p2.check." + short, "parameters": {}},
            "rationale": {"key": "p2.why." + family, "parameters": {}},
            "provides": {"key": "p2.provides." + family, "parameters": {}}})


def merge_findings(results: list[dict], section: str, identity: str) -> list[dict]:
    """Keep event-specific measurements; merge repeated checks without losing their evidence links."""
    output: dict[str, dict] = {}
    for result in results:
        for raw in result.get(section, []):
            key = raw.get("finding_id") if raw.get("presentation_role") == "primary" else raw.get(identity)
            key = str(key or raw.get(identity))
            if key not in output:
                output[key] = deepcopy(raw)
            elif section == "recommended_checks":
                for field in ("supporting_finding_ids", "source_event_refs"):
                    for reference in raw.get(field, []):
                        if reference not in output[key].setdefault(field, []):
                            output[key][field].append(deepcopy(reference))
    return list(output.values())
