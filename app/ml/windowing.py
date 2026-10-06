from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(slots=True)
class Window:
    window_index: int
    row_start: int
    row_end: int
    start_frame_index: int
    end_frame_index: int
    sample_count: int
    checksum_failure_ratio: float
    invalid_decoded_ratio: float
    duration_ms: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def create_windows(
    dataframe: pd.DataFrame,
    window_size_samples: int,
    step_size_samples: int,
    max_checksum_error_ratio: float = 0.0,
    max_invalid_decoded_ratio: float = 0.0,
) -> list[Window]:
    if window_size_samples <= 0:
        raise ValueError("window_size_samples must be positive")
    if step_size_samples <= 0:
        raise ValueError("step_size_samples must be positive")
    if dataframe.empty or len(dataframe) < window_size_samples:
        return []

    windows: list[Window] = []
    sorted_df = dataframe.reset_index(drop=True)

    for row_start in range(0, len(sorted_df) - window_size_samples + 1, step_size_samples):
        row_end = row_start + window_size_samples - 1
        chunk = sorted_df.iloc[row_start : row_end + 1]
        checksum_ok = chunk.get("checksum_valid", pd.Series(False, index=chunk.index)).fillna(False).astype(bool)
        decoded_ok = chunk.get("is_valid", pd.Series(False, index=chunk.index)).fillna(False).astype(bool)
        checksum_failure_ratio = 1.0 - float(checksum_ok.mean())
        invalid_decoded_ratio = 1.0 - float(decoded_ok.mean())

        if checksum_failure_ratio > max_checksum_error_ratio:
            continue
        if invalid_decoded_ratio > max_invalid_decoded_ratio:
            continue

        duration_ms = None
        if "relative_time_ms" in chunk.columns and chunk["relative_time_ms"].notna().all():
            duration_ms = float(chunk["relative_time_ms"].iloc[-1] - chunk["relative_time_ms"].iloc[0])

        windows.append(
            Window(
                window_index=len(windows),
                row_start=row_start,
                row_end=row_end,
                start_frame_index=int(chunk["frame_index"].iloc[0]),
                end_frame_index=int(chunk["frame_index"].iloc[-1]),
                sample_count=len(chunk),
                checksum_failure_ratio=checksum_failure_ratio,
                invalid_decoded_ratio=invalid_decoded_ratio,
                duration_ms=duration_ms,
            )
        )

    return windows

