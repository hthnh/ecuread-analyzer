from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.domain.telemetry import CORE_SIGNAL_COLUMNS
from app.ml.windowing import Window


STAT_FEATURES = [
    "mean",
    "std",
    "min",
    "max",
    "range",
    "median",
    "first",
    "last",
    "delta",
    "slope",
    "mean_absolute_diff",
    "max_absolute_diff",
    "constant_signal",
]

RELATION_FEATURES = [
    "corr_rpm_tps_voltage",
    "corr_rpm_tps_raw",
    "corr_rpm_map",
    "corr_tps_map",
    "rpm_per_tps_voltage",
]

WINDOW_METADATA_COLUMNS = [
    "window_index",
    "start_frame_index",
    "end_frame_index",
    "sample_count",
    "checksum_failure_ratio",
    "invalid_decoded_ratio",
    "duration_ms",
]


def get_feature_names(signal_columns: list[str] | None = None) -> list[str]:
    columns = signal_columns or CORE_SIGNAL_COLUMNS
    names: list[str] = []
    for column in columns:
        names.extend(f"{column}_{feature}" for feature in STAT_FEATURES)
    names.extend(_relation_features_for_columns(columns))
    return names


def _relation_features_for_columns(columns: list[str]) -> list[str]:
    features: list[str] = []
    available = set(columns)
    if {"rpm", "tps_voltage"}.issubset(available):
        features.append("corr_rpm_tps_voltage")
    if "rpm" in available and ("tps_raw_candidate" in available or "tps_raw" in available):
        features.append("corr_rpm_tps_raw")
    if {"rpm", "map_raw"}.issubset(available):
        features.append("corr_rpm_map")
    if {"tps_voltage", "map_raw"}.issubset(available):
        features.append("corr_tps_map")
    if {"rpm", "tps_voltage"}.issubset(available):
        features.append("rpm_per_tps_voltage")
    return features


def _clean_numeric_series(values: pd.Series) -> pd.Series:
    series = pd.to_numeric(values, errors="coerce").astype(float)
    if series.isna().all():
        return pd.Series(np.zeros(len(series)), index=series.index, dtype=float)
    return series.ffill().bfill().fillna(0.0)


def _safe_corr(left: pd.Series, right: pd.Series) -> float:
    if len(left) < 2 or left.nunique(dropna=False) <= 1 or right.nunique(dropna=False) <= 1:
        return 0.0
    value = float(left.corr(right))
    return value if math.isfinite(value) else 0.0


def _series_features(series: pd.Series) -> dict[str, float]:
    clean = _clean_numeric_series(series)
    diff = clean.diff().dropna().abs()
    first = float(clean.iloc[0]) if len(clean) else 0.0
    last = float(clean.iloc[-1]) if len(clean) else 0.0
    minimum = float(clean.min()) if len(clean) else 0.0
    maximum = float(clean.max()) if len(clean) else 0.0
    return {
        "mean": float(clean.mean()) if len(clean) else 0.0,
        "std": float(clean.std(ddof=0)) if len(clean) else 0.0,
        "min": minimum,
        "max": maximum,
        "range": maximum - minimum,
        "median": float(clean.median()) if len(clean) else 0.0,
        "first": first,
        "last": last,
        "delta": last - first,
        "slope": (last - first) / max(1, len(clean) - 1),
        "mean_absolute_diff": float(diff.mean()) if len(diff) else 0.0,
        "max_absolute_diff": float(diff.max()) if len(diff) else 0.0,
        "constant_signal": 1.0 if clean.nunique(dropna=False) <= 1 else 0.0,
    }


def extract_window_features(
    dataframe: pd.DataFrame,
    windows: list[Window],
    signal_columns: list[str] | None = None,
) -> pd.DataFrame:
    columns = signal_columns or CORE_SIGNAL_COLUMNS
    records: list[dict[str, float | int | None]] = []
    sorted_df = dataframe.reset_index(drop=True)

    for window in windows:
        chunk = sorted_df.iloc[window.row_start : window.row_end + 1]
        record: dict[str, float | int | None] = {
            "window_index": window.window_index,
            "start_frame_index": window.start_frame_index,
            "end_frame_index": window.end_frame_index,
            "sample_count": window.sample_count,
            "checksum_failure_ratio": window.checksum_failure_ratio,
            "invalid_decoded_ratio": window.invalid_decoded_ratio,
            "duration_ms": window.duration_ms,
        }

        clean_columns: dict[str, pd.Series] = {}
        for column in columns:
            clean_columns[column] = _clean_numeric_series(chunk[column])
            for feature_name, value in _series_features(chunk[column]).items():
                record[f"{column}_{feature_name}"] = value

        if {"rpm", "tps_voltage"}.issubset(clean_columns):
            rpm = clean_columns["rpm"]
            tps_voltage = clean_columns["tps_voltage"]
            record["corr_rpm_tps_voltage"] = _safe_corr(rpm, tps_voltage)
            record["rpm_per_tps_voltage"] = float(rpm.mean() / max(float(tps_voltage.mean()), 1e-6))
        tps_raw_column = "tps_raw" if "tps_raw" in clean_columns else "tps_raw_candidate"
        if {"rpm", tps_raw_column}.issubset(clean_columns):
            record["corr_rpm_tps_raw"] = _safe_corr(clean_columns["rpm"], clean_columns[tps_raw_column])
        if {"rpm", "map_raw"}.issubset(clean_columns):
            record["corr_rpm_map"] = _safe_corr(clean_columns["rpm"], clean_columns["map_raw"])
        if {"tps_voltage", "map_raw"}.issubset(clean_columns):
            record["corr_tps_map"] = _safe_corr(clean_columns["tps_voltage"], clean_columns["map_raw"])

        records.append(record)

    if not records:
        return pd.DataFrame(columns=WINDOW_METADATA_COLUMNS + get_feature_names(columns))

    feature_df = pd.DataFrame(records)
    feature_columns = get_feature_names(columns)
    for feature_name in feature_columns:
        if feature_name not in feature_df.columns:
            feature_df[feature_name] = 0.0
    feature_df[feature_columns] = feature_df[feature_columns].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return feature_df[WINDOW_METADATA_COLUMNS + feature_columns]
