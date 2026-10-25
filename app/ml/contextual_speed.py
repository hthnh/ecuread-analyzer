"""Robust motion evidence. No fixed RPM/speed ratio or mechanical fault rule."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from app.ml.contextual_detector import assign_operating_context
from app.ml.harness import DetectorContext, DetectorIdentity, DetectorResult
from app.ml.robust_reference import capture_balanced_stats, weighted_quantile

SPEED_DETECTOR_ID = "contextual_speed"
SPEED_SCHEMA = "contextual-speed-reference-v1"
SPEED_FEATURES = ("speed_median", "speed_rate", "speed_rate_dispersion", "speed_stationary_fraction")
SCORED = {"speed_rate": 1.0, "speed_rate_dispersion": 1.0,
          "rpm_median": 500.0, "tps_voltage_median": .25, "tps_raw_median": 10.0}


def speed_window_features(frames: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Use the identical eligible window identities, not a second window generator."""
    result = features.copy()
    records = []
    indexed = frames.set_index("frame_index", drop=False)
    for _, window in features.iterrows():
        chunk = indexed.loc[int(window.start_frame_index):int(window.end_frame_index)]
        times = pd.to_numeric(chunk.relative_time_ms, errors="coerce").to_numpy(float)
        speeds = pd.to_numeric(chunk.get("vehicle_speed_kmh", pd.Series(np.nan, index=chunk.index)), errors="coerce").to_numpy(float)
        valid = (len(chunk) == int(window.sample_count) and len(times) >= 5
                 and np.isfinite(times).all() and np.all(np.diff(times) > 0)
                 and np.isfinite(speeds).all() and np.all((speeds >= 0) & (speeds <= 255)))
        row = {name: np.nan for name in SPEED_FEATURES}
        row.update(speed_status="missing_speed_or_invalid_time", speed_start_time_ms=None, speed_end_time_ms=None)
        if valid:
            # Five elapsed-time blocks suppress single-byte quantization and brief bursts.
            blocks = np.minimum(((times - times[0]) / (times[-1] - times[0]) * 5).astype(int), 4)
            centers = [(np.median(times[blocks == b]) / 1000, np.median(speeds[blocks == b])) for b in range(5) if np.any(blocks == b)]
            if len(centers) == 5:
                t, v = np.asarray(centers).T
                rates = np.diff(v) / np.diff(t)
                rate = float(np.median(rates))
                row.update(speed_median=float(np.median(speeds)), speed_rate=rate,
                           speed_rate_dispersion=float(np.median(abs(rates - rate))),
                           speed_stationary_fraction=float(np.mean(speeds <= 1)), speed_status="ok",
                           speed_start_time_ms=float(times[0]), speed_end_time_ms=float(times[-1]))
        records.append(row)
    for column in (*SPEED_FEATURES, "speed_status", "speed_start_time_ms", "speed_end_time_ms"):
        result[column] = [r[column] for r in records]
    return result


def speed_context(row, edges):
    if row.get("speed_status") != "ok":
        return "signals_unavailable"
    stationary = row["speed_stationary_fraction"]
    motion = "stationary" if stationary == 1 else "stop_transition" if stationary > 0 else "moving"
    load = assign_operating_context(row)
    band = int(np.searchsorted(edges, row["speed_median"], side="right")) if motion == "moving" else 0
    return f"{motion}:speed_band_{band}:{load}"


def build_speed_reference(frames_by_capture, *, provenance, min_windows=50, min_captures=3):
    rows = []
    for sid, frame in sorted(frames_by_capture.items()):
        chunk = frame[frame.speed_status == "ok"].copy()
        chunk["reference_session_id"] = sid
        rows.append(chunk)
    all_rows = pd.concat(rows, ignore_index=True)
    moving = all_rows[all_rows.speed_stationary_fraction == 0]
    weights = 1 / moving.groupby("reference_session_id").speed_median.transform("count")
    edges = sorted(set(float(x) for x in weighted_quantile(moving.speed_median, weights, [.25, .5, .75]))) if len(moving) else []
    all_rows["speed_context"] = all_rows.apply(lambda r: speed_context(r, edges), axis=1)
    contexts, unsupported = {}, {}
    for name, chunk in all_rows.groupby("speed_context"):
        capture_ids = sorted(chunk.reference_session_id.unique())
        support = {"window_count": len(chunk), "reference_session_count": len(capture_ids), "reference_session_ids": capture_ids}
        if len(chunk) < min_windows or len(capture_ids) < min_captures:
            unsupported[name] = support
            continue
        contexts[name] = {**support, "features": {f: capture_balanced_stats(chunk, f, floor) for f, floor in SCORED.items()}}
    result = {"schema_version": SPEED_SCHEMA, "detector_id": SPEED_DETECTOR_ID,
              "reference_session_ids": sorted(frames_by_capture), "contexts": contexts,
              "unsupported_contexts": unsupported, "provenance": provenance,
              "configuration": {"speed_band_edges_kmh": edges, "min_windows": min_windows,
                  "min_captures": min_captures, "robust_z_threshold": 4.5,
                  "weighting": "equal_capture_within_context", "derivative": "five_elapsed_time_block_medians",
                  "speed_contract": "validated_native_table17_byte19_kmh",
                  "relationship_rule": "moving_only_min_rpm_and_tps_deviation; never RPM alone",
                  "scale_floors": SCORED},
              "limitations": ["Observed operating reference, not independently confirmed healthy data.",
                  "No fixed CVT ratio, road speed limit, or fault diagnosis.", "Speed is operator-corroborated, not GPS calibrated."]}
    digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:12]
    result["model_version"] = f"contextual-speed-robust-v1-{digest}"
    return result


class ContextualSpeedDetector:
    def __init__(self, reference, *, enabled=True):
        if reference.get("schema_version") != SPEED_SCHEMA:
            raise ValueError("incompatible contextual speed reference schema")
        self.reference, self.enabled = reference, enabled
        self.identity = DetectorIdentity(SPEED_DETECTOR_ID, "contextual-speed", reference["model_version"], "RobustContextualStats")
        self.required_features = (*SPEED_FEATURES, "rpm_median", "tps_voltage_median", "tps_raw_median")

    def infer(self, feature_frame: pd.DataFrame, context: DetectorContext) -> DetectorResult:
        windows = feature_frame.copy()
        config = self.reference["configuration"]
        threshold = config["robust_z_threshold"]
        evidence, flags, scores = [], [], []
        for _, row in windows.iterrows():
            name = speed_context(row, config["speed_band_edges_kmh"])
            stats = self.reference["contexts"].get(name)
            applicable = stats is not None and name != "signals_unavailable"
            item = {"applicable": applicable, "context": name,
                    "reason": "context_not_supported" if name != "signals_unavailable" else "missing_speed_or_invalid_time",
                    "start_time_ms": row.get("speed_start_time_ms"), "end_time_ms": row.get("speed_end_time_ms"),
                    "signals": {f: None if pd.isna(row.get(f)) else float(row[f]) for f in self.required_features},
                    "model_version": self.identity.model_version}
            score, flag = None, None
            if applicable:
                deviations = {f: abs(float(row[f]) - s["median"]) / s["robust_scale"] for f, s in stats["features"].items()}
                temporal = max(deviations["speed_rate"], deviations["speed_rate_dispersion"])
                relationship = min(deviations["rpm_median"], max(deviations["tps_voltage_median"], deviations["tps_raw_median"])) if name.startswith("moving:") else 0.0
                score = max(temporal, relationship)
                flag = score >= threshold
                item.update(reason="unusual_speed_change" if flag and temporal >= relationship else "unusual_speed_engine_relationship" if flag else "within_observed_reference",
                            threshold=threshold, deviations=deviations, reference_support=stats["reference_session_count"])
            evidence.append(item)
            scores.append(score)
            flags.append(flag)
        windows["speed_evidence"] = evidence
        windows["speed_anomaly_score"] = scores
        windows["speed_threshold"] = [threshold if f is not None else None for f in flags]
        windows["speed_is_anomaly"] = pd.Series(flags, index=windows.index, dtype=object)
        scored = sum(f is not None for f in flags)
        flagged = sum(f is True for f in flags)
        return DetectorResult(identity=self.identity, status="ok" if scored else "skipped",
            reason=None if scored else "no_supported_speed_context", required_features=self.required_features,
            windows=windows, anomaly_score_column="speed_anomaly_score", score_direction="higher_is_more_anomalous",
            is_anomaly_column="speed_is_anomaly", evidence_columns=("speed_evidence", "speed_threshold"),
            scored_window_count=scored, anomaly_window_count=flagged, anomaly_ratio=flagged / scored if scored else None,
            metadata={"threshold": threshold, "applicable_window_count": scored, "skipped_window_count": len(windows) - scored,
                      "configuration": config, "provenance": self.reference.get("provenance", {})})
