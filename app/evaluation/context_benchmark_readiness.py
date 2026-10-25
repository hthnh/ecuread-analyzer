"""H10: read-only, hash-pinned descriptive research; never fits a model."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import tarfile

import numpy as np
import pandas as pd

from app.config import Settings
from app.context_annotation.auto_labeler import stable_hash
from app.context_annotation.storage import file_sha256
from app.domain.telemetry import V2_CORE_SIGNAL_COLUMNS, get_required_signal_columns
from app.ecu.profiles.honda_keihin_71_17 import normalize_frame, decode_production_values
from app.audit.loading import parse_raw_hex
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file
from app.ingestion.raw_ecu.adapter import import_raw_file
from app.ecu.parser import parse_log_file
from app.ml.contextual_detector import ContextualBatteryVoltageDetector, assign_operating_context
from app.ml.features import get_feature_names
from app.ml.harness import DetectorContext
from app.ml.inference import load_model_bundle, run_inference
from app.ml.profile_v02_training import load_profile_v02_jsonl_file
from app.services.analysis_service import build_feature_frame, canonical_session_to_frame_data
from app.evaluation.model_harness_evaluation import anomaly_events


ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "data/context_annotation/datasets/ecuread-filtered-raw-18-v1-approved-v2"
CONTENT_SHA = "5fd672ff45b69681e310efff7661cc8313989cfc10191047dc476e8161e156c1"
CAMPAIGN = ROOT / "data/context_annotation/campaigns/ecuread-filtered-raw-18-v1"
MODEL = ROOT / "data/models/honda_keihin_71_17_v2"
REFERENCE = ROOT / "data/evaluation/h3/contextual_reference.json"
SIGNALS = list(V2_CORE_SIGNAL_COLUMNS)
TRACKS = ("micro_operating", "micro_thermal", "macro_operating", "macro_thermal")


def read_json(path):
    return json.loads(Path(path).read_text())


def read_jsonl(path):
    with Path(path).open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def distribution(values):
    a = np.asarray(list(values), dtype=float)
    require(bool(np.isfinite(a).all()), "nonfinite descriptive statistics")
    if not len(a):
        return {"count": 0}
    return {"count": len(a), "min": float(a.min()), "p05": float(np.quantile(a, .05)),
            "p25": float(np.quantile(a, .25)), "median": float(np.median(a)),
            "p75": float(np.quantile(a, .75)), "p95": float(np.quantile(a, .95)),
            "max": float(a.max()), "mean": float(a.mean())}


def union_duration(intervals):
    end, total = -float("inf"), 0.0
    for a, b in sorted(intervals):
        require(b >= a, "reversed duration interval")
        total += max(0.0, b - max(a, end))
        end = max(end, b)
    return total


def protected_hashes():
    paths = set()
    for directory in ("app", "real_data", "docs", "data/models", "data/context_annotation", "data/calibration", "data/evaluation"):
        paths.update(p for p in (ROOT / directory).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and not p.is_relative_to(ROOT / "data/evaluation/h10")
                     and p != ROOT / "docs/research/H10_CONTEXT_BENCHMARK_FEASIBILITY.md")
    return {str(p.relative_to(ROOT)): file_sha256(p) for p in sorted(paths)}


def verify_manifest(export=EXPORT):
    m = read_json(export / "export_manifest.json")
    require(stable_hash({k: v for k, v in m.items() if k not in ("created_at", "content_sha256")}) == CONTENT_SHA == m["content_sha256"], "approved-v2 content SHA mismatch")
    require(m["schema_version"] == "context-approved-research-export-v1" and m["frozen"] is False, "unexpected export schema/freeze policy")
    for name, sha in m["files"].items():
        p = (export / name).resolve()
        require(p.is_relative_to(export.resolve()) and p.is_file() and file_sha256(p) == sha, f"export file missing/SHA mismatch: {name}")
    q = read_json(export / "quality_audit.json")
    require((q["session_count"], q["canonical_sample_count"], sum(s["mapping"]["windows"] for s in q["sessions"])) == (18, 100688, 9989), "export inventory mismatch")
    require(len(m["source_hashes"]) == len(set(m["source_hashes"].values())) == 18, "duplicate source identity")
    require({s["session_id"]: s["source_sha256"] for s in q["sessions"]} == m["source_hashes"], "source inventory differs")
    att = read_json(export / "operator_attestation.json")
    approval = read_json(export / "operator_approval.json")
    require({s["session_id"]: s["source_sha256"] for s in att["sources"]} == m["source_hashes"], "attestation scope mismatch")
    require(approval["approval_type"] == "bulk_operator_endorsement" and approval["individual_interval_review"] is False, "approval provenance mismatch")
    require(approval["input_source_hashes"] == m["source_hashes"], "approval source hashes differ")
    require(len({s["physical_capture_group_id"] for s in q["sessions"]}) == 18 and att["machine_verified_independent_captures"] is None, "capture policy mismatch")
    return m, q


def independent_assignment(rows, start, end, duration):
    """Check interval geometry independently of the export writer."""
    touching = [r for r in rows if r["start_s"] <= end and (r["end_s"] > start or r["end_s"] == start == duration)]
    cursor = start
    for r in touching:
        if r["start_s"] > cursor:
            break
        cursor = max(cursor, r["end_s"])
    covered = bool(touching) and touching[0]["start_s"] <= start and cursor >= end
    boundaries = sorted({t for r in touching for t in (r["start_s"], r["end_s"]) if start < t <= end and t != duration})
    status = "unresolved" if not covered else "ambiguous" if len(touching) != 1 or boundaries else "abstained" if touching[0]["label"] == "unknown" else "homogeneous"
    return {"status": status, "assigned_label": touching[0]["label"] if status in ("homogeneous", "abstained") else None,
            "interval_ids": [r["id"] for r in touching], "labels_present": sorted({r["label"] for r in touching}),
            "boundary_times_s": boundaries, "review_provenance": sorted({r["review_provenance"] for r in touching})}


def verify_mapping(s, canonical, export=EXPORT):
    sid, duration = s["session_id"], s["duration_s"]
    base = export / "sessions" / sid
    labels, samples, windows = read_json(base / "labels.json"), read_jsonl(base / "samples.jsonl"), read_jsonl(base / "windows.jsonl")
    actual = canonical.samples
    times = [(x.timestamp_ms - actual[0].timestamp_ms) / 1000 for x in actual]
    require(len(samples) == len(actual) == s["canonical_sample_count"] and times[-1] == duration, f"sample count/duration mismatch: {sid}")
    require(all(b > a for a, b in zip(times, times[1:])), f"time order mismatch: {sid}")
    tracks = labels["tracks"]
    expected_ids, event_ids = {name: [None] * len(samples) for name in TRACKS}, [[] for _ in samples]
    for name in TRACKS:
        rows = tracks[name]
        require(rows[0]["start_s"] == 0 and rows[-1]["end_s"] == duration, f"track coverage: {sid}/{name}")
        require(all(a["end_s"] == b["start_s"] for a, b in zip(rows, rows[1:])), f"track overlap/gap: {sid}/{name}")
        for r in rows:
            a, b = bisect_left(times, r["start_s"]), (bisect_right(times, r["end_s"]) if r["end_s"] == duration else bisect_left(times, r["end_s"]))
            require((a, b) == (r["start_index"], r["end_index"]), "interval index mapping differs")
            for i in range(a, b):
                require(expected_ids[name][i] is None, "conflicting sample assignments")
                expected_ids[name][i] = r["id"]
        require(all(x is not None for x in expected_ids[name]), "missing sample mapping")
    for e in labels["event_markers"]:
        a, b = bisect_left(times, e["start_s"]), (bisect_right(times, e["end_s"]) if e["end_s"] == duration else bisect_left(times, e["end_s"]))
        require((a, b) == (e["start_index"], e["end_index"]), "event index mapping differs")
        for i in range(a, b):
            event_ids[i].append(e["id"])
    for i, (row, raw) in enumerate(zip(samples, actual, strict=True)):
        require((row["canonical_index"], row["sequence"], row["timestamp_ms"], row["elapsed_s"]) == (i, raw.sequence, raw.timestamp_ms, times[i]), "canonical identity mismatch")
        require(row["interval_ids"] == {n: ids[i] for n, ids in expected_ids.items()} and row["event_ids"] == event_ids[i], "sample evidence mapping differs")
    starts = list(range(0, len(samples) - 49, 10))
    require(len(starts) == len(windows) == s["mapping"]["windows"], "window truncation")
    for wi, (a, w) in enumerate(zip(starts, windows, strict=True)):
        b = a + 49
        require((w["window_index"], w["row_start"], w["row_end"], w["sample_count"], w["start_s"], w["end_s"]) == (wi, a, b, 50, times[a], times[b]), "window identity mismatch")
        require(set(w["tracks"]) == set(TRACKS), "missing window track")
        for n in TRACKS:
            require(w["tracks"][n] == independent_assignment(tracks[n], times[a], times[b], duration), f"window assignment differs: {sid}/{wi}/{n}")
        require(w["event_ids"] == [e["id"] for e in labels["event_markers"] if e["start_s"] <= times[b] and e["end_s"] > times[a]], "window event mapping differs")
    require(labels["annotation"]["schema_version"] == "context-annotation-v2", "annotation schema mismatch")
    require(labels["detailed_proposals"]["taxonomy_version"] == s["taxonomy_version"] == "context-taxonomy-v2"
            and labels["detailed_proposals"]["rule_version"] == s["rule_version"] == "ctx2-rules-v1"
            and stable_hash(labels["detailed_proposals"]["config"]) == s["rule_config_sha256"], "label taxonomy/rule/config mismatch")
    children = {r["id"]: r for r in labels["detailed_proposals"]["segments"]}
    for axis in ("operating", "thermal"):
        referenced = []
        for episode in tracks["macro_" + axis]:
            referenced.extend(episode["child_ids"])
            require(all(child in children for child in episode["child_ids"]), "missing macro child proposal")
        require(set(referenced) == set(children), "macro-to-micro coverage incomplete")
        for id, child in children.items():
            spans = [(max(child["start_s"], e["start_s"]), min(child["end_s"], e["end_s"]))
                     for e in tracks["macro_" + axis] if id in e["child_ids"]]
            require(abs(union_duration(spans) - (child["end_s"] - child["start_s"])) < 1e-6, "macro child time coverage incomplete")
    return labels, windows


def eligibility(sessions):
    result = {"window_count": 9989, "missing_mapping_count": 0, "mapping_conflicts": 0, "majority_vote_used": False,
              "missing_raw_speed_samples": 0, "missing_model_signal_samples": 0, "signal_label_discrepancies": [],
              "representations": {}, "window_duration_s": distribution(w["end_s"] - w["start_s"] for s in sessions for w in s["windows"])}
    for rep in ("micro", "macro"):
        totals = Counter()
        by_group, by_label, segment_durations, switches = [], defaultdict(Counter), defaultdict(list), []
        label_spans = defaultdict(lambda: defaultdict(list))
        for s in sessions:
            counts, op_labels, thermal_labels, joint = Counter(), Counter(), Counter(), Counter()
            conflict_samples = set()
            config = s["labels"]["detailed_proposals"]["config"]
            for r in s["labels"]["tracks"][rep + "_operating"]:
                if r["label"] != "stationary_running":
                    continue
                bad = [i for i in range(r["start_index"], r["end_index"]) if s["raw_frames"][i][19] > config["stationary_speed_kmh"]]
                conflict_samples.update(bad)
                if bad:
                    result["signal_label_discrepancies"].append({"session_id": s["session_id"], "representation": rep, "interval_id": r["id"],
                        "kind": "stationary_label_with_raw_speed_above_configured_stationary_threshold", "samples": len(bad),
                        "canonical_indices": bad, "speed_kmh": [s["raw_frames"][i][19] for i in bad],
                        "review_provenance": r["review_provenance"], "action": "preserved, flagged for independent human review; no relabeling"})
            for w in s["windows"]:
                op, th = (w["tracks"][rep + "_" + axis] for axis in ("operating", "thermal"))
                for axis, assignment in (("operating", op), ("thermal", th)):
                    counts[axis + ":" + assignment["status"]] += 1
                    if assignment["status"] in ("homogeneous", "abstained"):
                        (op_labels if axis == "operating" else thermal_labels)[assignment["assigned_label"]] += 1
                counts["both_known"] += op["status"] == th["status"] == "homogeneous"
                counts["either_boundary_ambiguous"] += "ambiguous" in (op["status"], th["status"])
                counts["either_abstained"] += "abstained" in (op["status"], th["status"])
                counts["touches_unknown"] += any("unknown" in x["labels_present"] for x in (op, th))
                conflict = any(i in conflict_samples for i in range(w["row_start"], w["row_end"] + 1))
                counts["measured_evidence_discrepancy_windows"] += conflict
                counts["both_known_without_measured_discrepancy"] += op["status"] == th["status"] == "homogeneous" and not conflict
                for axis, assignment in (("operating", op), ("thermal", th)):
                    if assignment["status"] in ("homogeneous", "abstained"):
                        label_spans[axis + ":" + assignment["assigned_label"]][s["session_id"]].append((w["start_s"], w["end_s"]))
                if op["status"] == th["status"] == "homogeneous":
                    label = op["assigned_label"] + "|" + th["assigned_label"]
                    joint[label] += 1
                    label_spans["joint_known:" + label][s["session_id"]].append((w["start_s"], w["end_s"]))
            totals.update(counts)
            by_group.append({"session_id": s["session_id"], "capture_group": s["physical_capture_group_id"], "counts": dict(counts),
                             "operating": dict(op_labels), "thermal": dict(thermal_labels), "joint_known": dict(joint)})
            for axis, items in (("operating", op_labels), ("thermal", thermal_labels), ("joint_known", joint)):
                for label, count in items.items():
                    by_label[axis + ":" + label][s["session_id"]] += count
            switch = {"session_id": s["session_id"]}
            for axis in ("operating", "thermal"):
                rows = s["labels"]["tracks"][rep + "_" + axis]
                changes = sum(a["label"] != b["label"] for a, b in zip(rows, rows[1:]))
                switch[axis] = {"interval_boundaries": len(rows) - 1, "label_changes": changes, "label_changes_per_hour": changes * 3600 / s["duration_s"]}
                for r in rows:
                    segment_durations[axis + ":" + r["label"]].append(r["end_s"] - r["start_s"])
            switches.append(switch)
        result["representations"][rep] = {"counts": dict(totals), "by_capture_group": by_group,
            "by_label": {label: {"windows": sum(c.values()), "capture_groups": len(c), "windows_by_capture": dict(c),
                                  "largest_capture_share": max(c.values()) / sum(c.values()),
                                  "eligible_window_time_union_s_by_capture": {sid: union_duration(spans) for sid, spans in label_spans[label].items()}}
                         for label, c in by_label.items()},
            "segment_duration_s": {k: distribution(v) for k, v in segment_durations.items()}, "switching_by_capture": switches,
            "switching_distribution_per_hour": {axis: distribution(x[axis]["label_changes_per_hour"] for x in switches) for axis in ("operating", "thermal")}}
    result["evidence_discrepancy_sample_count_unique"] = sum(len(set(i for d in result["signal_label_discrepancies"] if d["session_id"] == s["session_id"] for i in d["canonical_indices"])) for s in sessions)
    result["evidence_conflicts"] = result["evidence_discrepancy_sample_count_unique"]
    result["interpretation"] = "micro thermal inherits joint interval boundaries, even when the thermal label stays identical; macro thermal is independently coalesced. Status counts overlap across axes; touches_unknown includes unknown inside ambiguous windows. Raw speed/label discrepancies are review candidates, not adjudicated ground truth; affected windows separately reported. No imputation."
    return result


def signature(sample):
    return tuple(sample.get(k) for k in SIGNALS) + tuple((sample.get("candidate_signals") or {}).get("unknown_reserved_raw") or [])


def scaler_correspondence(features, model_dir):
    bundle = load_model_bundle(model_dir)
    x = features[bundle.metadata["feature_names"]].to_numpy(dtype=float)
    center = np.median(x, axis=0)
    scale = np.quantile(x, .75, axis=0) - np.quantile(x, .25, axis=0)
    scale[scale < 10 * np.finfo(scale.dtype).eps] = 1.0
    return {"reconstructed_windows": len(x), "declared_windows": bundle.metadata["training_window_count"],
            "center_matches": bool(np.allclose(center, bundle.scaler.center_, rtol=0, atol=1e-10)),
            "scale_matches": bool(np.allclose(scale, bundle.scaler.scale_, rtol=0, atol=1e-10)),
            "max_center_error": float(np.max(np.abs(center - bundle.scaler.center_))),
            "max_scale_error": float(np.max(np.abs(scale - bundle.scaler.scale_))),
            "limitation": "Read-only quantile comparison, not fitting. Matching scaler statistics corroborate declared data; no immutable training source hash proves historical model fitting bytes."}


def leakage_audit(sessions, settings):
    native_meta = read_json(MODEL / "model_metadata.json")
    candidate_dir = ROOT / "data/models/honda_keihin_71_17_v2_candidate_human_normal"
    candidate_meta = read_json(candidate_dir / "model_metadata.json")
    reference = read_json(REFERENCE)
    retirement = read_json(CAMPAIGN / "provenance/legacy57_retirement.json")["recovery_backup"]
    archive_path = Path(retirement["path"])
    require(file_sha256(archive_path) == retirement["sha256"], "historical recovery archive SHA mismatch")
    archived = {}
    with tarfile.open(archive_path) as archive:
        for member in archive:
            if member.isfile() and member.name.startswith("data/telemetry/"):
                payload = archive.extractfile(member).read()
                require(hashlib.sha256(payload).hexdigest() == retirement["file_sha256"][member.name], "archived file SHA mismatch")
                archived[member.name] = payload
    lineage_path = CAMPAIGN / "exports/old_to_raw_lineage.json"
    require(file_sha256(lineage_path) == read_json(EXPORT / "capture_groups.json")["lineage_sha256"], "lineage SHA mismatch")
    lineage = read_json(lineage_path)
    lookup = {s["session_id"]: s for s in sessions}
    old_samples, links = {}, []
    for entry in lineage["sessions"]:
        sid = entry["old_session_id"]
        prefix = "data/telemetry/" + sid + "/"
        files = {k[len(prefix):]: v for k, v in archived.items() if k.startswith(prefix)}
        digest = hashlib.sha256()
        for name, data in sorted(files.items()):
            digest.update(name.encode() + b"\0" + data + b"\0")
        require(digest.hexdigest() == entry["old_source_sha256"], f"historical tree SHA mismatch: {sid}")
        rows = [json.loads(line) for line in files["samples.jsonl"].splitlines() if line.strip()]
        if sid in candidate_meta["training_session_ids"]:
            old_samples[sid] = rows
        for match in entry["matches"]:
            parent = lookup[match["new_session_id"]]
            start = match["sample_offset"]
            original = parent["sample_dicts"][start:start + len(rows)]
            require(len(rows) == match["sample_count"] and len(original) == len(rows), "lineage slice count mismatch")
            embedded = [(x.get("quality_flags") or {}).get("raw_hex") for x in rows]
            if any(embedded):
                decoded = [decode_production_values(normalize_frame(parse_raw_hex(value))[0]) for value in embedded]
                signatures = [tuple(x.get(k) for k in SIGNALS) + tuple(x["unknown_reserved_raw"]) for x in decoded]
            else:
                signatures = [signature(x) for x in rows]
            require(signatures == [signature(x) for x in original], f"lineage full signal sequence mismatch: {sid}")
            error = max(abs((a["timestamp_ms"] - rows[0]["timestamp_ms"]) - (b["timestamp_ms"] - original[0]["timestamp_ms"])) for a, b in zip(rows, original, strict=True))
            require(error <= 5, "lineage timestamp mismatch")
            if any(embedded):
                require(all(value and bytes(normalize_frame(parse_raw_hex(value))[0]) == expected for value, expected in zip(embedded, parent["raw_frames"][start:start + len(rows)], strict=True)), "embedded RAW lineage mismatch")
            links.append({"historical_id": sid, "parent_session_id": parent["session_id"], "sample_offset": start,
                          "sample_count": len(rows), "time_offset_s": match["time_offset_s"], "max_timestamp_error_ms": error,
                          "classification": entry["classification"], "full_sequence_verified": True, "embedded_raw_verified": bool(any(embedded)),
                          "historical_canonical_decoder_mismatch_samples": entry.get("decoder_contract_mismatch_samples", {})})
    native_matches, training_parts = [], []
    sequence_index = defaultdict(list)
    for s in sessions:
        for offset in range(len(s["raw_frames"]) - 15):
            sequence_index[b"".join(s["raw_frames"][offset:offset + 16])].append((s["session_id"], offset))

    def shared_sequences(frames):
        matches = defaultdict(list)
        for offset in range(len(frames) - 15):
            for parent, start in sequence_index.get(b"".join(frames[offset:offset + 16]), []):
                matches[parent].append((offset, start))
        return {parent: {"matching_16_frame_starts": len(points), "first_training_offset": points[0][0], "first_capture_offset": points[0][1]}
                for parent, points in matches.items()}

    for name in native_meta["training_sessions"]:
        path = ROOT / native_meta["training_source"] / name
        raw = read_jsonl(path)
        frames = [bytes(normalize_frame(parse_raw_hex(r["raw_hex"]))[0]) for r in raw]
        loaded = load_profile_v02_jsonl_file(path, strict_checksum=True, allow_legacy_29_byte=False)
        matches = []
        for s in sessions:
            if frames == s["raw_frames"]:
                require(np.allclose(loaded.frame_data["relative_time_ms"] - loaded.frame_data["relative_time_ms"].iloc[0],
                                    s["frame_data"]["relative_time_ms"] - s["frame_data"]["relative_time_ms"].iloc[0], rtol=0, atol=.001), "training time sequence mismatch")
                matches.append(s["session_id"])
        if len(matches) == 1 and loaded.frame_data["ml_eligible"].all():
            training_parts.append(lookup[matches[0]]["features"])
        else:
            training_parts.append(build_feature_frame(loaded.frame_data, settings, SIGNALS))
        native_matches.append({"training_source": str(path.relative_to(ROOT)), "sha256": file_sha256(path),
                               "frames": len(frames), "full_24_byte_sequence_matches": matches,
                               "shared_exact_16_frame_sequences": shared_sequences(frames),
                               "sequence_sha256": hashlib.sha256(b"".join(frames)).hexdigest()})
    candidate_parts = []
    for sid in candidate_meta["training_session_ids"]:
        rows = old_samples[sid]
        frame = pd.DataFrame([{**{k: x[k] for k in SIGNALS}, "frame_index": x["sequence"], "relative_time_ms": x["timestamp_ms"],
                               "checksum_valid": x["checksum_valid"], "is_valid": x["frame_valid"], "ml_eligible": x["checksum_valid"] and x["frame_valid"]} for x in rows])
        link = next(r for r in links if r["historical_id"] == sid)
        offset = link["sample_offset"]
        if offset % 10 == 0 and frame["ml_eligible"].all():
            parent_features = lookup[link["parent_session_id"]]["features"]
            part = parent_features[(parent_features["start_frame_index"] >= offset) & (parent_features["end_frame_index"] < offset + len(rows))]
            require(len(part) == (len(rows) - 50) // 10 + 1, "candidate training slice window identity differs")
            candidate_parts.append(part)
        else:
            candidate_parts.append(build_feature_frame(frame, settings, SIGNALS))
    candidate_features = pd.concat(candidate_parts, ignore_index=True)
    native_scaler = scaler_correspondence(pd.concat(training_parts, ignore_index=True), MODEL)
    candidate_scaler = scaler_correspondence(candidate_features, candidate_dir)
    require(native_scaler["center_matches"] and native_scaler["scale_matches"] and native_scaler["reconstructed_windows"] == 3874, "native training reconstruction differs")
    require(candidate_scaler["center_matches"] and candidate_scaler["scale_matches"] and candidate_scaler["reconstructed_windows"] == 4155, "candidate training reconstruction differs")
    require(candidate_meta["training_session_ids"] == reference["reference_session_ids"], "reference/candidate training identity differs")
    observed = Counter(candidate_features.apply(assign_operating_context, axis=1))
    require(dict(observed) == reference["observed_context_counts"], "reference reconstruction context counts differ")
    reference_checks = []
    for context, stats in reference["contexts"].items():
        rows = candidate_features.loc[candidate_features.apply(assign_operating_context, axis=1) == context]
        for feature, saved in stats["features"].items():
            x = rows[feature].astype(float)
            median, mad, iqr = x.median(), (x - x.median()).abs().median(), x.quantile(.75) - x.quantile(.25)
            scale = max(mad * 1.4826, iqr / 1.349, reference["configuration"]["battery_voltage_scale_floor"])
            require(abs(median - saved["median"]) < 1e-5 and abs(scale - saved["robust_scale"]) < 1e-5, "reference statistic reconstruction differs")
            reference_checks.append({"context": context, "feature": feature, "median_matches": True, "scale_matches": True})
    historical_usage = []
    old_ids = {r["old_session_id"] for r in lineage["sessions"]}

    def ids_in(value):
        if isinstance(value, dict):
            return set().union(*(ids_in(v) for v in value.values())) if value else set()
        if isinstance(value, list):
            return set().union(*(ids_in(v) for v in value)) if value else set()
        return {value} & old_ids if isinstance(value, str) else set()

    for phase in sorted((ROOT / "data/evaluation").glob("h[2-9]*")):
        if not phase.is_dir():
            continue
        used, files = set(), []
        for path in sorted(phase.rglob("*")):
            if path.suffix not in (".json", ".jsonl"):
                continue
            if path.suffix == ".jsonl":
                ids = set()
                with path.open() as handle:
                    for line in handle:
                        if line.strip():
                            ids |= ids_in(json.loads(line))
            else:
                ids = ids_in(read_json(path))
            if ids:
                used |= ids
                files.append({"path": str(path.relative_to(ROOT)), "sha256": file_sha256(path), "historical_ids": sorted(ids)})
        historical_usage.append({"phase": phase.name, "input_or_catalog_ids": sorted(used), "artifacts": files,
                                 "meaning": "Prior exposure/catalog/review, not proof of fitting or model selection. Exclusion lists are also catalog exposure."})
    with (ROOT / "data/calibration/human_label_model_comparison.csv").open() as handle:
        comparison = {r["session_id"]: r for r in csv.DictReader(handle)}
    legacy_path = ROOT / "tests/fixtures/serial_log_20251105_204330.txt"
    legacy = import_raw_file(legacy_path, settings).canonical_session
    legacy_signals = get_required_signal_columns(legacy.telemetry_schema_version)
    legacy_features = build_feature_frame(canonical_session_to_frame_data(legacy, legacy_signals), settings, legacy_signals)
    legacy_frames = [bytes(normalize_frame(f.bytes)[0]) for f in parse_log_file(legacy_path).frames]
    legacy_reconstruction = {"fixture": str(legacy_path.relative_to(ROOT)), "sha256": file_sha256(legacy_path),
                             "scaler": scaler_correspondence(legacy_features, ROOT / "data/models"),
                             "shared_exact_16_frame_sequences": shared_sequences(legacy_frames),
                             "limitation": "Fixture matches declared name, 75 windows and scaler statistics; historical fit source hash/physical identity still unresolved."}
    history_hash_checks = []
    for phase in ("h2", "h3"):
        path = ROOT / "data/evaluation" / phase / "evaluation_manifest.json"
        for record in read_json(path)["sessions"]:
            sid = record["session_id"]
            key = "data/telemetry/" + sid + "/samples.jsonl"
            if key in archived and record.get("artifacts", {}).get("samples_sha256"):
                saved = record["artifacts"]["samples_sha256"]
                history_hash_checks.append({"phase": phase, "historical_id": sid, "recorded_samples_sha256": saved,
                                            "archived_samples_sha256": hashlib.sha256(archived[key]).hexdigest(),
                                            "matched": hashlib.sha256(archived[key]).hexdigest() == saved})
    require(file_sha256(ROOT / reference["provenance"]["calibration_signal_decisions"]) == reference["provenance"]["calibration_signal_decisions_sha256"], "reference decoder calibration provenance SHA mismatch")
    matrix = []
    train = set(candidate_meta["training_session_ids"])
    holdout = set(candidate_meta["normal_holdout_session_ids"])
    for s in sessions:
        sid = s["session_id"]
        linked = [r for r in links if r["parent_session_id"] == sid]
        fitting = [r for r in linked if r["historical_id"] in train]
        held = [r for r in linked if r["historical_id"] in holdout]
        baseline_seen = [r["training_source"] for r in native_matches if sid in r["full_24_byte_sequence_matches"]]
        selection = [r["historical_id"] for r in linked if r["historical_id"] in comparison]
        matrix.append({"session_id": sid, "source_sha256": s["source_sha256"], "capture_group": s["physical_capture_group_id"],
                       "native_if_fit": "documented_sequence_overlap" if baseline_seen else "no_overlap_in_reconstructed_declared_inputs",
                       "native_if_scaler_fit": bool(baseline_seen), "native_if_training_sources": baseline_seen,
                       "native_if_learned_offset_exposure": bool(baseline_seen),
                       "older_v02_if_declared_source_overlap": bool(baseline_seen), "older_v02_fitting_bytes": "unresolved; incompatible feature contract, declared sources share verified frame sequences",
                       "legacy_root_if_exposure": "no observed 16-frame overlap; historical fitting identity unresolved",
                       "candidate_if_fit": bool(fitting), "candidate_scaler_fit": bool(fitting), "candidate_fit_slices": fitting,
                       "candidate_learned_offset_exposure": bool(fitting), "context_reference_calibration": bool(fitting),
                       "historical_candidate_holdout_slices": held, "candidate_group_train_holdout_leakage": bool(fitting and held),
                       "previous_model_selection_exposure": bool(selection), "model_selection_ids": selection,
                       "threshold_policy_analysis_exposure": bool(selection),
                       "threshold_selection_detail": "Historical grid and recommendation consumed human_label_model_comparison.csv; current production policy was retained, not retuned by H10.",
                       "h2_h9_catalog_or_evaluation_exposure": [h["phase"] for h in historical_usage if any(r["historical_id"] in h["input_or_catalog_ids"] for r in linked)],
                       "unseen_test_set": False, "historical_links": linked,
                       "unresolved": ["No immutable original IF training input hash was saved; reconstructed data and matching scaler are corroboration, not independent historical byte authentication.",
                                      "Legacy root training artifact identity/physical lineage unresolved; not used for native inference."]})
    artifact_inventory = []
    for path in sorted((ROOT / "data/models").rglob("model_metadata.json")):
        m = read_json(path)
        artifact_inventory.append({"model_dir": str(path.parent.relative_to(ROOT)), "version": m.get("model_version_id", m.get("version")),
                                   "training_sessions": m.get("training_sessions"), "training_window_count": m.get("training_window_count"),
                                   "signal_columns": m.get("signal_columns"), "telemetry_schema_version": m.get("telemetry_schema_version"),
                                   "native_contract_compatible": m.get("signal_columns") == SIGNALS and m.get("telemetry_schema_version") == "canonical-telemetry-v2",
                                   "sha256": {p.name: file_sha256(p) for p in sorted(path.parent.iterdir()) if p.is_file()},
                                   "exposure_note": "v0.2 declares the same nine RAW sources verified in native_training_sequence_audit, but incompatible historical feature contract; historical fitting bytes unresolved. Root legacy fixture reconstructs scaler exactly, without immutable historical training hashes." if path.parent not in (MODEL, candidate_dir) else "Reconstructed declared inputs and scaler statistics checked."})
    return {"archive": {k: v for k, v in retirement.items() if k != "file_sha256"}, "historical_entries_verified": len(old_ids),
            "historical_links_verified": len(links), "native_training_sequence_audit": native_matches,
            "legacy_root_reconstruction": legacy_reconstruction, "historical_input_sample_hash_checks": history_hash_checks,
            "native_scaler_reconstruction": native_scaler, "candidate_scaler_reconstruction": candidate_scaler,
            "reference_statistic_reconstruction": reference_checks, "historical_usage": historical_usage,
            "feature_reuse_policy": "Reuse existing production features only for fully validated identical 50-sample windows on aligned source offsets, all rows ML-eligible. No feature reimplementation or model fitting.",
            "artifact_inventory": artifact_inventory, "per_capture_matrix": matrix,
            "summary": {"native_fit_overlap_captures": sum(bool(s["native_if_training_sources"]) for s in matrix),
                        "candidate_fit_and_reference_captures": sum(s["candidate_if_fit"] for s in matrix),
                        "candidate_train_holdout_correlated_capture_groups": sum(s["candidate_group_train_holdout_leakage"] for s in matrix),
                        "previous_selection_exposed_captures": sum(s["previous_model_selection_exposure"] for s in matrix)},
            "historical_selection_evidence": {"implementation": "app/audit/human_normal_retraining.py:build_threshold_policy_rows, production_recommendation", "comparison_csv_sha256": file_sha256(ROOT / "data/calibration/human_label_model_comparison.csv")}}


def score_statistics(rows):
    flags = sum(r["is_anomaly"] for r in rows)
    return {"windows": len(rows), "flagged_windows": flags, "flag_frequency": flags / len(rows) if rows else None,
            "score_sample": distribution(r["score_sample"] for r in rows), "decision_score": distribution(r["decision_score"] for r in rows),
            "capture_groups": len({r["session_id"] for r in rows})}


def baseline_audit(sessions):
    rows, capture_results = [], []
    reference = read_json(REFERENCE)
    detector = ContextualBatteryVoltageDetector(reference)
    for s in sessions:
        features = s["features"]
        inferred = run_inference(features, MODEL, expected_telemetry_schema_version="canonical-telemetry-v2", expected_signal_columns=SIGNALS)
        require(inferred.model_loaded and len(inferred.windows) == len(s["windows"]), "native inference unavailable/incomplete")
        battery = detector.infer(features, DetectorContext(telemetry_schema_version="canonical-telemetry-v2", signal_columns=tuple(SIGNALS)))
        require(battery.status in ("ok", "skipped") and len(battery.windows) == len(features), "contextual inference incomplete")
        events = anomaly_events(inferred.windows, s["frame_data"])
        local = []
        for w, scored, bat in zip(s["windows"], inferred.windows.to_dict("records"), battery.windows.to_dict("records"), strict=True):
            local.append({"session_id": s["session_id"], "window_index": w["window_index"], "row_start": w["row_start"], "row_end": w["row_end"],
                          "start_s": w["start_s"], "end_s": w["end_s"], "score_sample": float(scored["score_sample"]),
                          "decision_score": float(scored["decision_score"]), "is_anomaly": bool(scored["is_anomaly"]),
                          "contexts": {n: {"status": a["status"], "label": a["assigned_label"]} for n, a in w["tracks"].items()},
                          "contextual_applicable": bool(bat["contextual_applicable"]), "battery_context": bat["operating_context"],
                          "contextual_status": bat["contextual_status"]})
        rows.extend(local)
        flag_time = union_duration((r["start_s"], r["end_s"]) for r in local if r["is_anomaly"])
        # Use exported labels rather than guessing that all engine-on time is riding.
        moving = [r for r in s["labels"]["tracks"]["macro_operating"] if r["label"] in ("moving_episode", "moving_steady")]
        riding_s = sum(r["end_s"] - r["start_s"] for r in moving)
        riding_flag_s = union_duration((max(r["start_s"], e["start_s"]), min(r["end_s"], e["end_s"]))
                                      for r in local if r["is_anomaly"] for e in moving if max(r["start_s"], e["start_s"]) < min(r["end_s"], e["end_s"]))
        riding_events = sum(any(local[e["start_window_index"]]["start_s"] < r["end_s"] and local[e["end_window_index"]]["end_s"] > r["start_s"] for r in moving) for e in events)
        capture_results.append({"session_id": s["session_id"], "capture_group": s["physical_capture_group_id"], "duration_s": s["duration_s"],
                                "long_operator_attested": s["session_id"] in ("session_000016", "session_000018", "session_000023"),
                                **score_statistics(local), "flag_events": events, "flag_event_count": len(events),
                                "flagged_windows_per_capture_hour": sum(r["is_anomaly"] for r in local) * 3600 / s["duration_s"],
                                "flag_events_per_capture_hour": len(events) * 3600 / s["duration_s"], "flagged_time_union_s": flag_time,
                                "flagged_time_fraction": flag_time / s["duration_s"], "labeled_moving_duration_s": riding_s,
                                "moving_intersecting_events": riding_events, "moving_events_per_hour": riding_events * 3600 / riding_s if riding_s else None,
                                "moving_flag_time_union_s": riding_flag_s, "moving_flag_time_fraction": riding_flag_s / riding_s if riding_s else None,
                                "contextual_battery": {"status": battery.status, "reason": battery.reason, "scored_windows": battery.scored_window_count,
                                                       "skipped_windows": len(features) - battery.scored_window_count,
                                                       "status_counts": dict(Counter(r["contextual_status"] for r in local)),
                                                       "contexts": dict(Counter(r["battery_context"] for r in local)),
                                                       "skip_reasons": dict(Counter(b["contextual_evidence"].get("reason") for b in battery.windows.to_dict("records") if not b["contextual_applicable"]))}})
    by_context = {}
    for name in TRACKS:
        groups = defaultdict(list)
        for row in rows:
            assignment = row["contexts"][name]
            key = assignment["label"] if assignment["status"] in ("homogeneous", "abstained") else "__" + assignment["status"]
            groups[key].append(row)
        by_context[name] = {k: {**score_statistics(v), "by_capture": {sid: score_statistics([r for r in v if r["session_id"] == sid]) for sid in sorted({r["session_id"] for r in v})}} for k, v in groups.items()}
    long_ids = {s["session_id"] for s in capture_results if s["long_operator_attested"]}
    total_duration = sum(s["duration_s"] for s in sessions)
    return {"interpretation": "DESCRIPTIVE ONLY; scores/flags are not fault ground truth or confirmed false alarms; overlapping windows are dependent.",
            "model": {"path": str(MODEL.relative_to(ROOT)), "sha256": {p.name: file_sha256(p) for p in MODEL.iterdir() if p.is_file()},
                      "feature_schema": "ecu-window-features-v1", "signals": SIGNALS, "feature_count": len(get_feature_names(SIGNALS)),
                      "offset": float(load_model_bundle(MODEL).model.offset_), "threshold_policy": "Existing predict == -1 / decision_function < 0; unchanged."},
            "reference_sha256": file_sha256(REFERENCE), "all_windows": score_statistics(rows), "by_context": by_context,
            "per_capture": capture_results, "window_results": rows,
            "total_duration_s": total_duration,
            "total_flag_events": sum(s["flag_event_count"] for s in capture_results),
            "flag_events_per_capture_hour": sum(s["flag_event_count"] for s in capture_results) * 3600 / total_duration,
            "flagged_time_union_s": sum(s["flagged_time_union_s"] for s in capture_results),
            "between_capture_flag_frequency": distribution(s["flag_frequency"] for s in capture_results),
            "between_capture_event_rate_per_hour": distribution(s["flag_events_per_capture_hour"] for s in capture_results),
            "length_comparison": {label: {"capture_count": len(ids), **score_statistics([r for r in rows if r["session_id"] in ids]),
                                           "capture_equal_weight_flag_frequency": distribution(s["flag_frequency"] for s in capture_results if s["session_id"] in ids)}
                                  for label, ids in (("three_operator_attested_long_rides", long_ids), ("other_15_not_necessarily_short", {s["session_id"] for s in sessions} - long_ids))},
            "burden_definition": "Events are existing H2 anomaly_events consecutive flagged-window runs, not physical faults or production notifications. Time burden is union of flagged window spans, never sum of overlapping window durations. Capture-time and labeled-moving-time denominators are separate; unknown time is not assumed riding."}


def benchmark_design(eligible, leakage):
    coverage = eligible["representations"]["macro"]["by_label"]
    return {"verdict": "descriptive_analysis_only", "human_boundary_validation": "pending", "verified_physical_fault_labels": 0,
            "independence_policy": "operator_attested_capture_groups_v1; 18 collector-attested groups, machine verification null; strict machine-verified policy unchanged",
            "approaches": {
                "A": {"method": "Native-v2 IF", "current_artifact": "descriptive only, contaminated and historically selected", "future": "Refit SAME algorithm/feature contract on the identical training groups used for every comparator; temporary artifacts only", "complexity": "200 trees, 81 features; linear inference in windows times tree traversal", "interpretability": "feature deviations, not causal diagnosis"},
                "B": {"method": "Same IF, context-stratified analysis; optional train-only calibration", "feasibility": "smallest next experiment: stratification first, no new estimator", "calibration": "Any later calibration on train/calibration groups exclusively, separate from outer test", "complexity": "same IF plus context lookup", "interpretability": "conditional score/flag burden, not fault accuracy"},
                "C": {"method": "Context-conditioned IF", "feasibility": "conditional on sufficient training AND outer-test groups in each fold; aggregate macro motion initially, abstain otherwise", "coverage": coverage, "complexity": "one IF per supported context; sparse cells increase variance", "interpretability": "context-specific deviations; same signals define context and score"},
                "D": {"method": "PCA reconstruction residual", "feasibility": "defer; no audited residual advantage or independent fault labels justifies another fitted family yet", "future": "If justified, fit scaler/PCA/components/thresholds using training/calibration groups only and identical outer folds", "complexity": "linear projection; correlated 81-feature window statistics may suit a simple sensitivity comparator", "interpretability": "reconstruction contributions, not fault causes"}},
            "smallest_next_experiment": {
                "execution_now": "none; no model fitting or threshold calibration performed in H10",
                "unit": "whole operator-attested physical capture group; aliases, historical splits, replay and derivatives inherit parent; unresolved/synthetic excluded",
                "split": "Pre-register deterministic leave-one-capture-group-out outer folds (18); choose calibration groups only within the other 17, identically for A/B and any later candidates. Publish group/hash manifests before fitting.",
                "training_scope": "Without independently verified normal-only data, fit representative training captures as unlabeled data; do not rename context labels normal.",
                "labels": "Obtain independent boundary review, prioritizing 002/004/005/007/018; blind reviewer to IF flags. Macro and fine stay separate.",
                "eligibility": "Homogeneous known axis labels only for conditional metrics; crossing/unknown windows retained in denominators and separate strata, never majority-voted.",
                "sparse_context_policy": "Proposed protocol gate, not a production threshold: require >=3 training capture groups and >=1 eligible outer-test group per context; otherwise report unsupported and use pooled baseline without claiming conditioned comparison. Inspect per-group time, not just overlapping window totals.",
                "metrics": "Paired per-capture score distributions and union-time/event burden; equal-weight capture summaries and capture-level bootstrap uncertainty. No window-random split or independent-window significance tests. Treat folds as dependent training experiments.",
                "claims": "Context-conditioned score behavior/alert burden feasibility only; no precision, recall, diagnostic accuracy or false-positive rate without verified normal/fault labels.",
                "selection": "Pre-register contrasts, no tuning on outer folds. Existing corpus has prior selection exposure; reserve genuinely new captures for final confirmatory claims."},
            "limitations": ["18 groups do not establish population-level motorcycle generalization; physical independence rests on operator declaration.",
                            "50-row windows step 10 share 40 samples (80% overlap); nominal 9989 is not independent N.",
                            "Bulk operator endorsement is not individually verified boundaries or fault ground truth; manual time/boundary checks remain pending.",
                            "CTX2 labels derive speed/RPM/TPS/ECT; IF shares RPM/TPS/ECT/IAT/battery. Stratified differences and context recognition can be circular, not independent detector validation.",
                            "Context recognition, anomaly scores, normal-looking riding burden and real-fault sensitivity are distinct tasks.",
                            "IF/RCA predictions never become ground truth. Synthetic perturbations are sensitivity tests only, not real-world recall.",
                            "Historical source SHA absence remains disclosed even when complete frame sequences and scaler statistics match."],
            "leakage_summary": leakage["summary"]}


def run(output):
    output = output.resolve()
    require(not output.exists(), "H10 output must be a new directory; refusing overwrite")
    require(not any(output.is_relative_to(p) for p in (EXPORT, CAMPAIGN, ROOT / "data/models", ROOT / "real_data", ROOT / "app")), "output overlaps protected inputs")
    before = protected_hashes()
    m, q = verify_manifest()
    settings = Settings(data_dir=ROOT / "data", model_dir=ROOT / "data/models")
    meta = read_json(MODEL / "model_metadata.json")
    require(meta["signal_columns"] == SIGNALS and meta["feature_names"] == get_feature_names(SIGNALS) and meta["feature_schema_version"] == "ecu-window-features-v1", "native feature contract mismatch")
    sessions = []
    for source in q["sessions"]:
        sid = source["session_id"]
        path = ROOT / source["source_path"]
        require(file_sha256(path) == source["source_sha256"], f"RAW source SHA mismatch: {sid}")
        imported = import_native_jsonl_file(path, session_id=sid)
        canonical = imported.canonical_session
        require(canonical.decoder_version == "1.0.0" and canonical.telemetry_schema_version == "canonical-telemetry-v2", "decoder mismatch")
        require(all(x.frame_valid and x.checksum_valid and all(getattr(x, k) is not None for k in SIGNALS) for x in canonical.samples), "invalid/missing source evidence")
        frame = canonical_session_to_frame_data(canonical, SIGNALS)
        features = build_feature_frame(frame, settings, SIGNALS)
        labels, windows = verify_mapping(source, canonical)
        require(len(features) == len(windows) and features["start_frame_index"].tolist() == [w["row_start"] for w in windows]
                and features["end_frame_index"].tolist() == [w["row_end"] for w in windows], "production window identity mismatch")
        sessions.append({**source, "frame_data": frame, "features": features, "labels": labels, "windows": windows,
                         "sample_dicts": [{**{k: getattr(x, k) for k in SIGNALS}, "timestamp_ms": x.timestamp_ms,
                                           "candidate_signals": {"unknown_reserved_raw": x.candidate_signals.get("unknown_reserved_raw")}} for x in canonical.samples],
                         "raw_frames": [bytes.fromhex(r["raw_hex"]) for r in read_jsonl(path)]})
        print(f"verified {sid}: {len(canonical.samples)} samples / {len(features)} windows", flush=True)
    e = eligibility(sessions)
    leakage = leakage_audit(sessions, settings)
    print("lineage, declared training inputs, scalers and reference checked", flush=True)
    baseline = baseline_audit(sessions)
    design = benchmark_design(e, leakage)
    require(protected_hashes() == before, "protected research/model/source files changed during audit")
    verification = {"export_content_sha256": CONTENT_SHA, "referenced_export_files_verified": len(m["files"]),
                    "source_count": 18, "canonical_samples": 100688, "windows": 9989, "all_mappings_independently_checked": True,
                    "schema_decoder_feature_contract_checked": True, "protected_files_unchanged": len(before),
                    "snapshot_gate": m["snapshot_attempt"], "operator_approval": "bulk endorsement, not boundary/fault ground truth",
                    "protected_input_sha256": before}
    output.mkdir(parents=True)
    for name, payload in (("source_leakage_audit", leakage), ("context_window_eligibility", e), ("baseline_context_audit", baseline), ("benchmark_design", design)):
        payload = {"schema_version": "h10-" + name.replace("_", "-") + "-v1", "export_content_sha256": CONTENT_SHA, **payload}
        if name == "source_leakage_audit":
            payload["input_verification"] = verification
        (output / ("h10_" + name + ".json")).write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"verdict": design["verdict"], "leakage": leakage["summary"], "baseline": baseline["all_windows"],
                      "eligibility": {rep: e["representations"][rep]["counts"] for rep in ("micro", "macro")}}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/h10")
    run(parser.parse_args().output)
