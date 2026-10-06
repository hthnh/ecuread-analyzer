from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import joblib
import numpy as np
import pandas as pd


EXPECTED_MODEL_VERSION = "iforest-baseline-20260920T091709Z"
EXPECTED_TELEMETRY_SCHEMA_VERSION = "canonical-telemetry-v2"
EXPECTED_FEATURE_SCHEMA_VERSION = "ecu-window-features-v1"
EXPECTED_DECODER_VERSION_KEY = "honda_keihin_71_17:1.0.0"
EXPECTED_FEATURE_COUNT = 81
FLOAT_ATOL = 1e-9
FLOAT_RTOL = 1e-12

SUMMARY_FIELDS = [
    "model_version",
    "telemetry_schema_version",
    "feature_schema_version",
    "signal_columns",
    "window_count",
    "anomaly_window_count",
    "anomaly_ratio",
    "health_score",
    "overall_status",
    "evidence_window_count",
    "minimum_windows_for_status",
    "evidence_sufficient",
    "most_unusual_features",
    "model_loaded",
    "warnings",
    "note",
]


def _default_bundle_dir() -> Path:
    script_bundle_root = Path(__file__).resolve().parents[1]
    if (script_bundle_root / "BUNDLE_MANIFEST.json").exists():
        return script_bundle_root
    return Path.cwd() / "pi_analyzer_handoff"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha_manifest(bundle_dir: Path) -> None:
    manifest_path = bundle_dir / "MANIFEST.sha256"
    if not manifest_path.exists():
        raise AssertionError(f"missing SHA-256 manifest: {manifest_path}")
    for line_number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError as exc:
            raise AssertionError(f"bad manifest line {line_number}: {line!r}") from exc
        actual = _sha256(bundle_dir / relative)
        if actual != expected:
            raise AssertionError(f"hash mismatch for {relative}: expected {expected}, got {actual}")


def load_and_verify_metadata(bundle_dir: Path) -> dict[str, Any]:
    model_dir = bundle_dir / "models" / "honda_keihin_71_17_v2"
    metadata_path = model_dir / "model_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    checks = {
        "model_version_id": EXPECTED_MODEL_VERSION,
        "telemetry_schema_version": EXPECTED_TELEMETRY_SCHEMA_VERSION,
        "feature_schema_version": EXPECTED_FEATURE_SCHEMA_VERSION,
    }
    for key, expected in checks.items():
        actual = metadata.get(key)
        if actual != expected:
            raise AssertionError(f"{key} mismatch: expected {expected!r}, got {actual!r}")
    if EXPECTED_DECODER_VERSION_KEY not in metadata.get("training_decoder_versions", []):
        raise AssertionError("model metadata does not include expected training decoder provenance")
    if len(metadata.get("feature_names", [])) != EXPECTED_FEATURE_COUNT:
        raise AssertionError("model metadata feature_names length is not 81")
    joblib.load(model_dir / "robust_scaler.joblib")
    joblib.load(model_dir / "isolation_forest.joblib")
    return metadata


def _stable_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {field: summary[field] for field in SUMMARY_FIELDS}


def _load_fixture(bundle_dir: Path, name: str) -> Any:
    from app.domain.telemetry import CanonicalTelemetrySession

    payload = json.loads((bundle_dir / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))
    return CanonicalTelemetrySession.model_validate(payload)


def _analyze_fixture(bundle_dir: Path, name: str) -> dict[str, Any]:
    from app.config import Settings
    from app.services.pi_analysis import analyze_canonical_session

    session = _load_fixture(bundle_dir, name)
    return analyze_canonical_session(
        session,
        settings=Settings(),
        model_dir=bundle_dir / "models" / "honda_keihin_71_17_v2",
    )


def _assert_json_summary_matches(bundle_dir: Path, name: str, actual: dict[str, Any]) -> None:
    expected = json.loads((bundle_dir / "golden" / f"{name}.analysis.json").read_text(encoding="utf-8"))
    actual_summary = _stable_summary(actual["summary"])
    if actual_summary != expected:
        raise AssertionError(f"{name} summary does not match golden output")


def _assert_feature_frame_matches(bundle_dir: Path, actual: pd.DataFrame) -> None:
    expected = pd.read_csv(bundle_dir / "golden" / "v2_regular_session.features.csv")
    pd.testing.assert_frame_equal(
        actual.reset_index(drop=True),
        expected.reset_index(drop=True),
        check_dtype=False,
        atol=FLOAT_ATOL,
        rtol=FLOAT_RTOL,
    )


def _assert_windows_match(bundle_dir: Path, actual: pd.DataFrame) -> None:
    expected = pd.read_csv(bundle_dir / "golden" / "v2_regular_session.windows.csv", keep_default_na=False)
    actual = actual.reset_index(drop=True)
    expected = expected.reset_index(drop=True)
    if list(actual.columns) != list(expected.columns):
        raise AssertionError("regular-session window columns differ from golden")
    for column in ["window_index", "start_frame_index", "end_frame_index", "sample_count", "prediction"]:
        if actual[column].astype(int).tolist() != expected[column].astype(int).tolist():
            raise AssertionError(f"regular-session {column} values differ from golden")
    if actual["is_anomaly"].astype(bool).tolist() != expected["is_anomaly"].astype(bool).tolist():
        raise AssertionError("regular-session anomaly decisions differ from golden")
    for column in ["score_sample", "decision_score", "window_health_score"]:
        if not np.allclose(actual[column].astype(float), expected[column].astype(float), atol=FLOAT_ATOL, rtol=FLOAT_RTOL):
            raise AssertionError(f"regular-session {column} values differ from golden")


def verify_golden_inference(bundle_dir: Path, metadata: dict[str, Any]) -> None:
    source_dir = bundle_dir / "source"
    sys.path.insert(0, str(source_dir))

    from app.ml.features import get_feature_names

    if get_feature_names(metadata["signal_columns"]) != metadata["feature_names"]:
        raise AssertionError("runtime feature names differ from model metadata")

    limited = _analyze_fixture(bundle_dir, "v2_short_or_limited")
    _assert_json_summary_matches(bundle_dir, "v2_short_or_limited", limited)
    limited_summary = limited["summary"]
    if not (1 <= limited_summary["window_count"] <= 9):
        raise AssertionError("limited-data fixture must produce 1..9 windows")
    if limited_summary["overall_status"] != "limited_data":
        raise AssertionError("limited-data fixture did not produce limited_data status")
    if limited_summary["minimum_windows_for_status"] != 10 or limited_summary["evidence_sufficient"] is not False:
        raise AssertionError("limited-data evidence gate values are wrong")

    regular = _analyze_fixture(bundle_dir, "v2_regular_session")
    _assert_json_summary_matches(bundle_dir, "v2_regular_session", regular)
    _assert_feature_frame_matches(bundle_dir, regular["feature_frame"])
    _assert_windows_match(bundle_dir, regular["windows"])
    if len(metadata["feature_names"]) != EXPECTED_FEATURE_COUNT:
        raise AssertionError("expected 81 model features")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the Pi Analyzer inference handoff bundle.")
    parser.add_argument("--bundle", type=Path, default=_default_bundle_dir(), help="Path to pi_analyzer_handoff")
    args = parser.parse_args()

    bundle_dir = args.bundle.resolve()
    verify_sha_manifest(bundle_dir)
    metadata = load_and_verify_metadata(bundle_dir)
    verify_golden_inference(bundle_dir, metadata)
    print(f"OK: verified {bundle_dir}")
    print(f"model_version={metadata['model_version_id']}")
    print(f"feature_count={len(metadata['feature_names'])}")


if __name__ == "__main__":
    main()
