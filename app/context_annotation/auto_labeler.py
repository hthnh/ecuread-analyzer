"""Deterministic temporal rules for researcher-reviewed ECU context proposals."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.context_annotation.models import RULE_VERSION, SourceProvenance, Suggestion, SuggestionDocument
from app.context_annotation.session_loader import LoadedTelemetrySession
from app.context_annotation.speed import SpeedResolver, finite
from app.context_annotation.storage import utc_now_iso


@dataclass(frozen=True)
class RuleConfig:
    # Speed is an integer-byte candidate: thresholds exceed one quantization step.
    stationary_speed_kmh: float = 1.0
    moving_enter_kmh: float = 3.0
    engine_running_rpm: float = 400.0
    idle_region_low_rpm: float = 1000.0
    idle_region_high_rpm: float = 2200.0
    smoothing_s: float = 1.0
    speed_trend_window_s: float = 6.0
    acceleration_kmh_s: float = 0.7
    speed_trend_delta_kmh: float = 3.0
    steady_window_s: float = 8.0
    steady_spread_kmh: float = 3.0
    minimum_segment_s: float = 4.0
    engine_persistence_s: float = 3.0
    gap_min_s: float = 3.0
    gap_interval_multiplier: float = 8.0
    maximum_speed_rate_kmh_s: float = 35.0
    thermal_window_s: float = 60.0
    thermal_minimum_window_s: float = 20.0
    thermal_persistence_s: float = 20.0
    warming_rate_c_s: float = 0.05
    warming_delta_c: float = 2.0
    stable_rate_c_s: float = 0.03
    stable_spread_c: float = 3.0
    tps_opening_window_s: float = 2.0
    tps_burst_delta_v: float = 0.30
    tps_burst_rate_v_s: float = 0.35
    tps_burst_above_baseline_v: float = 0.40
    tps_burst_delta_raw: float = 20.0
    tps_burst_rate_raw_s: float = 20.0
    tps_burst_above_baseline_raw: float = 25.0
    tps_burst_hold_s: float = 0.5
    tps_event_cooldown_s: float = 3.0
    stop_go_window_s: float = 180.0
    stop_go_cycles: int = 3

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"rule threshold {key} must be finite and positive")
        if self.moving_enter_kmh <= self.stationary_speed_kmh:
            raise ValueError("moving entry must exceed stationary threshold")
        if self.stop_go_cycles < 2:
            raise ValueError("stop_go_cycles must be at least two")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp_integrity(loaded: LoadedTelemetrySession, config: RuleConfig) -> tuple[np.ndarray, dict[str, Any]]:
    raw = [finite(sample.get("timestamp_ms")) for sample in loaded.samples]
    basis = "captured_timestamp_ms"
    if all(t is None for t in raw) and loaded.canonical_session is not None:
        sampling = loaded.canonical_session.sampling
        dt = sampling.sample_interval_ms
        if dt is None and sampling.sampling_rate_hz:
            dt = 1000.0 / sampling.sampling_rate_hz
        if dt is not None and dt > 0:
            raw = [i * dt for i in range(len(raw))]
            basis = "declared_sampling_interval"
    if not raw or any(t is None for t in raw):
        raise ValueError("timestamp integrity: missing timestamps without a complete declared sampling basis")
    times = (np.asarray(raw, dtype=float) - raw[0]) / 1000.0
    delta = np.diff(times)
    if not np.all(delta > 0):
        raise ValueError("timestamp integrity: duplicate or non-increasing timestamps; no sorting or gap interpolation")
    median_dt = float(np.median(delta)) if len(delta) else 0.0
    gap_limit = max(config.gap_min_s, median_dt * config.gap_interval_multiplier)
    gaps = [{"start_s": float(times[i]), "end_s": float(times[i + 1])}
            for i, d in enumerate(delta) if d > gap_limit]
    return times, {"status": "valid", "basis": basis, "median_interval_s": median_dt,
                   "gap_threshold_s": gap_limit, "capture_gaps": gaps,
                   "gap_duration_s": sum(g["end_s"] - g["start_s"] for g in gaps)}


def _array(samples: list[dict], fields: tuple[str, ...]) -> np.ndarray:
    values = []
    for sample in samples:
        valid = sample.get("frame_valid", True) and sample.get("checksum_valid", True)
        value = next((finite(sample.get(k)) for k in fields if finite(sample.get(k)) is not None), None) if valid else None
        values.append(np.nan if value is None else value)
    return np.asarray(values, dtype=float)


def _smooth(values: np.ndarray, times: np.ndarray, seconds: float) -> np.ndarray:
    left = np.searchsorted(times, times - seconds / 2, side="right")
    right = np.searchsorted(times, times + seconds / 2, side="right")
    smoothed = pd.Series(values).rolling(_TimeBounds(left, right), min_periods=1).median().to_numpy()
    smoothed[~np.isfinite(values)] = np.nan
    return smoothed


class _TimeBounds(pd.api.indexers.BaseIndexer):
    def __init__(self, left: np.ndarray, right: np.ndarray):
        super().__init__()
        self.left, self.right = left, right

    def get_window_bounds(self, num_values=0, min_periods=None, center=None, closed=None, step=None):
        return self.left, self.right


def _window_trends(values: np.ndarray, times: np.ndarray, seconds: float, minimum_span: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    missing = np.full(len(times), np.nan)
    valid_indices = np.flatnonzero(np.isfinite(values))
    if len(valid_indices) < 3:
        return missing.copy(), missing.copy(), missing.copy()
    valid_times = times[valid_indices]
    left = np.searchsorted(times, times - seconds / 2)
    right = np.searchsorted(times, times + seconds / 2, side="right")
    first = np.searchsorted(valid_times, times - seconds / 2)
    last = np.searchsorted(valid_times, times + seconds / 2, side="right")
    counts = last - first
    thirds = np.maximum(1, counts // 3)

    def bounds(a, b):
        start = valid_indices[np.clip(a, 0, len(valid_indices) - 1)].copy()
        end = (valid_indices[np.clip(b - 1, 0, len(valid_indices) - 1)] + 1).copy()
        start[counts == 0] = 0
        end[counts == 0] = 0
        return _TimeBounds(start, end)

    first_bounds, last_bounds = bounds(first, first + thirds), bounds(last - thirds, last)
    y = pd.Series(values)
    observed_times = pd.Series(np.where(np.isfinite(values), times, np.nan))
    change = y.rolling(last_bounds, min_periods=1).median() - y.rolling(first_bounds, min_periods=1).median()
    span = observed_times.rolling(last_bounds, min_periods=1).median() - observed_times.rolling(first_bounds, min_periods=1).median()
    full = y.rolling(_TimeBounds(left, right), min_periods=3)
    spread = full.quantile(.9) - full.quantile(.1)
    observed_span = valid_times[np.clip(last - 1, 0, len(valid_times) - 1)] - valid_times[np.clip(first, 0, len(valid_times) - 1)]
    sufficient = (counts >= 3) & (observed_span >= minimum_span) & (counts / np.maximum(1, right - left) >= .8)
    rate = (change / span).to_numpy()
    change, spread = change.to_numpy(), spread.to_numpy()
    for result in (rate, change, spread):
        result[~sufficient] = np.nan
    return rate, change, spread


def _runs(states: list[str], times: np.ndarray, end_s: float) -> list[tuple[float, float, str]]:
    if not states:
        return []
    runs = []
    start, current = float(times[0]), states[0]
    for i in range(1, len(states)):
        if states[i] != current:
            boundary = float((times[i - 1] + times[i]) / 2)
            if boundary > start:
                runs.append((start, boundary, current))
            start, current = boundary, states[i]
    if end_s > start:
        runs.append((start, end_s, current))
    return runs


def _persist(runs: list[tuple[float, float, str]], minimum_s: float) -> list[tuple[float, float, str]]:
    output = []
    for start, end, state in runs:
        if end - start < minimum_s - 1e-9 and state != "unknown":
            state = "moving_variable" if state.startswith("moving_") else "unknown"
        if output and output[-1][2] == state and abs(output[-1][1] - start) < 1e-9:
            output[-1] = (output[-1][0], end, state)
        else:
            output.append((start, end, state))
    # Short motion transitions are folded into a mixed interval, keeping evidence gaps intact.
    i = 0
    while i < len(output):
        start, end, state = output[i]
        if state.startswith("moving_") and end - start < minimum_s - 1e-9:
            neighbors = [j for j in (i - 1, i + 1) if 0 <= j < len(output) and output[j][2].startswith("moving_")]
            if neighbors:
                j = max(neighbors, key=lambda index: output[index][1] - output[index][0])
                needed = minimum_s - (end - start)
                neighbor_start, neighbor_end, neighbor_state = output[j]
                if neighbor_end - neighbor_start >= needed + minimum_s:
                    if j < i:
                        output[j] = (neighbor_start, neighbor_end - needed, neighbor_state)
                        output[i] = (start - needed, end, "moving_variable")
                    else:
                        output[i] = (start, end + needed, "moving_variable")
                        output[j] = (neighbor_start + needed, neighbor_end, neighbor_state)
                else:
                    a, b = sorted((i, j))
                    output[a:b + 1] = [(output[a][0], output[b][1], "moving_variable")]
                a = min(i, j)
                i = max(0, a - 1)
                continue
        if i > 0 and output[i - 1][2] == state:
            output[i - 1:i + 1] = [(output[i - 1][0], end, state)]
            i = max(0, i - 1)
            continue
        i += 1
    return output


def _snap_thermal(thermal: list[tuple], operating: list[tuple], tolerance_s: float) -> list[tuple]:
    boundaries = sorted({v for start, end, _ in operating for v in (start, end)})
    points = [thermal[0][0]] + [r[1] for r in thermal]
    for i in range(1, len(points) - 1):
        closest = min(boundaries, key=lambda t: abs(t - points[i]))
        if abs(closest - points[i]) <= tolerance_s and points[i - 1] < closest < points[i + 1]:
            points[i] = closest
    return [(points[i], points[i + 1], run[2]) for i, run in enumerate(thermal)]


def _stats(values: np.ndarray) -> dict[str, float]:
    values = values[np.isfinite(values)]
    if not len(values):
        return {}
    width = max(1, min(len(values) // 3, 4))
    return {"start": float(np.median(values[:width])), "end": float(np.median(values[-width:])),
            "min": float(np.min(values)), "max": float(np.max(values)),
            "median": float(np.median(values)), "p90_p10": float(np.quantile(values, 0.9) - np.quantile(values, 0.1))}


def _evidence(times: np.ndarray, arrays: dict[str, np.ndarray], start: float, end: float, baseline: float | None, tps_key: str) -> dict:
    mask = (times >= start) & (times <= end)
    evidence: dict[str, Any] = {"sample_count": int(mask.sum())}
    for key, values in arrays.items():
        summary = _stats(values[mask])
        if summary:
            evidence[key] = summary
    speed = evidence.get("speed_kmh")
    if speed:
        evidence.update({"speed_start": speed["start"], "speed_end": speed["end"]})
    for key, name, deadband in (("rpm", "rpm_trend", 150), (tps_key, "tps_trend", 0.1 if tps_key == "tps_voltage" else 5)):
        summary = evidence.get(key)
        if summary:
            delta = summary["end"] - summary["start"]
            evidence[name] = ("increasing" if key == "rpm" else "opening") if delta > deadband else (
                ("decreasing" if key == "rpm" else "closing") if delta < -deadband else "stable")
    if baseline is not None:
        evidence["tps_baseline"] = baseline
        evidence["tps_basis"] = tps_key
    return evidence


REASONS = {
    "engine_off": "Sustained zero RPM with closed-baseline TPS and no contradictory movement evidence.",
    "stationary_running": "Sustained near-zero validated candidate speed with engine RPM above the noise floor.",
    "moving_accelerating": "Sustained positive candidate-speed trend exceeding quantization and persistence thresholds.",
    "moving_decelerating": "Sustained negative candidate-speed trend; no braking inference.",
    "moving_steady": "Observed movement with a narrow robust speed spread across the temporal window.",
    "moving_variable": "Observed movement with mixed or short-lived trends and no persistent steady state.",
    "unknown": "Missing, conflicting, transitional, or insufficiently persistent evidence; motion not inferred from RPM.",
}


def suggest_context(loaded: LoadedTelemetrySession, config: RuleConfig | None = None,
                    speeds: list[float | None] | None = None, speed_provenance: dict | None = None,
                    resolver: SpeedResolver | None = None) -> SuggestionDocument:
    config = config or RuleConfig()
    if loaded.metadata.status != "ok":
        raise ValueError(f"cannot auto-label session: {loaded.metadata.status}")
    times, integrity = timestamp_integrity(loaded, config)
    if len(times) < 2 or times[-1] <= 0:
        raise ValueError("at least two timed samples are required")
    if speeds is None:
        speeds, speed_provenance = (resolver or SpeedResolver()).resolve(loaded)
    if len(speeds) != len(times):
        raise ValueError("speed sample count differs from canonical sample count")
    speed_provenance = speed_provenance or {"status": "provided_research_signal", "available_count": sum(s is not None for s in speeds)}
    speed = np.asarray([np.nan if s is None else s for s in speeds], dtype=float)
    rpm = _array(loaded.samples, ("rpm",))
    voltage = _array(loaded.samples, ("tps_voltage",))
    tps_key = "tps_voltage" if np.isfinite(voltage).sum() >= len(times) / 2 else "tps_raw"
    tps = voltage if tps_key == "tps_voltage" else _array(loaded.samples, ("tps_raw", "tps_raw_candidate"))
    ect = _array(loaded.samples, ("ect_c", "ect_c_candidate"))
    valid = np.asarray([s.get("frame_valid", True) and s.get("checksum_valid", True) for s in loaded.samples])
    speed[~valid] = np.nan
    finite_tps = tps[np.isfinite(tps)]
    baseline = float(np.quantile(finite_tps, 0.05)) if len(finite_tps) else None
    config_payload = asdict(config)
    config_sha = stable_hash(config_payload)
    seed = {"source_sha256": loaded.metadata.source.sha256, "config_sha256": config_sha,
            "rule_version": RULE_VERSION, "signal_provenance": {
                key: speed_provenance.get(key) for key in ("raw_source_sha256", "decoder_version", "native_byte_index", "sample_offset")
            }}
    segments, events = [], []
    all_operating_runs = []
    conflicts, rejected_outliers = 0, 0

    def proposal(start: float, end: float, *, state: str | None = None, thermal: str | None = None,
                 event: str | None = None, reason: str, evidence: dict, quality: str) -> Suggestion:
        original = {"start_s": round(start, 6), "end_s": round(end, 6), "operating_context": state,
                    "thermal_phase": thermal, "event": event, "rule_version": RULE_VERSION,
                    "evidence": evidence, "evidence_quality": quality, "reason": reason}
        provenance = {**original, "rule_config": config_payload, "config_sha256": config_sha,
                      "signal_provenance": speed_provenance, "timestamp_basis": integrity["basis"]}
        return Suggestion(id="suggest_" + stable_hash({**seed, **original})[:20], **original, original_proposal=provenance)

    cut_points = [0] + [i + 1 for i, d in enumerate(np.diff(times)) if d > integrity["gap_threshold_s"]] + [len(times)]
    for chunk_start, chunk_end in zip(cut_points[:-1], cut_points[1:], strict=True):
        t = times[chunk_start:chunk_end]
        s, r, p, c = (v[chunk_start:chunk_end].copy() for v in (speed, rpm, tps, ect))
        if len(t) < 2:
            continue
        implausible = np.flatnonzero(np.abs(np.diff(s) / np.diff(t)) > config.maximum_speed_rate_kmh_s) + 1
        s[implausible] = np.nan
        rejected_outliers += len(implausible)
        smooth = _smooth(s, t, config.smoothing_s)
        slope, change, _ = _window_trends(smooth, t, config.speed_trend_window_s, config.speed_trend_window_s / 2)
        _, _, spread = _window_trends(smooth, t, config.steady_window_s, config.steady_window_s / 2)
        thermal_slope, thermal_delta, thermal_spread = _window_trends(c, t, config.thermal_window_s, config.thermal_minimum_window_s)
        operating, thermal = [], []
        moving = False
        tps_closed = 0.15 if tps_key == "tps_voltage" else 5.0
        for i in range(len(t)):
            if np.isfinite(smooth[i]):
                if smooth[i] >= config.moving_enter_kmh:
                    moving = True
                elif smooth[i] <= config.stationary_speed_kmh:
                    moving = False
            else:
                moving = False
            conflict = np.isfinite(r[i]) and r[i] == 0 and np.isfinite(smooth[i]) and smooth[i] >= config.moving_enter_kmh
            conflicts += int(conflict)
            if conflict:
                state = "unknown"
            elif r[i] == 0 and baseline is not None and np.isfinite(p[i]) and p[i] <= baseline + tps_closed and (
                not np.isfinite(smooth[i]) or smooth[i] <= config.stationary_speed_kmh
            ):
                state = "engine_off"
            elif not np.isfinite(s[i]) or not np.isfinite(smooth[i]):
                state = "unknown"
            elif s[i] <= config.stationary_speed_kmh and smooth[i] <= config.stationary_speed_kmh and r[i] >= config.engine_running_rpm:
                state = "stationary_running"
            elif moving:
                if slope[i] >= config.acceleration_kmh_s and change[i] >= config.speed_trend_delta_kmh:
                    state = "moving_accelerating"
                elif slope[i] <= -config.acceleration_kmh_s and change[i] <= -config.speed_trend_delta_kmh:
                    state = "moving_decelerating"
                elif spread[i] <= config.steady_spread_kmh:
                    state = "moving_steady"
                else:
                    state = "moving_variable"
            else:
                state = "unknown"
            operating.append(state)
            if not np.isfinite(c[i]):
                phase = "unknown"
            elif thermal_slope[i] >= config.warming_rate_c_s and thermal_delta[i] >= config.warming_delta_c:
                phase = "warming"
            elif abs(thermal_slope[i]) <= config.stable_rate_c_s and thermal_spread[i] <= config.stable_spread_c:
                phase = "stable"
            else:
                phase = "unknown"
            thermal.append(phase)
        operating_runs = _persist(_runs(operating, t, float(t[-1])), config.minimum_segment_s)
        all_operating_runs.extend(operating_runs)
        thermal_runs = _persist(_runs(thermal, t, float(t[-1])), config.thermal_persistence_s)
        thermal_runs = _snap_thermal(thermal_runs, operating_runs, config.minimum_segment_s)
        arrays = {"speed_kmh": s, "rpm": r, tps_key: p, "ect_c": c}
        for start, end, state in operating_runs:
            for thermal_start, thermal_end, phase in thermal_runs:
                a, b = max(start, thermal_start), min(end, thermal_end)
                if b <= a:
                    continue
                evidence = _evidence(t, arrays, a, b, baseline, tps_key)
                if "rpm" in evidence:
                    evidence["rpm_in_idle_region"] = config.idle_region_low_rpm <= evidence["rpm"]["median"] <= config.idle_region_high_rpm
                evidence["thermal_rule"] = phase
                selected = (t >= a) & (t <= b)
                for name, measured in (("speed_trend_kmh_s", slope), ("thermal_trend_c_s", thermal_slope),
                                       ("thermal_window_delta_c", thermal_delta)):
                    summary = _stats(measured[selected])
                    if summary:
                        evidence[name] = summary
                quality = "insufficient" if state == "unknown" else (
                    "limited" if phase == "unknown" or integrity["basis"] != "captured_timestamp_ms" or not np.isfinite(s[selected]).all() else "strong")
                segments.append(proposal(a, b, state=state, thermal=phase, reason=REASONS[state], evidence=evidence, quality=quality))

        # Engine events require observed zero-RPM runs on one side, not an ECU idle sentinel.
        engine_states = ["off" if value == 0 else "running" if value >= config.engine_running_rpm else "unknown" for value in r]
        engine_runs = _persist(_runs(engine_states, t, float(t[-1])), config.engine_persistence_s)
        for before, after in zip(engine_runs, engine_runs[1:]):
            if (before[2], after[2]) not in {("off", "running"), ("running", "off")}:
                continue
            kind = "engine_start" if after[2] == "running" else "engine_stop"
            a, b = max(float(t[0]), after[0] - 1), min(float(t[-1]), after[0] + 1)
            events.append(proposal(a, b, event=kind, reason="Observed sustained zero/nonzero RPM transition.",
                                   evidence=_evidence(t, arrays, a, b, baseline, tps_key), quality="strong"))

        if baseline is not None:
            smooth_tps = _smooth(p, t, 0.5)
            delta_limit = config.tps_burst_delta_v if tps_key == "tps_voltage" else config.tps_burst_delta_raw
            rate_limit = config.tps_burst_rate_v_s if tps_key == "tps_voltage" else config.tps_burst_rate_raw_s
            level_limit = config.tps_burst_above_baseline_v if tps_key == "tps_voltage" else config.tps_burst_above_baseline_raw
            last_event_end = -float("inf")
            for i, now in enumerate(t):
                if now <= last_event_end + config.tps_event_cooldown_s or not np.isfinite(smooth_tps[i]):
                    continue
                left = int(np.searchsorted(t, now - config.tps_opening_window_s))
                right = int(np.searchsorted(t, now + config.tps_burst_hold_s, side="right"))
                prior = smooth_tps[left:i]
                if not len(prior) or not np.isfinite(prior).all() or not np.isfinite(smooth_tps[i:right]).all():
                    continue
                low_index = left + int(np.argmin(prior))
                elapsed = now - t[low_index]
                rise = smooth_tps[i] - smooth_tps[low_index]
                if elapsed <= 0 or right <= i or t[right - 1] - now < config.tps_burst_hold_s * 0.75:
                    continue
                if rise < delta_limit or rise / elapsed < rate_limit or np.min(smooth_tps[i:right]) < baseline + level_limit:
                    continue
                a, b = float(t[low_index]), float(t[right - 1])
                evidence = _evidence(t, arrays, a, b, baseline, tps_key)
                evidence.update({"tps_rise": float(rise), "opening_rate_per_s": float(rise / elapsed),
                                 "above_baseline": float(smooth_tps[i] - baseline), "hold_s": float(b - now)})
                events.append(proposal(a, b, event="throttle_burst", reason="Rapid TPS rise with a sustained opening above the session baseline.", evidence=evidence, quality="strong"))
                last_event_end = b

        cycles = []
        verified = [run for run in operating_runs if run[2] != "unknown"]
        for before, after in zip(verified, verified[1:]):
            if before[2] == "stationary_running" and after[2].startswith("moving_") and after[0] - before[1] < config.minimum_segment_s:
                cycles.append((before[0], after[1]))
        last_stop_go_end = -float("inf")
        for i in range(config.stop_go_cycles - 1, len(cycles)):
            window = cycles[i - config.stop_go_cycles + 1:i + 1]
            a, b = window[0][0], window[-1][1]
            if b - a <= config.stop_go_window_s and a >= last_stop_go_end:
                events.append(proposal(a, b, event="repeated_stop_go", reason="Repeated verified stationary-to-moving cycles within the temporal window.",
                                       evidence={"verified_cycles": len(window), "cycle_intervals_s": [list(v) for v in window]}, quality="strong"))
                last_stop_go_end = b

    for gap in integrity["capture_gaps"]:
        segments.append(proposal(gap["start_s"], gap["end_s"], state="unknown", thermal="unknown",
                                 reason="Capture gap; no sensor interpolation or inferred operating state.",
                                 evidence={"capture_gap_s": gap["end_s"] - gap["start_s"], "sample_count": 0}, quality="insufficient"))
    segments.sort(key=lambda s: (s.start_s, s.end_s))
    events.sort(key=lambda e: (e.start_s, e.end_s, e.event))
    durations = np.asarray([s.end_s - s.start_s for s in segments])
    total = float(times[-1])
    metrics = {
        "duration_s": total, "segment_count": len(segments), "event_count": len(events),
        "duration_distribution_s": {"min": float(np.min(durations)), "median": float(np.median(durations)),
                                    "p90": float(np.quantile(durations, 0.9)), "max": float(np.max(durations))} if len(durations) else {},
        "operating_duration_s": {label: sum(s.end_s - s.start_s for s in segments if s.operating_context == label)
                                 for label in sorted({s.operating_context for s in segments})},
        "thermal_duration_s": {label: sum(s.end_s - s.start_s for s in segments if s.thermal_phase == label)
                               for label in ("warming", "stable", "unknown")},
        "unknown_operating_duration_s": sum(s.end_s - s.start_s for s in segments if s.operating_context == "unknown"),
        "unknown_coverage": sum(s.end_s - s.start_s for s in segments if s.operating_context == "unknown") / total,
        "fragmentation_per_minute": len(segments) / (total / 60),
        "short_segment_count": int(sum(d < config.minimum_segment_s for d in durations)),
        "rule_conflicting_samples": conflicts, "speed_outliers_rejected": rejected_outliers,
        "stationary_interval_count": sum(s.operating_context == "stationary_running" for s in segments),
        "verified_stationary_interval_count": sum(state == "stationary_running" for _, _, state in all_operating_runs),
        "stationary_interval_durations_s": [b - a for a, b, state in all_operating_runs if state == "stationary_running"],
        "event_counts": dict(Counter(e.event for e in events)),
        "pending_human_review": len(segments) + len(events),
        "speed_available_count": sum(s is not None for s in speeds),
        "partial_evidence": any(s.evidence_quality != "strong" for s in segments),
        "speed_spread_by_context_kmh": {
            label: float(np.median([s.evidence["speed_kmh"]["p90_p10"] for s in segments
                                    if s.operating_context == label and "speed_kmh" in s.evidence]))
            for label in ("moving_steady", "moving_variable")
            if any(s.operating_context == label and "speed_kmh" in s.evidence for s in segments)
        },
    }
    return SuggestionDocument(session_id=loaded.metadata.session_id,
                              source=SourceProvenance(filename=loaded.metadata.source.filename, sha256=loaded.metadata.source.sha256,
                                                      session_id=loaded.metadata.session_id, decoder_provenance=loaded.metadata.decoder_provenance),
                              config=config_payload, config_sha256=config_sha, signal_provenance=speed_provenance,
                              timestamp_integrity=integrity, segments=segments, events=events, metrics=metrics, generated_at=utc_now_iso())
