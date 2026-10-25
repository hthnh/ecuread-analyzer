"""Explicit, hash-verified local bundle selection with atomic activation/rollback."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.domain.telemetry import V2_CORE_SIGNAL_COLUMNS
from app.ml.features import get_feature_names


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@dataclass(frozen=True)
class ActiveBundle:
    manifest: dict
    manifest_sha256: str
    model_dir: Path
    battery: dict
    speed: dict | None

    def provenance(self):
        return {"bundle_id": self.manifest["bundle_id"], "manifest_sha256": self.manifest_sha256,
                "artifacts": self.manifest["artifacts"], "activation_scope": "local"}


def load_bundle(settings: Settings, manifest_path: Path, expected_sha: str | None = None):
    digest = sha256(manifest_path)
    if expected_sha and digest != expected_sha:
        raise ValueError("active bundle manifest SHA mismatch; no legacy fallback allowed")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != "ecu-detector-bundle-v1":
        raise ValueError("unsupported model bundle schema")
    if (manifest.get("telemetry_schema_version") != "canonical-telemetry-v2"
        or manifest.get("feature_schema_version") != "ecu-window-features-v1"
        or manifest.get("feature_names") != get_feature_names(V2_CORE_SIGNAL_COLUMNS)
        or manifest.get("signal_columns") != V2_CORE_SIGNAL_COLUMNS
        or manifest.get("window_geometry") != {"size": settings.window_size_samples, "stride": settings.window_step_samples}):
        raise ValueError("active bundle feature/schema/window compatibility mismatch")
    paths = {}
    for name, item in manifest["artifacts"].items():
        path = (settings.data_dir / item["path"]).resolve()
        if not path.is_relative_to(settings.data_dir.resolve()) or not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError(f"active bundle artifact missing or SHA mismatch: {name}")
        paths[name] = path
    for key in ("if_model", "if_scaler", "if_metadata", "battery"):
        if key not in paths:
            raise ValueError(f"bundle missing required artifact: {key}")
    model_dir = paths["if_model"].parent
    if paths["if_scaler"].parent != model_dir or paths["if_metadata"].parent != model_dir:
        raise ValueError("IF model/scaler/metadata must belong to the same bundle directory")
    expected_names = {"if_model": "isolation_forest.joblib", "if_scaler": "robust_scaler.joblib", "if_metadata": "model_metadata.json"}
    if any(paths[key].name != name for key, name in expected_names.items()):
        raise ValueError("bundle IF artifact filenames do not match the supported loader")
    metadata = json.loads(paths["if_metadata"].read_text())
    if (metadata.get("telemetry_schema_version") != manifest["telemetry_schema_version"]
        or metadata.get("feature_schema_version") != manifest["feature_schema_version"]
        or metadata.get("feature_names") != manifest["feature_names"]
        or metadata.get("signal_columns") != manifest["signal_columns"]
        or metadata.get("window_size_samples") != settings.window_size_samples
        or metadata.get("window_step_samples") != settings.window_step_samples):
        raise ValueError("bundle IF metadata feature/schema/window mismatch")
    battery = json.loads(paths["battery"].read_text())
    if battery.get("schema_version") != "contextual-battery-reference-v1":
        raise ValueError("incompatible battery reference schema")
    speed = json.loads(paths["speed"].read_text()) if "speed" in paths else None
    if speed is not None and speed.get("schema_version") != "contextual-speed-reference-v1":
        raise ValueError("incompatible speed reference schema")
    return ActiveBundle(manifest, digest, model_dir, battery, speed)


def resolve_active_bundle(settings: Settings):
    config = settings.model_dir / "active_bundle.json"
    if not config.is_file():
        return None
    pointer = json.loads(config.read_text())
    path = (settings.data_dir / pointer["manifest"]).resolve()
    if not path.is_relative_to(settings.data_dir.resolve()):
        raise ValueError("active bundle manifest escapes data directory")
    return load_bundle(settings, path, pointer["sha256"])


def activate_bundle(settings: Settings, manifest_path: Path):
    bundle = load_bundle(settings, manifest_path)
    config = settings.model_dir / "active_bundle.json"
    pointer = {"manifest": str(manifest_path.resolve().relative_to(settings.data_dir.resolve())),
               "sha256": bundle.manifest_sha256, "bundle_id": bundle.manifest["bundle_id"], "scope": "local"}
    tmp = config.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, config)
    return pointer


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    print(json.dumps(activate_bundle(Settings(), parser.parse_args().manifest), indent=2))
