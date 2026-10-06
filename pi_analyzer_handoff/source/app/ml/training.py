from __future__ import annotations

from pathlib import Path

MODEL_FILENAME = "isolation_forest.joblib"
SCALER_FILENAME = "robust_scaler.joblib"
METADATA_FILENAME = "model_metadata.json"


def model_paths(model_dir: str | Path) -> dict[str, Path]:
    root = Path(model_dir)
    return {
        "model": root / MODEL_FILENAME,
        "scaler": root / SCALER_FILENAME,
        "metadata": root / METADATA_FILENAME,
    }


def model_artifacts_exist(model_dir: str | Path) -> bool:
    paths = model_paths(model_dir)
    return all(path.exists() for path in paths.values())
