from __future__ import annotations

import pandas as pd

from app.ml.features import get_feature_names, extract_window_features
from app.ml.windowing import create_windows


def make_frame_data(count: int = 60) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "frame_index": list(range(count)),
            "checksum_valid": [True] * count,
            "is_valid": [True] * count,
            "relative_time_ms": [index * 100 for index in range(count)],
            "rpm": [1000 + index for index in range(count)],
            "tps_voltage": [0.5] * count,
            "tps_raw_candidate": [10] * count,
            "battery_voltage": [12.5] * count,
            "iat_c": [30] * count,
            "ect_c_candidate": [60] * count,
            "map_raw": [80 + (index % 3) for index in range(count)],
        }
    )


def test_windows_and_features_are_stable_without_nan() -> None:
    data = make_frame_data(60)
    windows = create_windows(data, window_size_samples=20, step_size_samples=10)
    features = extract_window_features(data, windows)
    assert len(windows) == 5
    assert len(features) == 5
    assert list(features.columns)[-len(get_feature_names()) :] == get_feature_names()
    assert not features[get_feature_names()].isna().any().any()
    assert features["corr_rpm_tps_voltage"].eq(0.0).all()
    assert features["tps_voltage_constant_signal"].eq(1.0).all()


def test_window_skip_thresholds() -> None:
    data = make_frame_data(30)
    data.loc[5, "checksum_valid"] = False
    windows = create_windows(data, window_size_samples=20, step_size_samples=5, max_checksum_error_ratio=0.0)
    assert len(windows) == 1
    assert windows[0].start_frame_index == 10
