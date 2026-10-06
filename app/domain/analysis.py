from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(slots=True)
class AnalysisServiceOutput:
    analysis_run_id: str
    session_id: str
    frame_data: pd.DataFrame
    feature_frame: pd.DataFrame
    windows: pd.DataFrame
    summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

