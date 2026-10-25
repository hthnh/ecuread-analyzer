"""Equal total weight per physical capture within each supported context."""
from __future__ import annotations

import numpy as np
import pandas as pd


def weighted_quantile(values, weights, quantiles):
    values, weights = np.asarray(values, float), np.asarray(weights, float)
    order = np.argsort(values, kind="stable")
    values, weights = values[order], weights[order]
    positions = (np.cumsum(weights) - weights / 2) / weights.sum()
    return np.interp(quantiles, positions, values)


def capture_balanced_stats(frame: pd.DataFrame, feature: str, scale_floor: float):
    clean = frame[["reference_session_id", feature]].replace([np.inf, -np.inf], np.nan).dropna()
    values = clean[feature].to_numpy(float)
    weights = 1 / clean.groupby("reference_session_id")[feature].transform("count").to_numpy(float)
    q10, q25, median, q75, q90 = weighted_quantile(values, weights, [.1, .25, .5, .75, .9])
    mad = float(weighted_quantile(abs(values - median), weights, [.5])[0])
    return {"count": len(clean), "median": float(median), "mad": mad, "iqr": float(q75 - q25),
            "robust_scale": float(max(mad * 1.4826, (q75 - q25) / 1.349, scale_floor)),
            "scale_floor": scale_floor, "p10": float(q10), "p90": float(q90),
            "weighting": "equal_capture_within_context"}
