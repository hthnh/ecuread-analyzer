from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

from app.config import Settings
from app.domain.telemetry import CanonicalTelemetrySession, get_required_signal_columns
from app.evaluation.legacy_iforest import legacy_run_inference
from app.ml.artifacts import load_model_bundle
from app.ml.harness import DetectorContext, ModelHarness
from app.ml.iforest_detector import IsolationForestDetector
from app.ml.inference import run_inference
from app.services.analysis_service import (
    AnalysisService,
    build_feature_frame,
    canonical_session_to_frame_data,
)


FLOAT_ATOL = 1e-9
FLOAT_RTOL = 1e-12
STABLE_SUMMARY_FIELDS = [
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


@dataclass(frozen=True, slots=True)
class EvaluationPaths:
    repo_root: Path
    output_dir: Path
    data_dir: Path
    real_data_dir: Path
    model_dir: Path
    handoff_dir: Path
    pre_h1_tarball: Path


def default_paths(repo_root: Path | None = None, output_dir: Path | None = None) -> EvaluationPaths:
    root = (repo_root or Path.cwd()).resolve()
    return EvaluationPaths(
        repo_root=root,
        output_dir=(output_dir or root / "data" / "evaluation" / "h2").resolve(),
        data_dir=root / "data",
        real_data_dir=root / "real_data",
        model_dir=root / "data" / "models" / "honda_keihin_71_17_v2",
        handoff_dir=root / "pi_analyzer_handoff",
        pre_h1_tarball=root / "pi_analyzer_handoff.tar.gz",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=json_default) + "\n", encoding="utf-8")


def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def load_csv_by_key(path: Path, key: str) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {row[key]: row for row in csv.DictReader(handle) if row.get(key)}


def load_canonical_session_from_telemetry(session_dir: Path) -> CanonicalTelemetrySession:
    metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    samples = [
        json.loads(line)
        for line in (session_dir / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return CanonicalTelemetrySession.model_validate(
        {
            "session_id": metadata.get("session_id") or session_dir.name,
            "vehicle_id": metadata.get("vehicle_id"),
            "device_id": metadata.get("device_id"),
            "ecu_profile_id": metadata.get("ecu_profile_id"),
            "decoder_id": metadata["decoder_id"],
            "decoder_version": metadata["decoder_version"],
            "telemetry_schema_version": metadata["telemetry_schema_version"],
            "sampling": metadata.get("sampling") or {},
            "signal_definitions": metadata.get("signal_definitions") or {},
            "samples": samples,
            "source_type": metadata.get("source_type", "canonical"),
        }
    )


def _duration_ms_from_session(session: CanonicalTelemetrySession) -> float | None:
    timestamps = [sample.timestamp_ms for sample in session.samples if sample.timestamp_ms is not None]
    if len(timestamps) >= 2:
        return float(max(timestamps) - min(timestamps))
    if session.sampling.sample_interval_ms is not None and len(session.samples) > 1:
        return float((len(session.samples) - 1) * session.sampling.sample_interval_ms)
    if session.sampling.sampling_rate_hz is not None and session.sampling.sampling_rate_hz > 0 and len(session.samples) > 1:
        return float((len(session.samples) - 1) * (1000.0 / session.sampling.sampling_rate_hz))
    return None


def _verified_signals(signal_definitions: dict[str, dict[str, str]]) -> list[str]:
    verified: list[str] = []
    for signal_name, definition in signal_definitions.items():
        status = str(definition.get("status", "")).lower()
        if "verified" in status:
            verified.append(signal_name)
    return sorted(verified)


def _label_metadata(session_id: str, labels: dict[str, dict[str, str]], splits: dict[str, dict[str, str]]) -> dict[str, Any]:
    label_row = labels.get(session_id, {})
    split_row = splits.get(session_id, {})
    label = label_row.get("label") or split_row.get("human_label")
    label_source = label_row.get("label_source")
    suitable_for_metrics = label in {"verified_normal", "verified_fault"}
    return {
        "label": label,
        "label_source": label_source,
        "notes": label_row.get("notes"),
        "split": split_row.get("split") or "unassigned",
        "ground_truth_available": bool(label),
        "suitable_for_precision_recall_f1": suitable_for_metrics,
        "metrics_reason": (
            "verified binary label"
            if suitable_for_metrics
            else "not a verified binary normal/fault ground-truth label"
        ),
    }


def build_evaluation_manifest(paths: EvaluationPaths) -> dict[str, Any]:
    settings = Settings(data_dir=paths.data_dir, model_dir=paths.model_dir)
    model_metadata = json.loads((paths.model_dir / "model_metadata.json").read_text(encoding="utf-8"))
    labels = load_csv_by_key(paths.data_dir / "calibration" / "session_labels.csv", "session_id")
    splits = load_csv_by_key(paths.data_dir / "calibration" / "human_normal_split.csv", "session_id")

    sessions: list[dict[str, Any]] = []
    telemetry_root = paths.data_dir / "telemetry"
    for session_dir in sorted(path for path in telemetry_root.glob("*") if path.is_dir()):
        try:
            session = load_canonical_session_from_telemetry(session_dir)
            signal_columns = get_required_signal_columns(session.telemetry_schema_version)
            frame_data = canonical_session_to_frame_data(session, signal_columns)
            feature_frame = build_feature_frame(frame_data, settings, signal_columns)
            window_count: int | None = int(len(feature_frame))
            evaluation_ready = True
            error = None
        except Exception as exc:  # noqa: BLE001 - manifest records unusable sessions.
            metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
            session = CanonicalTelemetrySession.model_validate(
                {
                    "session_id": metadata.get("session_id") or session_dir.name,
                    "decoder_id": metadata.get("decoder_id", "unknown"),
                    "decoder_version": metadata.get("decoder_version", "unknown"),
                    "telemetry_schema_version": metadata.get("telemetry_schema_version", "canonical-telemetry-v1"),
                    "sampling": metadata.get("sampling") or {},
                    "signal_definitions": metadata.get("signal_definitions") or {},
                    "samples": [],
                }
            )
            signal_columns = []
            window_count = None
            evaluation_ready = False
            error = f"{type(exc).__name__}: {exc}"

        duration_ms = _duration_ms_from_session(session)
        sample_count = len(session.samples)
        source_metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
        sessions.append(
            {
                "dataset_kind": "canonical_telemetry",
                "session_id": session.session_id or session_dir.name,
                "path": str(session_dir.relative_to(paths.repo_root)),
                "capture_provenance": {
                    "source_type": session.source_type,
                    "vehicle_id": session.vehicle_id,
                    "device_id": session.device_id,
                    "ecu_profile_id": session.ecu_profile_id,
                },
                "decoder": {
                    "decoder_id": session.decoder_id,
                    "decoder_version": session.decoder_version,
                    "decoder_version_key": session.decoder_version_key,
                },
                "model_version": model_metadata.get("model_version_id") or model_metadata.get("version"),
                "telemetry_schema_version": session.telemetry_schema_version,
                "signal_columns": signal_columns,
                "verified_signals": _verified_signals(source_metadata.get("signal_definitions", {})),
                "sample_count": sample_count,
                "recording_duration_ms": duration_ms,
                "window_count": window_count,
                "temporal_order_key": session.session_id or session_dir.name,
                "training_evaluation_split": _label_metadata(session.session_id or session_dir.name, labels, splits),
                "evaluation_ready": evaluation_ready,
                "evaluation_error": error,
                "artifacts": {
                    "metadata_sha256": file_sha256(session_dir / "metadata.json"),
                    "samples_sha256": file_sha256(session_dir / "samples.jsonl"),
                },
            }
        )

    calibration_datasets = discover_calibration_datasets(paths)
    raw_real_run_sources = discover_real_run_sources(paths, model_metadata)

    return {
        "schema_version": "model-harness-evaluation-manifest-v1",
        "provenance": {
            "repo_root": str(paths.repo_root),
            "source_revision": "unavailable-no-git-repository",
            "model_dir": str(paths.model_dir.relative_to(paths.repo_root)),
            "model_metadata_sha256": file_sha256(paths.model_dir / "model_metadata.json"),
            "model_artifact_sha256": file_sha256(paths.model_dir / "isolation_forest.joblib"),
            "scaler_artifact_sha256": file_sha256(paths.model_dir / "robust_scaler.joblib"),
            "feature_schema_version": model_metadata.get("feature_schema_version"),
            "telemetry_schema_version": model_metadata.get("telemetry_schema_version"),
            "model_version": model_metadata.get("model_version_id") or model_metadata.get("version"),
            "training_sessions_from_model_metadata": model_metadata.get("training_sessions", []),
        },
        "policy": {
            "unlabeled_sessions_are_not_verified_normal_or_faulty": True,
            "precision_recall_f1_requires_verified_binary_labels": True,
            "temporal_order_preserved_by": "session_id ordering for canonical ride/session IDs; raw source filenames preserved separately",
        },
        "sessions": sessions,
        "calibration_datasets": calibration_datasets,
        "raw_real_run_sources": raw_real_run_sources,
        "summary": {
            "canonical_session_count": len(sessions),
            "evaluation_ready_canonical_session_count": sum(1 for item in sessions if item["evaluation_ready"]),
            "calibration_dataset_count": len(calibration_datasets),
            "raw_real_run_source_count": len(raw_real_run_sources),
        },
    }


def discover_calibration_datasets(paths: EvaluationPaths) -> list[dict[str, Any]]:
    calibration_rows = load_csv_by_key(paths.data_dir / "decoder-audit" / "reports" / "calibration_sessions.csv", "session_id")
    datasets: list[dict[str, Any]] = []
    for session_id, row in sorted(calibration_rows.items()):
        datasets.append(
            {
                "dataset_kind": "controlled_calibration",
                "session_id": session_id,
                "scenario_id": row.get("scenario_id"),
                "path": row.get("session_dir"),
                "frame_count": _int_or_none(row.get("frame_count")),
                "valid_frame_count": _int_or_none(row.get("valid_frame_count")),
                "recording_duration_ms": _float_or_none(row.get("duration_ms")),
                "events_discovered": _split_semicolon(row.get("events_discovered")),
                "ground_truth_available": bool(row.get("events_discovered")),
                "ground_truth_scope": "signal calibration events; not anomaly detector normal/fault labels",
                "evaluation_ready": False,
                "evaluation_note": "raw calibration artifact is cataloged for provenance; canonical telemetry sessions are evaluated separately",
            }
        )
    return datasets


def discover_real_run_sources(paths: EvaluationPaths, model_metadata: dict[str, Any]) -> list[dict[str, Any]]:
    training_sources = set(model_metadata.get("training_sessions", []))
    sources: list[dict[str, Any]] = []
    for path in sorted((paths.real_data_dir / "real_run").glob("*.jsonl")):
        sources.append(
            {
                "dataset_kind": "raw_real_run_source",
                "source_file": str(path.relative_to(paths.repo_root)),
                "source_filename": path.name,
                "used_for_model_training": path.name in training_sources,
                "sha256": file_sha256(path),
                "ground_truth_available": False,
            }
        )
    return sources


def _int_or_none(value: str | None) -> int | None:
    if value in {None, ""}:
        return None
    return int(float(value))


def _float_or_none(value: str | None) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _split_semicolon(value: str | None) -> list[str]:
    if not value:
        return []
    return [item for item in value.split(";") if item]


def run_parity_validation(paths: EvaluationPaths) -> dict[str, Any]:
    settings = Settings(data_dir=paths.data_dir, model_dir=paths.model_dir)
    historical = identify_pre_h1_source(paths)
    fixtures = [
        paths.handoff_dir / "fixtures" / "v2_short_or_limited.json",
        paths.handoff_dir / "fixtures" / "v2_regular_session.json",
    ]
    cases = []
    for fixture_path in fixtures:
        session = CanonicalTelemetrySession.model_validate(json.loads(fixture_path.read_text(encoding="utf-8")))
        signal_columns = get_required_signal_columns(session.telemetry_schema_version)
        frame_data = canonical_session_to_frame_data(session, signal_columns)
        feature_frame = build_feature_frame(frame_data, settings, signal_columns)
        post = AnalysisService(settings, model_dir=paths.model_dir).analyze(
            session,
            persist=False,
        )

        historical_case = run_historical_handoff_analysis(paths, fixture_path)
        if historical_case["available"]:
            cases.append(compare_historical_parity_case(fixture_path.stem, historical_case, post))
            continue

        pre = legacy_run_inference(
            feature_frame,
            paths.model_dir,
            expected_telemetry_schema_version=session.telemetry_schema_version,
            expected_signal_columns=signal_columns,
            minimum_windows_for_status=settings.min_session_windows_for_status,
        )
        fallback_case = compare_parity_case(fixture_path.stem, feature_frame, pre, run_inference(
            feature_frame,
            paths.model_dir,
            expected_telemetry_schema_version=session.telemetry_schema_version,
            expected_signal_columns=signal_columns,
            minimum_windows_for_status=settings.min_session_windows_for_status,
        ))
        fallback_case["historical_execution"] = historical_case
        cases.append(fallback_case)

    api_compatibility = run_public_api_compatibility(paths, fixtures[-1])
    all_passed = all(case["passed"] for case in cases) and api_compatibility["passed"]
    return {
        "schema_version": "model-harness-parity-report-v1",
        "historical_source": historical,
        "numeric_tolerance": {"float_atol": FLOAT_ATOL, "float_rtol": FLOAT_RTOL},
        "cases": cases,
        "public_api_compatibility": api_compatibility,
        "passed": all_passed,
        "limitations": [
            "No .git directory is present, so Git revision checkout of pre-H1 code is not possible.",
            "The pre-H1 reference is the checked-in pi_analyzer_handoff.tar.gz source snapshot plus an isolated copy of its inference logic.",
        ],
    }


def run_historical_handoff_analysis(paths: EvaluationPaths, fixture_path: Path) -> dict[str, Any]:
    if not paths.pre_h1_tarball.exists():
        return {"available": False, "reason": "pre-H1 tarball not found"}
    script = """
import json
import pandas as pd
import sys
from pathlib import Path
from app.config import Settings
from app.domain.telemetry import CanonicalTelemetrySession
from app.services.pi_analysis import analyze_canonical_session

fixture_path = Path(sys.argv[1])
model_dir = Path(sys.argv[2])
output_path = Path(sys.argv[3])
session = CanonicalTelemetrySession.model_validate(json.loads(fixture_path.read_text(encoding="utf-8")))
result = analyze_canonical_session(session, settings=Settings(), model_dir=model_dir)

def records(frame):
    return frame.where(pd.notna(frame), None).to_dict(orient="records")

payload = {
    "summary": result["summary"],
    "feature_columns": list(result["feature_frame"].columns),
    "window_columns": list(result["windows"].columns),
    "windows": records(result["windows"]),
}
output_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
"""
    try:
        with tempfile.TemporaryDirectory(prefix="h2-pre-h1-") as tmp:
            tmp_path = Path(tmp)
            with tarfile.open(paths.pre_h1_tarball, "r:gz") as tar:
                tar.extractall(tmp_path, filter="data")
            output_path = tmp_path / "legacy-output.json"
            source_dir = tmp_path / "pi_analyzer_handoff" / "source"
            env = {"PYTHONPATH": str(source_dir)}
            subprocess.run(
                [sys.executable, "-c", script, str(fixture_path), str(paths.model_dir), str(output_path)],
                cwd=tmp_path,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            return {
                "available": True,
                "summary": payload["summary"],
                "feature_columns": payload["feature_columns"],
                "window_columns": payload["window_columns"],
                "windows": payload["windows"],
            }
    except Exception as exc:  # noqa: BLE001 - parity report documents historical execution limits.
        return {
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }


def identify_pre_h1_source(paths: EvaluationPaths) -> dict[str, Any]:
    if not paths.pre_h1_tarball.exists():
        return {
            "source": None,
            "available": False,
            "limitation": "pi_analyzer_handoff.tar.gz was not found and this workspace has no .git history",
        }
    source_hash = _tar_member_sha256(paths.pre_h1_tarball, "pi_analyzer_handoff/source/app/ml/inference.py")
    manifest_hash = _tar_member_sha256(paths.pre_h1_tarball, "pi_analyzer_handoff/MANIFEST.sha256")
    return {
        "source": str(paths.pre_h1_tarball.relative_to(paths.repo_root)),
        "available": True,
        "source_kind": "checked-in handoff tarball",
        "source_revision": "unavailable-no-git-repository",
        "legacy_inference_member": "pi_analyzer_handoff/source/app/ml/inference.py",
        "legacy_inference_sha256": source_hash,
        "manifest_sha256": manifest_hash,
    }


def _tar_member_sha256(tarball: Path, member_name: str) -> str:
    digest = hashlib.sha256()
    with tarfile.open(tarball, "r:gz") as tar:
        member = tar.extractfile(member_name)
        if member is None:
            raise FileNotFoundError(member_name)
        for chunk in iter(lambda: member.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_parity_case(
    name: str,
    feature_frame: pd.DataFrame,
    pre: Any,
    post: Any,
) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    comparisons.append(compare_lists("feature_columns", list(feature_frame.columns), list(pre.windows[feature_frame.columns].columns)))
    comparisons.append(compare_series_exact("window_index", pre.windows, post.windows, "window_index"))
    comparisons.append(compare_series_exact("start_frame_index", pre.windows, post.windows, "start_frame_index"))
    comparisons.append(compare_series_exact("end_frame_index", pre.windows, post.windows, "end_frame_index"))
    comparisons.append(compare_series_exact("prediction", pre.windows, post.windows, "prediction"))
    comparisons.append(compare_series_exact("is_anomaly", pre.windows, post.windows, "is_anomaly"))
    for column in ["score_sample", "decision_score", "window_health_score"]:
        comparisons.append(compare_series_numeric(column, pre.windows, post.windows, column))
    comparisons.append(compare_lists("most_unusual_features", pre.most_unusual_features, post.most_unusual_features))

    pre_summary = stable_inference_summary(pre)
    post_summary = stable_inference_summary(post)
    comparisons.append(compare_dict("analysis_summary", pre_summary, post_summary))
    comparisons.append(compare_lists("event_boundaries", anomaly_events(pre.windows), anomaly_events(post.windows)))
    passed = all(item["passed"] for item in comparisons)
    return {
        "case": name,
        "window_count": int(len(feature_frame)),
        "passed": passed,
        "comparisons": comparisons,
        "post_h1_additive_metadata": {
            "detector_results_present": bool(getattr(post, "detector_results", [])),
            "detector_statuses": [
                item.get("status")
                for item in getattr(post, "detector_results", [])
            ],
        },
    }


def compare_historical_parity_case(name: str, historical_case: dict[str, Any], post: Any) -> dict[str, Any]:
    pre_windows = pd.DataFrame(historical_case["windows"])
    post_windows = post.windows.reset_index(drop=True)
    comparisons: list[dict[str, Any]] = []
    comparisons.append(compare_lists("feature_columns", historical_case["feature_columns"], list(post.feature_frame.columns)))
    comparisons.append(compare_lists("window_columns", historical_case["window_columns"], list(post_windows.columns)))
    comparisons.append(compare_series_exact("window_index", pre_windows, post_windows, "window_index"))
    comparisons.append(compare_series_exact("start_frame_index", pre_windows, post_windows, "start_frame_index"))
    comparisons.append(compare_series_exact("end_frame_index", pre_windows, post_windows, "end_frame_index"))
    comparisons.append(compare_series_exact("prediction", pre_windows, post_windows, "prediction"))
    comparisons.append(compare_series_exact("is_anomaly", pre_windows, post_windows, "is_anomaly"))
    for column in ["score_sample", "decision_score", "window_health_score"]:
        comparisons.append(compare_series_numeric(column, pre_windows, post_windows, column))
    comparisons.append(
        compare_dict(
            "analysis_summary",
            stable_summary_dict(historical_case["summary"]),
            stable_summary_dict(post.summary),
        )
    )
    comparisons.append(compare_lists("event_boundaries", anomaly_events(pre_windows), anomaly_events(post_windows)))
    passed = all(item["passed"] for item in comparisons)
    return {
        "case": name,
        "window_count": int(len(post.feature_frame)),
        "passed": passed,
        "historical_execution": {"available": True, "source": "pi_analyzer_handoff.tar.gz"},
        "comparisons": comparisons,
        "post_h1_additive_metadata": {
            "detectors_summary_present": "detectors" in post.summary,
            "detector_statuses": [
                item.get("status")
                for item in post.summary.get("detectors", [])
            ],
        },
    }


def stable_summary_dict(summary: dict[str, Any]) -> dict[str, Any]:
    return {field: summary.get(field) for field in STABLE_SUMMARY_FIELDS}


def stable_inference_summary(output: Any) -> dict[str, Any]:
    return {
        "anomaly_ratio": output.anomaly_ratio,
        "anomaly_window_count": output.anomaly_windows,
        "evidence_sufficient": output.evidence_sufficient,
        "evidence_window_count": output.evidence_window_count,
        "health_score": output.health_score,
        "minimum_windows_for_status": output.minimum_windows_for_status,
        "model_loaded": output.model_loaded,
        "most_unusual_features": output.most_unusual_features,
        "note": output.note,
        "overall_status": output.overall_status,
        "window_count": int(len(output.windows)),
    }


def compare_lists(name: str, left: list[Any], right: list[Any]) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "exact",
        "passed": left == right,
        "left_count": len(left),
        "right_count": len(right),
        "first_difference": first_difference(left, right),
    }


def compare_dict(name: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "exact_dict",
        "passed": left == right,
        "left": None if left == right else left,
        "right": None if left == right else right,
    }


def first_difference(left: list[Any], right: list[Any]) -> dict[str, Any] | None:
    for index, (left_item, right_item) in enumerate(zip(left, right, strict=False)):
        if left_item != right_item:
            return {"index": index, "left": left_item, "right": right_item}
    if len(left) != len(right):
        return {"index": min(len(left), len(right)), "left": len(left), "right": len(right)}
    return None


def compare_series_exact(name: str, left: pd.DataFrame, right: pd.DataFrame, column: str) -> dict[str, Any]:
    left_values = left[column].tolist() if column in left else []
    right_values = right[column].tolist() if column in right else []
    return compare_lists(name, left_values, right_values)


def compare_series_numeric(name: str, left: pd.DataFrame, right: pd.DataFrame, column: str) -> dict[str, Any]:
    if column not in left or column not in right:
        return {"name": name, "kind": "numeric", "passed": False, "reason": "missing column"}
    left_values = pd.to_numeric(left[column], errors="coerce").to_numpy(dtype=float)
    right_values = pd.to_numeric(right[column], errors="coerce").to_numpy(dtype=float)
    same_shape = left_values.shape == right_values.shape
    passed = bool(same_shape and np.allclose(left_values, right_values, atol=FLOAT_ATOL, rtol=FLOAT_RTOL, equal_nan=True))
    max_abs_diff = None
    if same_shape and left_values.size:
        max_abs_diff = float(np.nanmax(np.abs(left_values - right_values)))
    return {
        "name": name,
        "kind": "numeric",
        "passed": passed,
        "atol": FLOAT_ATOL,
        "rtol": FLOAT_RTOL,
        "count": int(left_values.size),
        "max_abs_diff": max_abs_diff,
    }


def run_public_api_compatibility(paths: EvaluationPaths, fixture_path: Path) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from app.main import create_app

    session_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="h2-api-compat-") as tmp:
        settings = Settings(data_dir=Path(tmp) / "data", model_dir=paths.model_dir)
        client = TestClient(create_app(settings))
        response = client.post("/api/v1/analysis", json=session_payload)
        body = response.json()
    stable_fields_present = all(field in body for field in STABLE_SUMMARY_FIELDS if field != "model_version")
    return {
        "endpoint": "POST /api/v1/analysis",
        "status_code": response.status_code,
        "passed": response.status_code == 200 and stable_fields_present,
        "stable_fields_present": stable_fields_present,
        "additive_fields": sorted(field for field in body if field not in STABLE_SUMMARY_FIELDS and field != "analysis_run_id"),
        "detectors_additive_metadata_present": "detectors" in body,
    }


def run_benchmark(paths: EvaluationPaths, manifest: dict[str, Any]) -> dict[str, Any]:
    settings = Settings(data_dir=paths.data_dir, model_dir=paths.model_dir)
    bundle = load_model_bundle(paths.model_dir)
    session_results: list[dict[str, Any]] = []
    for item in manifest["sessions"]:
        if item["dataset_kind"] != "canonical_telemetry":
            continue
        session_dir = paths.repo_root / item["path"]
        try:
            session = load_canonical_session_from_telemetry(session_dir)
            signal_columns = get_required_signal_columns(session.telemetry_schema_version)
            frame_data = canonical_session_to_frame_data(session, signal_columns)
            feature_frame = build_feature_frame(frame_data, settings, signal_columns)
            harness = ModelHarness([IsolationForestDetector(bundle)])
            started = perf_counter()
            harness_result = harness.run(
                feature_frame,
                DetectorContext(
                    telemetry_schema_version=session.telemetry_schema_version,
                    signal_columns=tuple(signal_columns),
                    minimum_windows_for_status=settings.min_session_windows_for_status,
                ),
            )
            latency_ms = round((perf_counter() - started) * 1000.0, 6)
            detector_results = [
                detector_benchmark_payload(result, feature_frame, frame_data, item.get("recording_duration_ms"), latency_ms)
                for result in harness_result.results
            ]
            session_results.append(
                {
                    "session_id": item["session_id"],
                    "dataset_kind": item["dataset_kind"],
                    "training_evaluation_split": item["training_evaluation_split"],
                    "window_count": int(len(feature_frame)),
                    "recording_duration_ms": item.get("recording_duration_ms"),
                    "detectors": detector_results,
                    "detector_disagreements": detector_disagreements(harness_result.results),
                }
            )
        except Exception as exc:  # noqa: BLE001 - benchmark records failed sessions.
            session_results.append(
                {
                    "session_id": item["session_id"],
                    "dataset_kind": item["dataset_kind"],
                    "training_evaluation_split": item["training_evaluation_split"],
                    "window_count": item.get("window_count"),
                    "recording_duration_ms": item.get("recording_duration_ms"),
                    "detectors": [
                        {
                            "detector_id": "isolation_forest",
                            "status": "failed",
                            "reason": "benchmark_exception",
                            "error": {"type": type(exc).__name__, "message": str(exc)},
                        }
                    ],
                    "detector_disagreements": {"available": False, "reason": "session failed"},
                }
            )

    return {
        "schema_version": "model-harness-benchmark-results-v1",
        "configuration": {
            "model_dir": str(paths.model_dir.relative_to(paths.repo_root)),
            "model_version": bundle.metadata.get("model_version_id") or bundle.metadata.get("version"),
            "detectors": ["isolation_forest"],
            "score_policy": "raw detector scores preserved; no cross-detector score normalization",
            "threshold_policy": "production inference thresholds unchanged",
        },
        "label_metrics": label_metrics_policy(manifest),
        "sessions": session_results,
        "summary": benchmark_summary(session_results),
    }


def detector_benchmark_payload(
    result: Any,
    feature_frame: pd.DataFrame,
    frame_data: pd.DataFrame,
    recording_duration_ms: float | None,
    latency_ms: float,
) -> dict[str, Any]:
    payload = result.to_summary()
    payload["metadata"] = result.metadata
    payload["latency_ms"] = latency_ms
    payload["score_distribution"] = score_distribution(result)
    event_windows = _windows_for_event_detection(result)
    events = anomaly_events(event_windows, frame_data=frame_data) if result.status == "ok" else []
    payload["events"] = {
        "count": len(events),
        "items": events,
        "total_duration_ms": round(sum(float(event.get("duration_ms") or 0.0) for event in events), 6),
    }
    hours = (recording_duration_ms or 0.0) / 3_600_000.0
    payload["alert_frequency_per_hour"] = None if hours <= 0 else round(len(events) / hours, 6)
    payload["anomaly_windows_per_hour"] = None
    if hours > 0 and result.anomaly_window_count is not None:
        payload["anomaly_windows_per_hour"] = round(result.anomaly_window_count / hours, 6)
    return payload


def score_distribution(result: Any) -> dict[str, Any] | None:
    column = result.anomaly_score_column
    if result.status != "ok" or not column or column not in result.windows:
        return None
    series = pd.to_numeric(result.windows[column], errors="coerce").dropna()
    if series.empty:
        return None
    return {
        "column": column,
        "direction": result.score_direction,
        "count": int(series.count()),
        "min": float(series.min()),
        "p10": float(series.quantile(0.10)),
        "median": float(series.median()),
        "p90": float(series.quantile(0.90)),
        "max": float(series.max()),
    }


def anomaly_events(windows: pd.DataFrame, frame_data: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    if windows.empty or "is_anomaly" not in windows:
        return []
    events: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for row in windows.to_dict(orient="records"):
        if _bool_or_none(row.get("is_anomaly")) is True:
            current.append(row)
        elif current:
            events.append(_event_from_rows(current, frame_data))
            current = []
    if current:
        events.append(_event_from_rows(current, frame_data))
    return events


def _event_from_rows(rows: list[dict[str, Any]], frame_data: pd.DataFrame | None) -> dict[str, Any]:
    start_sequence = int(rows[0].get("start_frame_index", rows[0].get("start_sequence", 0)))
    end_sequence = int(rows[-1].get("end_frame_index", rows[-1].get("end_sequence", start_sequence)))
    duration_ms = None
    if frame_data is not None and not frame_data.empty and "frame_index" in frame_data and "relative_time_ms" in frame_data:
        indexed = frame_data.set_index("frame_index")
        if start_sequence in indexed.index and end_sequence in indexed.index:
            start_ms = indexed.loc[start_sequence, "relative_time_ms"]
            end_ms = indexed.loc[end_sequence, "relative_time_ms"]
            if pd.notna(start_ms) and pd.notna(end_ms):
                duration_ms = float(end_ms) - float(start_ms)
    if duration_ms is None:
        duration_ms = float(sum(float(row.get("duration_ms") or 0.0) for row in rows))
    return {
        "start_window_index": int(rows[0].get("window_index", 0)),
        "end_window_index": int(rows[-1].get("window_index", 0)),
        "start_sequence": start_sequence,
        "end_sequence": end_sequence,
        "window_count": len(rows),
        "duration_ms": round(max(0.0, duration_ms), 6),
    }


def detector_disagreements(results: list[Any]) -> dict[str, Any]:
    ok_results = [result for result in results if result.status == "ok" and result.is_anomaly_column]
    if len(ok_results) < 2:
        return {"available": False, "reason": "fewer than two successful detectors"}
    pairs = []
    for left_index, left in enumerate(ok_results):
        for right in ok_results[left_index + 1 :]:
            left_values = [_bool_or_none(value) for value in left.windows[left.is_anomaly_column].tolist()]
            right_values = [_bool_or_none(value) for value in right.windows[right.is_anomaly_column].tolist()]
            aligned = list(zip(left_values, right_values, strict=False))
            compared = sum(1 for left_value, right_value in aligned if left_value is not None and right_value is not None)
            unavailable = len(aligned) - compared
            disagreements = sum(
                1
                for left_value, right_value in aligned
                if left_value is not None and right_value is not None and left_value != right_value
            )
            pairs.append(
                {
                    "left_detector_id": left.identity.detector_id,
                    "right_detector_id": right.identity.detector_id,
                    "compared_windows": compared,
                    "unavailable_windows": unavailable,
                    "window_disagreement_count": disagreements,
                    "window_disagreement_ratio": None if compared == 0 else round(disagreements / compared, 6),
                    "event_count_delta": len(anomaly_events(_windows_for_event_detection(left))) - len(
                        anomaly_events(_windows_for_event_detection(right))
                    ),
                }
            )
    return {"available": True, "pairs": pairs}


def _windows_for_event_detection(result: Any) -> pd.DataFrame:
    windows = result.windows.copy()
    column = result.is_anomaly_column
    if column and column in windows and column != "is_anomaly":
        windows["is_anomaly"] = windows[column]
    return windows


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return bool(value)


def label_metrics_policy(manifest: dict[str, Any]) -> dict[str, Any]:
    suitable = [
        session
        for session in manifest["sessions"]
        if session.get("training_evaluation_split", {}).get("suitable_for_precision_recall_f1")
    ]
    if not suitable:
        return {
            "computed": False,
            "reason": "no verified binary normal/fault labels are available; context labels are preserved but not scored as truth",
        }
    return {
        "computed": False,
        "reason": "verified labels exist but metric calculation is not implemented for this label schema",
        "eligible_session_count": len(suitable),
    }


def benchmark_summary(session_results: list[dict[str, Any]]) -> dict[str, Any]:
    detector_rows = [
        detector
        for session in session_results
        for detector in session.get("detectors", [])
    ]
    latencies = [
        float(detector["latency_ms"])
        for detector in detector_rows
        if detector.get("latency_ms") is not None
    ]
    status_counts: dict[str, int] = {}
    for detector in detector_rows:
        status = str(detector.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "session_count": len(session_results),
        "detector_result_count": len(detector_rows),
        "detector_status_counts": dict(sorted(status_counts.items())),
        "latency_ms": {
            "count": len(latencies),
            "median": round(statistics.median(latencies), 6) if latencies else None,
            "max": round(max(latencies), 6) if latencies else None,
        },
    }


def write_markdown_report(
    path: Path,
    manifest: dict[str, Any],
    parity: dict[str, Any],
    benchmark: dict[str, Any],
) -> None:
    lines = [
        "# H2 Model Harness Evaluation Report",
        "",
        "## Parity",
        "",
        f"- Pre-H1 source: `{parity['historical_source'].get('source')}`",
        f"- Git history available: `False`",
        f"- Parity passed: `{parity['passed']}`",
        f"- Cases: {', '.join(case['case'] for case in parity['cases'])}",
        "- Limitation: no `.git` directory is present; the checked-in handoff tarball is the historical source.",
        "",
        "## Dataset Manifest",
        "",
        f"- Canonical sessions: {manifest['summary']['canonical_session_count']}",
        f"- Evaluation-ready canonical sessions: {manifest['summary']['evaluation_ready_canonical_session_count']}",
        f"- Calibration datasets cataloged: {manifest['summary']['calibration_dataset_count']}",
        f"- Raw real-run sources cataloged: {manifest['summary']['raw_real_run_source_count']}",
        "- Unlabeled/context-labeled sessions are not treated as verified normal or faulty data.",
        "",
        "## Benchmark",
        "",
        f"- Sessions evaluated: {benchmark['summary']['session_count']}",
        f"- Detector statuses: `{benchmark['summary']['detector_status_counts']}`",
        f"- Median latency ms: `{benchmark['summary']['latency_ms']['median']}`",
        f"- Label metrics computed: `{benchmark['label_metrics']['computed']}`",
        f"- Label metrics reason: {benchmark['label_metrics']['reason']}",
        "",
        "## Outputs",
        "",
        "- `evaluation_manifest.json`",
        "- `parity_report.json`",
        "- `benchmark_results.json`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_all(paths: EvaluationPaths) -> dict[str, Path]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_evaluation_manifest(paths)
    parity = run_parity_validation(paths)
    benchmark = run_benchmark(paths, manifest)

    manifest_path = paths.output_dir / "evaluation_manifest.json"
    parity_path = paths.output_dir / "parity_report.json"
    benchmark_path = paths.output_dir / "benchmark_results.json"
    report_path = paths.output_dir / "report.md"
    write_json(manifest_path, manifest)
    write_json(parity_path, parity)
    write_json(benchmark_path, benchmark)
    write_markdown_report(report_path, manifest, parity, benchmark)
    return {
        "manifest": manifest_path,
        "parity": parity_path,
        "benchmark": benchmark_path,
        "report": report_path,
    }
