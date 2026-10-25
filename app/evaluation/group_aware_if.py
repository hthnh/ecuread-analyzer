"""H10.1 exploratory capture-held-out refits; models live only in /tmp."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
from importlib.metadata import version
from itertools import combinations
import json
import platform
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from app.config import Settings
from app.context_annotation.storage import file_sha256
from app.evaluation.context_benchmark_readiness import (
    CONTENT_SHA, EXPORT, MODEL, ROOT, SIGNALS, TRACKS, distribution,
    read_json, read_jsonl, require, stable_hash, union_duration, verify_manifest, verify_mapping,
)
from app.evaluation.model_harness_evaluation import anomaly_events
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.ml.artifacts import load_model_bundle
from app.ml.features import get_feature_names
from app.ml.inference import run_inference
from app.ml.training import train_isolation_forest
from app.services.analysis_service import build_feature_frame, canonical_session_to_frame_data


STUDY = ROOT / "data/evaluation/h10_1"
REPORT = ROOT / "docs/research/H10_1_GROUP_AWARE_IF_EVALUATION.md"
FEATURES = get_feature_names(SIGNALS)
CONTRASTS = (
    ("macro_operating", "stationary_running", "moving_episode"),
    ("macro_operating", "moving_steady", "moving_episode"),
    ("macro_thermal", "warming", "stable"),
    ("micro_operating", "stationary_running", "moving_variable"),
    ("micro_operating", "moving_steady", "moving_variable"),
    ("micro_thermal", "warming", "stable"),
)


def write_json(path, value):
    with Path(path).open("x") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def protected_inputs():
    paths = set()
    for name in ("app", "tests", "real_data", "docs", "static", "templates", "data/models",
                 "data/context_annotation", "data/calibration", "data/decoder-audit", "data/evaluation"):
        paths.update(p for p in (ROOT / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts
                     and not p.is_relative_to(STUDY) and p != REPORT)
    paths.update(ROOT / name for name in ("pyproject.toml", "Dockerfile", "docker-compose.yml"))
    return {str(p.relative_to(ROOT)): file_sha256(p) for p in sorted(paths)}


def check_protected(expected):
    current = protected_inputs()
    changed = [name for name, sha in expected.items() if current.get(name) != sha]
    require(not changed, f"protected artifacts changed: {changed}")
    return {"pinned_files_checked": len(expected), "changed_files": changed,
            "new_unpinned_files": sorted(set(current) - set(expected))}


def array_sha(array):
    a = np.ascontiguousarray(array, dtype="<f8")
    return hashlib.sha256(str(a.shape).encode() + b"\0" + a.tobytes()).hexdigest()


def make_folds(sources):
    require(len(sources) == len({s["physical_capture_group_id"] for s in sources}) == 18, "expected 18 distinct attested groups")
    folds = []
    for held in sorted(sources, key=lambda s: s["session_id"]):
        training = [s for s in sorted(sources, key=lambda s: s["session_id"]) if s["physical_capture_group_id"] != held["physical_capture_group_id"]]
        require(len(training) == 17, "capture group appears in both partitions")
        folds.append({"fold_id": "logo-" + held["session_id"], "held_out": held,
                      "training": training, "training_windows": sum(s["window_count"] for s in training),
                      "held_out_windows": held["window_count"]})
    return folds


def preregister(study=STUDY):
    require(study.resolve() == STUDY, "use the dedicated H10.1 study directory")
    require(not study.exists(), "preregistration/results already exist; refusing overwrite")
    manifest, audit = verify_manifest()
    settings, meta = Settings(), read_json(MODEL / "model_metadata.json")
    policy = {"n_estimators": settings.isolation_n_estimators, "contamination": settings.isolation_contamination,
              "random_state": settings.isolation_random_state, "max_samples": "auto", "max_features": 1.0,
              "bootstrap": False, "n_jobs": -1}
    require(policy["n_estimators"] == meta["n_estimators"] and policy["contamination"] == meta["contamination"]
            and policy["random_state"] == meta["hyperparameters"]["random_state"], "environment differs from pinned IF policy")
    require((settings.window_size_samples, settings.window_step_samples) == (50, 10), "window contract mismatch")
    require(meta["feature_names"] == FEATURES and meta["signal_columns"] == SIGNALS and meta["feature_schema_version"] == "ecu-window-features-v1", "native model feature contract mismatch")
    sources = [{"session_id": s["session_id"], "source_path": s["source_path"], "source_sha256": s["source_sha256"],
                "physical_capture_group_id": s["physical_capture_group_id"], "window_count": s["mapping"]["windows"],
                "sample_count": s["canonical_sample_count"], "duration_s": s["duration_s"]} for s in audit["sessions"]]
    protocol = {"schema_version": "h10.1-protocol-v1", "study_id": "h10.1-group-if-exploratory-v1", "registered_at": datetime.now(UTC).isoformat(),
        "software": {"python": platform.python_version(), **{p: version(p) for p in ("scikit-learn", "numpy", "pandas", "joblib")}},
        "study_type": "EXPLORATORY; prior corpus-wide model selection exposure, not independent confirmatory validation",
        "export_content_sha256": CONTENT_SHA, "export_manifest_sha256": file_sha256(EXPORT / "export_manifest.json"),
        "grouping": "operator_attested_capture_groups_v1", "machine_verified_independence": None,
        "outer_folds": 18, "train_groups_per_fold": 17, "source_hashes": manifest["source_hashes"],
        "feature_schema": "ecu-window-features-v1", "telemetry_schema": "canonical-telemetry-v2",
        "decoder": "honda_keihin_71_17:1.0.0", "feature_names": FEATURES, "signal_columns": SIGNALS,
        "window_size": 50, "window_step": 10, "hyperparameters": policy,
        "quality_policy": {"max_checksum_error_ratio": settings.max_window_checksum_error_ratio,
                           "max_invalid_decoded_ratio": settings.max_window_invalid_decoded_ratio},
        "preprocessing": "Existing per-window feature extraction unchanged. Fit default RobustScaler on all training windows only, in sorted session/window order. No balancing, resampling, global normalization, imputation beyond existing extractor, or held-out preprocessing fit.",
        "threshold": "Existing contamination=0.03 learns offset from training windows; held-out prediction uses unchanged decision_function < 0. No cutoff or hyperparameter search.",
        "training_truth": "Unlabeled captures, not independently confirmed healthy data; long captures have greater training-window weight as in original baseline.",
        "primary_population": "All 18 captures and all 9989 ML-eligible windows, including short, ambiguous, unknown and discrepant observations",
        "contrasts": [list(c) for c in CONTRASTS],
        "context_policy": "Approved-v2 conservative mappings only; fine/macro separate, no majority voting. Context labels are strata, not fault truth. Missing contexts report unsupported, never zero burden.",
        "statistics": {"principal_unit": "capture", "bootstrap_seed": 42, "bootstrap_draws": 10000,
                       "interval": "Descriptive 2.5/97.5 capture-bootstrap percentiles; paired context differences on common-support captures, no interval with fewer than 3 groups. No p-values or confirmatory significance claim; overlapping windows/fold training sets are dependent.",
                       "summaries": "Equal-capture first; duration-weighted capture summary second; pooled windows secondary. Within-context weights use eligible window-time union. Report consecutive flagged-window runs and union-time burden; non-stratum windows break stratum runs.",
                       "dominance": "Report sign counts and leave-one/two-capture-out effect ranges; no detector refitting to select contrasts.",
                       "support": "Per-fold context support: >=3 training capture groups, >=1 held-out window; report training/test window counts, group counts and time. This research gate does not delete states or alter production thresholds."},
        "sensitivity": {"extremely_short": f"Aggregation-only view excluding captures with fewer than existing min_session_windows_for_status={settings.min_session_windows_for_status}; primary and training unchanged",
                        "short_window_cutoff": settings.min_session_windows_for_status,
                        "discrepancies": "Aggregation-only view masking 001 windows 0-5, the six windows intersecting the 19 retained speed/label discrepancies. No annotation mutation or boundary adjudication."},
        "special_captures": ["session_000001", "session_000014", "session_000020", "session_000023", "session_000004"],
        "manual_boundary_adjudication": "pending genuine human review", "fault_ground_truth": "not supplied",
        "forbidden": ["PCA fitting", "context-conditioned IF fitting", "context threshold calibration", "production writes/promotion", "RCA/IF-derived fault truth"],
        "artifact_storage": "All model/scaler/feature-cache artifacts in a new /tmp/ecuread-h10-1-* directory; metadata/hashes/results only in study output",
        "reproducibility": "Repeat all 18 fits from the pinned feature cache, compare model/scaler SHA, offsets and every prediction. Validate training-only scaler quantiles and offset for every fold; poison held-out features in the repeat to test fitting invariance."}
    protocol["decision_policy"] = "Exploratory context_experiment_justified only if a preregistered macro stationary or warming flag contrast has >=3 common-support groups, positive mean after every single-group deletion, and retains positive direction in both short-capture and discrepancy sensitivities. Otherwise insufficient_evidence; absence of evidence is not baseline adequacy. Uncertainty and conditioning support must be disclosed; no promotion/calibration in H10.1."
    protected = protected_inputs()
    study.mkdir(parents=True)
    write_json(study / "protocol.json", protocol)
    write_json(study / "fold_manifest.json", {"schema_version": "h10.1-folds-v1", "export_content_sha256": CONTENT_SHA, "folds": make_folds(sources)})
    write_json(study / "protected_inputs.json", protected)
    write_json(study / "preregistration.json", {"registered_at": protocol["registered_at"], "training_started": False,
        "protocol_sha256": file_sha256(study / "protocol.json"), "fold_manifest_sha256": file_sha256(study / "fold_manifest.json"),
        "protected_inputs_sha256": file_sha256(study / "protected_inputs.json")})
    print("Preregistered before training:", file_sha256(study / "protocol.json"), flush=True)


def load_registration(study=STUDY):
    registration = read_json(study / "preregistration.json")
    for name in ("protocol", "fold_manifest", "protected_inputs"):
        require(file_sha256(study / (name + ".json")) == registration[name + "_sha256"], "preregistration changed: " + name)
    protocol = read_json(study / "protocol.json")
    require(protocol["export_content_sha256"] == CONTENT_SHA, "unrecognized research export")
    return protocol, read_json(study / "fold_manifest.json")["folds"], read_json(study / "protected_inputs.json")


def training_matrix(fold, frames):
    ids = [s["session_id"] for s in fold["training"]]
    held = fold["held_out"]["session_id"]
    require(held not in ids and len(ids) == len(set(ids)) == 17, "training/test identity leakage")
    train_groups = {s["physical_capture_group_id"] for s in fold["training"]}
    require(fold["held_out"]["physical_capture_group_id"] not in train_groups and len(train_groups) == 17, "training/test physical group leakage")
    require(fold["held_out"]["source_sha256"] not in {s["source_sha256"] for s in fold["training"]}, "training/test source SHA leakage")
    result = pd.concat([frames[id][FEATURES] for id in ids], ignore_index=True)
    require(len(result) == fold["training_windows"], "training window count differs")
    return result


def settings_for(protocol):
    p = protocol["hyperparameters"]
    return Settings(window_size_samples=protocol["window_size"], window_step_samples=protocol["window_step"],
                    isolation_n_estimators=p["n_estimators"], isolation_contamination=p["contamination"], isolation_random_state=p["random_state"],
                    max_window_checksum_error_ratio=protocol["quality_policy"]["max_checksum_error_ratio"],
                    max_window_invalid_decoded_ratio=protocol["quality_policy"]["max_invalid_decoded_ratio"])


def fit_fold(fold, frames, protocol, model_dir):
    model_dir = Path(model_dir).resolve()
    require(model_dir.is_relative_to(Path("/tmp")) and not model_dir.exists(), "fits require a new disposable /tmp directory")
    train = training_matrix(fold, frames)
    x = train[FEATURES].to_numpy(dtype=float)
    require(bool(np.isfinite(x).all()), "nonfinite training feature matrix")
    train_hash = array_sha(x)
    meta = train_isolation_forest(train, [s["session_id"] for s in fold["training"]], settings_for(protocol), model_dir=model_dir,
        signal_columns=SIGNALS, training_decoder_versions=[protocol["decoder"]],
        notes="H10.1 EXPLORATORY; unlabeled training groups, not confirmed healthy; disposable research only",
        extra_metadata={"model_version_id": protocol["study_id"] + ":" + fold["fold_id"], "version": protocol["study_id"] + ":" + fold["fold_id"],
                        "telemetry_schema_version": protocol["telemetry_schema"], "decoder_id": "honda_keihin_71_17", "decoder_version": "1.0.0",
                        "fold_id": fold["fold_id"], "export_content_sha256": CONTENT_SHA, "training_feature_sha256": train_hash,
                        "held_out_group": fold["held_out"]["physical_capture_group_id"], "training_sources": fold["training"],
                        "protocol_sha256": file_sha256(STUDY / "protocol.json")})
    bundle = load_model_bundle(model_dir)
    center = np.median(x, axis=0)
    scale = np.quantile(x, .75, axis=0) - np.quantile(x, .25, axis=0)
    scale[scale < 10 * np.finfo(float).eps] = 1.0
    require(np.allclose(bundle.scaler.center_, center, rtol=0, atol=1e-10) and np.allclose(bundle.scaler.scale_, scale, rtol=0, atol=1e-10), "scaler differs from training-only quantiles")
    expected_offset = float(np.percentile(bundle.model.score_samples(bundle.scaler.transform(train)), 100 * protocol["hyperparameters"]["contamination"]))
    require(abs(bundle.model.offset_ - expected_offset) < 1e-12, "offset not learned solely from training rows")
    require(bundle.model.n_estimators == 200 and bundle.model.random_state == 42 and bundle.model.max_samples == "auto"
            and bundle.model.max_features == 1.0 and bundle.model.bootstrap is False, "IF architecture changed")
    return {"fold_id": fold["fold_id"], "trained_at": meta["created_at"], "artifact_dir": str(model_dir), "metadata": meta,
            "training_windows": len(train), "training_feature_sha256": train_hash, "learned_offset": float(bundle.model.offset_),
            "artifact_sha256": {p.name: file_sha256(p) for p in sorted(model_dir.iterdir()) if p.is_file()},
            "training_only_scaler_verified": True, "training_only_offset_verified": True,
            "training_group_intersection_test": [], "training_source_intersection_test": []}


def load_features(protocol, audit):
    frames, sessions, summaries = {}, {}, {}
    settings = settings_for(protocol)
    for source in audit["sessions"]:
        sid, path = source["session_id"], ROOT / source["source_path"]
        require(file_sha256(path) == protocol["source_hashes"][sid], "source SHA mismatch")
        canonical = import_native_jsonl_file(path, session_id=sid).canonical_session
        require(canonical.telemetry_schema_version == protocol["telemetry_schema"] and canonical.decoder_version == "1.0.0", "decoder/schema mismatch")
        require(all(s.frame_valid and s.checksum_valid and all(getattr(s, k) is not None for k in SIGNALS) for s in canonical.samples), "invalid/ineligible source evidence")
        raw = canonical_session_to_frame_data(canonical, SIGNALS)
        feature = build_feature_frame(raw, settings, SIGNALS)
        labels, windows = verify_mapping(source, canonical)
        require(len(feature) == len(windows) and feature["start_frame_index"].tolist() == [w["row_start"] for w in windows]
                and feature["end_frame_index"].tolist() == [w["row_end"] for w in windows], "canonical/feature window identity mismatch")
        frames[sid] = feature
        sessions[sid] = {**source, "labels": labels, "windows": windows, "frame_data": raw}
        summaries[sid] = {"signals": {k: distribution(raw[k]) for k in SIGNALS},
                          "speed_kmh": distribution(bytes.fromhex(r["raw_hex"])[19] for r in read_jsonl(path)),
                          "timestamp_interval_ms": distribution(np.diff(raw["relative_time_ms"])),
                          "invalid_frames": 0, "missing_model_signals": 0, "decoder_schema_issues": [],
                          "canonical_samples": len(canonical.samples), "feature_matrix_sha256": array_sha(feature[FEATURES].to_numpy())}
        print(f"verified features {sid}: {len(feature)} windows", flush=True)
    return frames, sessions, summaries


def window_rows(fold, scored, session, artifact):
    rows = []
    feature_sha = array_sha(session["features"][FEATURES].to_numpy())
    for w, r in zip(session["windows"], scored.to_dict("records"), strict=True):
        require(int(r["window_index"]) == w["window_index"], "prediction window identity mismatch")
        rows.append({"fold_id": fold["fold_id"], "session_id": session["session_id"], "capture_group": session["physical_capture_group_id"],
            "source_sha256": session["source_sha256"], "window_index": w["window_index"], "row_start": w["row_start"], "row_end": w["row_end"],
            "start_s": w["start_s"], "end_s": w["end_s"], "sample_count": 50,
            "score_sample": float(r["score_sample"]), "decision_score": float(r["decision_score"]),
            "prediction": int(r["prediction"]), "is_anomaly": bool(r["is_anomaly"]), "learned_offset": artifact["learned_offset"],
            "feature_matrix_sha256": feature_sha,
            "model_sha256": artifact["artifact_sha256"]["isolation_forest.joblib"], "scaler_sha256": artifact["artifact_sha256"]["robust_scaler.joblib"],
            "contexts": w["tracks"], "event_ids": w["event_ids"],
            "speed_context_discrepancy_window": session["session_id"] == "session_000001" and w["window_index"] <= 5})
    return rows


def stratum(row, track):
    a = row["contexts"][track]
    return a["assigned_label"] if a["status"] in ("homogeneous", "abstained") else "__" + a["status"]


def burden(rows, mask, session, labeled_s=None):
    selected = [r for r, use in zip(rows, mask, strict=True) if use]
    if not selected:
        return {"windows": 0, "supported": False, "flagged_windows": 0, "flag_frequency": None,
                "eligible_time_union_s": 0, "flag_time_union_s": 0, "flag_time_fraction": None,
                "labeled_context_duration_s": labeled_s,
                "run_count": 0, "runs_per_eligible_hour": None, "runs_per_labeled_context_hour": None,
                "score_sample": distribution([]), "decision_score": distribution([])}
    small = pd.DataFrame([{"window_index": r["window_index"], "start_frame_index": r["row_start"], "end_frame_index": r["row_end"],
                           "duration_ms": (r["end_s"] - r["start_s"]) * 1000, "is_anomaly": bool(use and r["is_anomaly"])}
                          for r, use in zip(rows, mask, strict=True)])
    events = anomaly_events(small, session["frame_data"])
    time = union_duration((r["start_s"], r["end_s"]) for r in selected)
    flagged_time = union_duration((r["start_s"], r["end_s"]) for r in selected if r["is_anomaly"])
    flags = sum(r["is_anomaly"] for r in selected)
    return {"supported": True, "windows": len(selected), "flagged_windows": flags, "flag_frequency": flags / len(selected),
            "eligible_time_union_s": time, "labeled_context_duration_s": labeled_s, "flag_time_union_s": flagged_time,
            "flag_time_fraction": flagged_time / time if time else None, "run_count": len(events), "runs": events,
            "runs_per_eligible_hour": len(events) * 3600 / time if time else None,
            "runs_per_labeled_context_hour": len(events) * 3600 / labeled_s if labeled_s else None,
            "score_sample": distribution(r["score_sample"] for r in selected), "decision_score": distribution(r["decision_score"] for r in selected)}


def capture_results(sessions, predictions, exclude_short=False, exclude_discrepancies=False):
    results = []
    for sid, session in sessions.items():
        rows = predictions[sid]
        if exclude_short and len(rows) < read_json(STUDY / "protocol.json")["sensitivity"]["short_window_cutoff"]:
            continue
        active = [not (exclude_discrepancies and r["speed_context_discrepancy_window"]) for r in rows]
        overall = burden(rows, active, session, session["duration_s"])
        contexts = {}
        for track in TRACKS:
            present = sorted({stratum(r, track) for r in rows})
            declared = {r["label"] for r in session["labels"]["tracks"][track]}
            contexts[track] = {}
            for label in sorted(set(present) | declared):
                length = sum(r["end_s"] - r["start_s"] for r in session["labels"]["tracks"][track] if r["label"] == label) if not label.startswith("__") else None
                contexts[track][label] = burden(rows, [use and stratum(r, track) == label for use, r in zip(active, rows, strict=True)], session, length)
        results.append({"session_id": sid, "capture_group": session["physical_capture_group_id"], "source_sha256": session["source_sha256"],
                        "duration_s": session["duration_s"], "overall": overall, "contexts": contexts})
    return results


def bootstrap_interval(values, seed=42, draws=10000):
    x = np.asarray(values, dtype=float)
    if len(x) < 3:
        return {"groups": len(x), "interval": None, "reason": "fewer than 3 supported capture groups"}
    means = np.random.default_rng(seed).choice(x, size=(draws, len(x)), replace=True).mean(axis=1)
    return {"groups": len(x), "mean": float(x.mean()), "interval": [float(v) for v in np.quantile(means, [.025, .975])],
            "method": "descriptive capture bootstrap; folds share training data, not confirmatory inference", "seed": seed, "draws": draws}


def aggregate(items, weight="eligible_time_union_s"):
    supported = [i for i in items if i["windows"]]
    if not supported:
        return {"capture_count": 0, "supported": False}
    windows = sum(i["windows"] for i in supported)
    flags = sum(i["flagged_windows"] for i in supported)
    seconds = sum(i["eligible_time_union_s"] for i in supported)
    weights = [i[weight] for i in supported]
    labeled_seconds = sum(i.get("labeled_context_duration_s") or 0 for i in supported)
    labeled_rates = [i["runs_per_labeled_context_hour"] for i in supported if i.get("runs_per_labeled_context_hour") is not None]
    return {"supported": True, "capture_count": len(supported), "windows": windows, "flagged_windows": flags,
            "equal_capture_flag_frequency": float(np.mean([i["flag_frequency"] for i in supported])),
            "duration_weighted_flag_frequency": float(np.average([i["flag_frequency"] for i in supported], weights=weights)),
            "pooled_window_flag_frequency": flags / windows, "equal_capture_flag_frequency_uncertainty": bootstrap_interval([i["flag_frequency"] for i in supported]),
            "equal_capture_score_median": distribution(i["score_sample"]["median"] for i in supported),
            "equal_capture_decision_median": distribution(i["decision_score"]["median"] for i in supported),
            "equal_capture_run_rate_per_hour": float(np.mean([i["runs_per_eligible_hour"] for i in supported])),
            "equal_capture_runs_per_labeled_context_hour": float(np.mean(labeled_rates)) if labeled_rates else None,
            "pooled_runs_per_labeled_context_hour": sum(i["run_count"] for i in supported) * 3600 / labeled_seconds if labeled_seconds else None,
            "labeled_context_duration_s": labeled_seconds,
            "total_runs": sum(i["run_count"] for i in supported), "eligible_time_union_s": seconds,
            "pooled_runs_per_eligible_hour": sum(i["run_count"] for i in supported) * 3600 / seconds,
            "pooled_flag_time_fraction": sum(i["flag_time_union_s"] for i in supported) / seconds,
            "largest_capture_share_of_flags": max(i["flagged_windows"] for i in supported) / flags if flags else 0}


def context_summary(captures, predictions):
    result = {"overall": aggregate([{**c["overall"], "duration_s": c["duration_s"]} for c in captures], weight="duration_s"), "tracks": {}}
    ids = {c["session_id"] for c in captures}
    for track in TRACKS:
        result["tracks"][track] = {}
        labels = sorted({label for c in captures for label in c["contexts"][track]})
        for label in labels:
            items = [c["contexts"][track][label] for c in captures if label in c["contexts"][track]]
            pooled = [r for sid, rows in predictions.items() if sid in ids for r in rows if stratum(r, track) == label]
            result["tracks"][track][label] = {**aggregate(items), "pooled_score_sample": distribution(r["score_sample"] for r in pooled),
                                              "pooled_decision_score": distribution(r["decision_score"] for r in pooled)}
    return result


def paired_contrasts(captures):
    contrasts = []
    for track, left, right in CONTRASTS:
        pairs = []
        for c in captures:
            a, b = (c["contexts"][track].get(label, {}) for label in (left, right))
            if a.get("windows", 0) and b.get("windows", 0):
                pairs.append({"session_id": c["session_id"], "left_windows": a["windows"], "right_windows": b["windows"],
                              "flag_frequency_difference": a["flag_frequency"] - b["flag_frequency"],
                              "flag_time_fraction_difference": a["flag_time_fraction"] - b["flag_time_fraction"],
                              "score_median_difference": a["score_sample"]["median"] - b["score_sample"]["median"],
                              "decision_median_difference": a["decision_score"]["median"] - b["decision_score"]["median"],
                              "run_rate_difference": a["runs_per_eligible_hour"] - b["runs_per_eligible_hour"]})
        values = [p["flag_frequency_difference"] for p in pairs]
        mean = float(np.mean(values)) if values else None
        removed = {}
        for n in (1, 2):
            effects = []
            if len(values) >= n + 3:
                for omit in combinations(range(len(values)), n):
                    effects.append(float(np.mean([v for i, v in enumerate(values) if i not in omit])))
            removed[str(n)] = {"range": [min(effects), max(effects)] if effects else None,
                               "positive_in_all": bool(effects) and min(effects) > 0}
        contrasts.append({"track": track, "left": left, "right": right, "pairs": pairs,
            "supported_groups": len(pairs), "mean_paired_flag_difference": mean,
            "positive_groups": sum(v > 0 for v in values), "negative_groups": sum(v < 0 for v in values), "ties": sum(v == 0 for v in values),
            "paired_flag_uncertainty": bootstrap_interval(values), "paired_time_burden_uncertainty": bootstrap_interval([p["flag_time_fraction_difference"] for p in pairs]),
            "paired_run_rate_uncertainty": bootstrap_interval([p["run_rate_difference"] for p in pairs]),
            "leave_capture_out_influence": removed})
    return contrasts


def context_eligibility(folds, sessions):
    rows = []
    for fold in folds:
        held = fold["held_out"]["session_id"]
        for track in TRACKS:
            all_labels = {r["label"] for s in sessions.values() for r in s["labels"]["tracks"][track]}
            for label in sorted(all_labels):
                support = {}
                for source in fold["training"]:
                    sid = source["session_id"]
                    windows = [w for w in sessions[sid]["windows"] if w["tracks"][track]["assigned_label"] == label and w["tracks"][track]["status"] == "homogeneous"]
                    if windows:
                        support[sid] = {"windows": len(windows), "time_union_s": union_duration((w["start_s"], w["end_s"]) for w in windows)}
                test = [w for w in sessions[held]["windows"] if w["tracks"][track]["assigned_label"] == label and w["tracks"][track]["status"] == "homogeneous"]
                rows.append({"fold_id": fold["fold_id"], "track": track, "label": label, "train_capture_groups": len(support),
                             "train_windows": sum(c["windows"] for c in support.values()), "train_support": support,
                             "held_out_windows": len(test), "held_out_time_union_s": union_duration((w["start_s"], w["end_s"]) for w in test),
                             "supported_for_future_conditioning": len(support) >= 3 and bool(test) and label != "unknown",
                             "unsupported_reasons": (["unknown_is_abstention"] if label == "unknown" else []) + (["fewer_than_3_training_groups"] if len(support) < 3 else []) + (["no_homogeneous_known_test_windows"] if not test else [])})
    return {"fold_context_rows": rows, "primary_window_count": sum(len(s["windows"]) for s in sessions.values()),
            "per_capture_axis_status": {sid: {t: dict(Counter(w["tracks"][t]["status"] for w in s["windows"])) for t in TRACKS} for sid, s in sessions.items()},
            "note": "Denominators include ambiguous/unknown windows separately. Conditioning support is a research feasibility gate, not a trained model or deletion rule."}


def investigate(protocol, sessions, frames, summaries, predictions, artifacts, captures):
    old = read_json(ROOT / "data/evaluation/h10/h10_baseline_context_audit.json")
    result = {}
    for sid in protocol["special_captures"]:
        fold = next(f for f in read_json(STUDY / "fold_manifest.json")["folds"] if f["held_out"]["session_id"] == sid)
        train = training_matrix(fold, frames)
        held = frames[sid][FEATURES]
        center, q01, q99 = train.median(), train.quantile(.01), train.quantile(.99)
        scale = (train.quantile(.75) - train.quantile(.25)).replace(0, 1)
        departures = []
        for feature in FEATURES:
            value = held[feature]
            departures.append({"feature": feature, "held_out_median": float(value.median()), "train_median": float(center[feature]),
                               "train_p01": float(q01[feature]), "train_p99": float(q99[feature]),
                               "fraction_outside_train_p01_p99": float(((value < q01[feature]) | (value > q99[feature])).mean()),
                               "median_difference_in_train_iqr": float(abs(value.median() - center[feature]) / scale[feature])})
        prior = next(c for c in old["per_capture"] if c["session_id"] == sid)
        current = next(c for c in captures if c["session_id"] == sid)
        result[sid] = {"measured_signals": summaries[sid], "held_out_results": current,
                       "old_in_sample_or_selection_exposed_comparator": {k: prior[k] for k in ("windows", "flagged_windows", "flag_frequency", "flag_event_count")},
                       "feature_departures": sorted(departures, key=lambda r: (-r["fraction_outside_train_p01_p99"], -r["median_difference_in_train_iqr"])),
                       "offset": next(a["learned_offset"] for a in artifacts if a["fold_id"] == fold["fold_id"]),
                       "manual_boundary_adjudication": "pending", "physical_fault_truth": "unavailable; no fault/false-alarm inference from flags"}
    return result


def decide(contrasts, sensitivity):
    target = [c for c in contrasts if (c["track"], c["left"]) in (("macro_operating", "stationary_running"), ("macro_thermal", "warming"))]
    stable = []
    for c in target:
        matches = [next(x for x in view["contrasts"] if (x["track"], x["left"], x["right"]) == (c["track"], c["left"], c["right"])) for view in sensitivity.values()]
        if (c["supported_groups"] >= 3 and c["mean_paired_flag_difference"] > 0
                and c["leave_capture_out_influence"]["1"]["positive_in_all"]
                and all(x["mean_paired_flag_difference"] is not None and x["mean_paired_flag_difference"] > 0 for x in matches)):
            stable.append(c)
    return {"verdict": "context_experiment_justified" if stable else "insufficient_evidence",
            "rationale": "Exploratory group-paired context dependence warrants a train/calibration-group-only calibration pilot, not promotion or fault-accuracy claims." if stable else "Group-paired evidence insufficient to justify calibration yet; do not equate lack of evidence with baseline adequacy.",
            "architecture_decision": "Keep production IF/thresholds unchanged. Do not train conditioned IF or PCA in H10.1. Prefer a later preregistered same-IF calibration study after real boundary adjudication.",
            "decision_rule": "Positive paired macro stationary or warming flag contrast with >=3 common-support captures, positive mean after every single-capture deletion, and positive direction in both preregistered sensitivities. Descriptive judgement, not significance or fault accuracy.",
            "manual_boundary_adjudication": "pending", "study_type": "EXPLORATORY", "verified_fault_sensitivity": "not evaluated"}


def run():
    protocol, folds, protected = load_registration()
    require(not (STUDY / "fitted_artifact_provenance.json").exists(), "experiment already run; refusing overwrite")
    check_protected(protected)
    _, audit = verify_manifest()
    started = datetime.now(UTC).isoformat()
    require(started > protocol["registered_at"], "training precedes preregistration")
    disposable = Path(tempfile.mkdtemp(prefix="ecuread-h10-1-", dir="/tmp"))
    frames, sessions, summaries = load_features(protocol, audit)
    for sid in sessions:
        sessions[sid]["features"] = frames[sid]
    cache = disposable / "native_v2_features.npz"
    np.savez(cache, **{sid: frame[FEATURES].to_numpy(dtype=float) for sid, frame in frames.items()})
    artifacts, predictions = [], {}
    for fold in folds:
        sid = fold["held_out"]["session_id"]
        artifact = fit_fold(fold, frames, protocol, disposable / fold["fold_id"])
        out = run_inference(frames[sid], artifact["artifact_dir"], expected_telemetry_schema_version=protocol["telemetry_schema"], expected_signal_columns=SIGNALS)
        require(out.overall_status != "model_unavailable" and out.model_loaded and len(out.windows) == fold["held_out_windows"], "held-out scoring incomplete/incompatible")
        predictions[sid] = window_rows(fold, out.windows, sessions[sid], artifact)
        artifacts.append(artifact)
        print(f"{fold['fold_id']}: train={artifact['training_windows']} test={len(out.windows)} flags={out.anomaly_windows} offset={artifact['learned_offset']:.9f}", flush=True)
    captures = capture_results(sessions, predictions)
    contexts = context_summary(captures, predictions)
    contrasts = paired_contrasts(captures)
    sensitivity = {}
    for label, short, discrepancies in (("exclude_extremely_short_captures", True, False), ("mask_discrepancy_windows", False, True)):
        selected = capture_results(sessions, predictions, exclude_short=short, exclude_discrepancies=discrepancies)
        selected_predictions = {sid: [r for r in rows if not(discrepancies and r["speed_context_discrepancy_window"])] for sid, rows in predictions.items()}
        sensitivity[label] = {"capture_ids": [c["session_id"] for c in selected], "contrasts": paired_contrasts(selected),
                              "summary": context_summary(selected, selected_predictions), "models_refitted": False}
    validation = check_protected(protected)
    validation.update({"preregistered_before_training": True, "training_started": started, "all_18_folds_group_source_disjoint": True,
                       "training_only_scalers_offsets_verified": True, "all_9989_windows_scored_once": sum(len(r) for r in predictions.values()) == 9989,
                       "source_samples_verified": sum(s["canonical_sample_count"] for s in sessions.values()), "manual_adjudication": "pending"})
    write_json(STUDY / "fitted_artifact_provenance.json", {"protocol_sha256": file_sha256(STUDY / "protocol.json"),
        "disposable_root": str(disposable), "feature_cache": str(cache), "feature_cache_sha256": file_sha256(cache), "folds": artifacts})
    with (STUDY / "held_out_predictions.jsonl").open("x") as handle:
        for sid in sorted(predictions):
            for row in predictions[sid]:
                handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    for name, value in (("per_capture_results", captures), ("context_results", contexts), ("context_eligibility", context_eligibility(folds, sessions)),
                        ("uncertainty_sensitivity", {"primary_paired_contrasts": contrasts, "sensitivity_views": sensitivity}),
                        ("special_capture_investigation", investigate(protocol, sessions, frames, summaries, predictions, artifacts, captures)),
                        ("leakage_invariance_validation", validation), ("final_decision", decide(contrasts, sensitivity))):
        write_json(STUDY / (name + ".json"), value)
    print("Complete:", read_json(STUDY / "final_decision.json")["verdict"], flush=True)


def verify_refits():
    protocol, folds, protected = load_registration()
    record = read_json(STUDY / "fitted_artifact_provenance.json")
    cache = Path(record["feature_cache"])
    require(file_sha256(cache) == record["feature_cache_sha256"], "feature cache changed")
    saved = {a["fold_id"]: a for a in record["folds"]}
    predictions = read_jsonl(STUDY / "held_out_predictions.jsonl")
    with np.load(cache, allow_pickle=False) as arrays:
        frames = {sid: pd.DataFrame(arrays[sid], columns=FEATURES) for sid in sorted(arrays.files)}
    repeat_root = Path(tempfile.mkdtemp(prefix="ecuread-h10-1-repeat-", dir="/tmp"))
    checks = []
    for fold in folds:
        sid = fold["held_out"]["session_id"]
        held = frames[sid].copy()
        expected_training_sha = saved[fold["fold_id"]]["training_feature_sha256"]
        frames[sid] = frames[sid] + 1e6
        require(array_sha(training_matrix(fold, frames).to_numpy()) == expected_training_sha, "held-out poison affected fitting data")
        artifact = fit_fold(fold, frames, protocol, repeat_root / fold["fold_id"])
        frames[sid] = held
        previous = saved[fold["fold_id"]]
        for name in ("isolation_forest.joblib", "robust_scaler.joblib"):
            require(artifact["artifact_sha256"][name] == previous["artifact_sha256"][name], "refit artifact not reproducible")
        bundle = load_model_bundle(artifact["artifact_dir"])
        transformed = bundle.scaler.transform(held)
        scores, decisions, flags = bundle.model.score_samples(transformed), bundle.model.decision_function(transformed), bundle.model.predict(transformed)
        old = [r for r in predictions if r["session_id"] == sid]
        require(np.array_equal(scores, [r["score_sample"] for r in old]) and np.array_equal(decisions, [r["decision_score"] for r in old])
                and np.array_equal(flags, [r["prediction"] for r in old]), "refit held-out predictions differ")
        checks.append({"fold_id": fold["fold_id"], "model_scaler_byte_identical": True, "held_out_poison_fit_invariant": True,
                       "held_out_scores_predictions_bit_identical": True, "repeat_artifact_dir": artifact["artifact_dir"]})
        print("refit invariance verified", fold["fold_id"], flush=True)
    write_json(STUDY / "refit_reproducibility.json", {"folds": checks, "protected_inputs": check_protected(protected),
        "note": "Model/scaler bytes and predictions reproducible. Metadata creation timestamp/training runtime intentionally vary; no claim of identical volatile metadata."})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preregister", "run", "verify-refits"))
    command = parser.parse_args().command
    {"preregister": preregister, "run": run, "verify-refits": verify_refits}[command]()
