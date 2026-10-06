from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib

from app.ml.training import model_artifacts_exist, model_paths


@dataclass(slots=True)
class ModelBundle:
    model: Any
    scaler: Any
    metadata: dict[str, Any]


def load_model_bundle(model_dir: str | Path) -> ModelBundle:
    paths = model_paths(model_dir)
    if not model_artifacts_exist(model_dir):
        raise FileNotFoundError(f"model artifacts are not complete in {model_dir}")
    metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    return ModelBundle(
        model=joblib.load(paths["model"]),
        scaler=joblib.load(paths["scaler"]),
        metadata=metadata,
    )
