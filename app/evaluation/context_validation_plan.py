"""H10.2A: blinded context review materials and design only; never fits or scores."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import random
from unittest.mock import patch
from xml.etree import ElementTree as ET

from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from app.context_annotation.speed import read_raw_capture, SPEED_DECODER_VERSION
from app.evaluation import group_aware_if as h101
from app.evaluation.context_benchmark_readiness import (
    CONTENT_SHA, EXPORT, ROOT, TRACKS, distribution, file_sha256,
    independent_assignment, read_json, read_jsonl, require, stable_hash,
    union_duration, verify_manifest, verify_mapping,
)
from app.ingestion.honda_0x17.adapter import import_native_jsonl_file


STUDY = ROOT / "data/evaluation/h10_2a"
REPORT = ROOT / "docs/research/H10_2A_CONTEXT_VALIDATION_PLAN.md"
SEED = 42
ACTIONS = ["Confirm", "Correct", "Reject", "Uncertain"]
PLOT_SIGNALS = [("vehicle_speed_kmh", "Speed (km/h)", "#067647"), ("rpm", "RPM", "#2563eb"),
                ("tps_voltage", "TPS (V)", "#b54708"), ("ect_c", "ECT (C)", "#be185d")]

# Counts are fixed before selecting regions. Selection never receives IF outcomes.
SELECTION = {
    1: [("full", None, None)],
    14: [("window", "thermal", "warming"), ("window", "thermal", "warming"),
         ("window", "operating", "stationary_running"), ("boundary", "thermal", None), ("boundary", "operating", None)],
    6: [("window", "operating", "stationary_running"), ("window", "operating", "moving_episode"),
        ("boundary", "operating", None), ("boundary", "thermal", None)],
    15: [("window", "operating", "stationary_running"), ("window", "operating", "moving_episode"),
         ("boundary", "operating", None), ("boundary", "operating", None)],
    23: [("window", "thermal", "warming"), ("window", "operating", "moving_steady"),
         ("window", "operating", "moving_episode"), ("window", "thermal", "stable"),
         ("boundary", "thermal", None), ("boundary", "operating", None)],
    2: [("window", "thermal", "warming"), ("window", "thermal", "stable"),
        ("boundary", "thermal", None), ("boundary", "thermal", None)],
    4: [("window", "operating", "stationary_running"), ("window", "operating", "moving_episode"),
        ("boundary", "operating", "stationary_running"), ("boundary", "operating", "stationary_running")],
    5: [("window", "thermal", "stable"), ("window", "operating", "moving_episode"),
        ("boundary", "thermal", None), ("boundary", "operating", None)],
    7: [("window", "operating", "moving_episode"), ("window", "operating", "moving_steady"),
        ("boundary", "operating", None), ("boundary", "operating", None)],
    18: [("window", "operating", "moving_steady"), ("boundary", "thermal", None),
         ("boundary", "operating", None), ("boundary", "operating", None)],
}


def write_json(path, value):
    h101.write_json(path, value)


@contextmanager
def prohibit_fitting():
    def forbidden(*args, **kwargs):
        raise AssertionError("H10.2A forbids fitting models, scalers or context thresholds")
    with patch.object(IsolationForest, "fit", forbidden), patch.object(RobustScaler, "fit", forbidden):
        yield


def protect():
    paths = h101.protected_inputs()
    for path in h101.STUDY.rglob("*"):
        if path.is_file():
            paths[str(path.relative_to(ROOT))] = file_sha256(path)
    paths[str(h101.REPORT.relative_to(ROOT))] = file_sha256(h101.REPORT)
    return {p: sha for p, sha in paths.items() if not (ROOT / p).is_relative_to(STUDY) and ROOT / p != REPORT}


def unchanged(expected):
    changed = [p for p, sha in expected.items() if not (ROOT / p).is_file() or file_sha256(ROOT / p) != sha]
    require(not changed, f"protected inputs changed: {changed}")
    return {"pinned_files_checked": len(expected), "changed_files": changed}


def verify_references(*, published=False):
    h101.load_registration()
    seal = read_json(h101.STUDY / "completion_validation.json")
    for path, sha in seal["output_sha256"].items():
        require(file_sha256(ROOT / path) == sha, f"immutable H10.1 output changed: {path}")
    if published:
        from app.evaluation.published_integrity import verify_published_inputs
        verify_published_inputs(read_json(h101.STUDY / "protected_inputs.json"))
    else:
        unchanged(read_json(h101.STUDY / "protected_inputs.json"))
    return verify_manifest()


def load_capture(source):
    sid, path = source["session_id"], ROOT / source["source_path"]
    canonical = import_native_jsonl_file(path, session_id=sid).canonical_session
    labels, windows = verify_mapping(source, canonical)
    raw = read_raw_capture(str(path), source["source_sha256"])
    require(len(raw.speeds) == len(canonical.samples) and all(v is not None for v in raw.speeds), "RAW speed missing or truncated")
    points = []
    for i, sample in enumerate(canonical.samples):
        require(sample.timestamp_ms == raw.timestamps_ms[i], "RAW/canonical timestamp mismatch")
        require(sample.frame_valid and sample.checksum_valid, "invalid source evidence")
        points.append({"canonical_index": i, "sequence": sample.sequence, "timestamp_ms": sample.timestamp_ms,
            "elapsed_s": (sample.timestamp_ms - canonical.samples[0].timestamp_ms) / 1000,
            "vehicle_speed_kmh": raw.speeds[i], "rpm": sample.rpm, "tps_voltage": sample.tps_voltage,
            "tps_raw": sample.tps_raw, "ect_c": sample.ect_c,
            "frame_valid": sample.frame_valid, "checksum_valid": sample.checksum_valid})
    return {"source": source, "labels": labels, "windows": windows, "points": points}


def boundaries(rows):
    return [{"time_s": a["end_s"], "left_interval_id": a["id"], "right_interval_id": b["id"],
             "left_label": a["label"], "right_label": b["label"]}
            for a, b in zip(rows, rows[1:])]


def seeded_random(key):
    return random.Random(int(hashlib.sha256(f"{SEED}:{key}".encode()).hexdigest(), 16))


def pending_review():
    return {"status": "pending", "action": None, "reviewer": None, "reviewed_at": None,
            "manual_review_time_s": None, "corrected_contexts": None, "boundary_corrections": None, "notes": None}


def make_region(capture, focus, selector):
    source, all_points, labels = capture["source"], capture["points"], capture["labels"]
    times = [p["elapsed_s"] for p in all_points]
    # Include neighboring evidence; retain every sample, including brief states.
    a = max(0, bisect_right(times, focus["start_s"] - 10) - 1)
    b = min(len(times) - 1, bisect_left(times, focus["end_s"] + 10))
    if focus["kind"] == "full":
        a, b = 0, len(times) - 1
    start, end = times[a], times[b]
    context = {t: [dict(r) for r in labels["tracks"][t] if r["start_s"] <= end and r["end_s"] > start] for t in TRACKS}
    edges = {t: [r for r in boundaries(labels["tracks"][t]) if start <= r["time_s"] <= end] for t in TRACKS}
    points = all_points[a:b + 1]
    rid = "review-" + stable_hash({"source": source["source_sha256"], "selector": selector, "focus": focus})[:16]
    return {"region_id": rid, "capture_id": source["session_id"], "physical_capture_group_id": source["physical_capture_group_id"],
        "source_path": source["source_path"], "source_sha256": source["source_sha256"], "focus": focus,
        "selection": selector, "start_timestamp_ms": points[0]["timestamp_ms"], "end_timestamp_ms": points[-1]["timestamp_ms"],
        "start_s": start, "end_s": end, "start_index": a, "end_index_inclusive": b,
        "timestamp_basis": "original elapsed_ms; absolute capture UTC unavailable, not inferred",
        "proposed_context": context, "boundary_positions": edges,
        "assignment_scope": "focal_ml_window" if focus["kind"] == "window" else "evidence_region",
        "focus_assignment": {t: independent_assignment(labels["tracks"][t], start if focus["kind"] == "boundary" else focus["start_s"],
            end if focus["kind"] == "boundary" else focus["end_s"], source["duration_s"]) for t in TRACKS},
        "event_markers": [dict(e) for e in labels["event_markers"] if e["start_s"] <= end and e["end_s"] > start],
        "measured_evidence": {k: distribution(p[k] for p in points) for k in ("vehicle_speed_kmh", "rpm", "tps_voltage", "tps_raw", "ect_c")},
        "samples": points, "sample_count": len(points), "review": pending_review()}


def select_regions(captures):
    result = []
    for number, specs in SELECTION.items():
        sid = f"session_{number:06}"
        capture, used = captures[sid], set()
        for slot, (kind, axis, label) in enumerate(specs):
            track = "macro_" + axis if axis else None
            selector = {"kind": kind, "track": track, "label": label, "slot": slot,
                        "reason": "Full short capture: inspect measured speed/context consistency" if kind == "full" else
                        f"Seeded random {kind} for {axis} context coverage" + (f": {label}" if label else ""),
                        "seed": SEED, "uses_detector_outcomes": False}
            rng = seeded_random(sid + ":" + str(slot))
            if kind == "full":
                focus = {"kind": kind, "start_s": 0.0, "end_s": capture["source"]["duration_s"]}
            elif kind == "window":
                candidates = [w for w in capture["windows"] if w["tracks"][track]["status"] == "homogeneous"
                              and w["tracks"][track]["assigned_label"] == label and ("window", w["window_index"]) not in used]
                require(bool(candidates), f"no distinct supported selection: {sid}/{slot}")
                w = rng.choice(candidates)
                focus = {"kind": kind, "start_s": w["start_s"], "end_s": w["end_s"], "window_index": w["window_index"],
                         "row_start": w["row_start"], "row_end": w["row_end"]}
                used.add(("window", w["window_index"]))
            else:
                candidates = [e for e in boundaries(capture["labels"]["tracks"][track])
                              if (label is None or label in (e["left_label"], e["right_label"]))
                              and (track, e["time_s"]) not in used]
                require(bool(candidates), f"no distinct boundary selection: {sid}/{slot}")
                edge = rng.choice(candidates)
                focus = {"kind": kind, "start_s": edge["time_s"], "end_s": edge["time_s"], "track": track, "boundary": edge}
                used.add((track, edge["time_s"]))
            result.append(make_region(capture, focus, selector))
    seeded_random("review-order").shuffle(result)
    for index, region in enumerate(result):
        region["review_order"] = index + 1
    require(len(result) == len({r["region_id"] for r in result}) == 40, "review-set identity mismatch")
    return result


def nonoverlapping(windows):
    selected, last = [], -1
    for w in sorted(windows, key=lambda x: x["row_start"]):
        if w["row_start"] > last:
            selected.append(w)
            last = w["row_end"]
    return selected


def calibration_split(sources):
    order = sorted(sources, key=lambda s: s["session_id"])
    require(len(order) == len({s["physical_capture_group_id"] for s in order}) == len({s["source_sha256"] for s in order}) == 18,
            "expected 18 distinct original capture groups and hashes")
    random.Random(SEED).shuffle(order)
    folds = []
    for i, held in enumerate(order):
        calibration = [order[(i + offset) % 18] for offset in range(1, 6)]
        fitting = [order[(i + offset) % 18] for offset in range(6, 18)]
        folds.append({"fold_id": "nested-" + held["session_id"], "fitting": fitting, "calibration": calibration, "test": [held]})
    return sorted(folds, key=lambda f: f["fold_id"])


def support_plan(folds, windows):
    rows = []
    for fold in folds:
        for arm, track, labels in (("B", "macro_thermal", ["warming", "stable"]),
                                   ("C", "macro_operating", ["stationary_running", "moving_episode", "moving_steady"])):
            for label in labels:
                counts = {}
                for source in fold["calibration"]:
                    sid = source["session_id"]
                    candidates = [w for w in windows[sid] if w["tracks"][track]["status"] == "homogeneous" and w["tracks"][track]["assigned_label"] == label]
                    disjoint = nonoverlapping(candidates)
                    counts[sid] = {"eligible_windows": len(candidates), "nonoverlapping_windows": len(disjoint),
                                   "selected_window_indices": [w["window_index"] for w in disjoint]}
                contributing = [s for s, c in counts.items() if c["nonoverlapping_windows"] >= 3]
                total = sum(counts[s]["nonoverlapping_windows"] for s in contributing)
                rows.append({"fold_id": fold["fold_id"], "arm": arm, "track": track, "context": label, "calibration_groups": counts,
                    "qualified_groups": len(contributing), "qualified_nonoverlapping_windows": total,
                    "eligible_to_learn_future_cutoff": len(contributing) >= 3 and total >= 30,
                    "test_outcomes_used": False, "threshold_learned": False})
    return rows


def protocol(sources, windows, h101_protocol):
    folds = calibration_split(sources)
    return {"schema_version": "h10.2a-calibration-design-v1", "study_id": "h10.2a-context-calibration-design-v1",
        "registered_at": datetime.now(UTC).isoformat(), "status": "design_only_awaiting_human_review",
        "study_type": "EXPLORATORY; prior selection and H10.1 outcomes known, not confirmatory",
        "export_content_sha256": CONTENT_SHA, "source_hashes": {s["session_id"]: s["source_sha256"] for s in sources},
        "grouping_policy": "operator_attested_capture_groups_v1", "machine_verified_independence": None,
        "architecture": {k: h101_protocol[k] for k in ("decoder", "telemetry_schema", "feature_schema", "feature_names", "signal_columns", "window_size", "window_step", "hyperparameters", "quality_policy", "software")},
        "preprocessing": "Unchanged production features; default RobustScaler and IF fitted only on fitting groups. Training captures unlabeled, not confirmed healthy. No balancing or model hyperparameter search.",
        "partition_policy": {"outer_folds": 18, "fitting_groups": 12, "calibration_groups": 5, "test_groups": 1, "seed": SEED,
            "assignment": "Sort original session IDs; random.Random(42).shuffle once. For outer test index i, next five cyclic groups calibrate; remaining twelve fit. Each group tests once, calibrates five times and fits twelve times. No support/outcome-driven reassignment.",
            "folds": folds, "aliases_and_derivatives": "Not inputs; any future alias/split/augmentation must inherit parent group and cannot cross partitions. Unresolved/synthetic data excluded.",
            "test_access": "Use test telemetry only for fixed feature extraction/inference after fit and calibration hashes sealed. No fitting, support optimization, cutoff choice or hyperparameter selection from outer-test outcomes. Prior H10 exposure remains disclosed."},
        "arms": {
            "A": {"type": "existing IF architecture control", "flag_rule": "raw_score < fitting_model.offset_"},
            "B": {"type": "same IF plus thermal calibration only", "track": "macro_thermal", "contexts": ["warming", "stable"]},
            "C": {"type": "same IF plus operating calibration only", "track": "macro_operating", "contexts": ["stationary_running", "moving_episode", "moving_steady"]}},
        "shared_model_invariance": "One scaler/IF fit per fold, shared byte-identically across A/B/C. Raw score and test-window identity must match across arms. Do not reuse H10.1 fitted models: they fitted 17 groups, including the new calibration groups.",
        "calibration": {"alpha": h101_protocol["hyperparameters"]["contamination"], "score_direction": "lower raw score is more unusual",
            "support": {"minimum_groups": 3, "minimum_nonoverlapping_windows_per_contributing_group": 3, "minimum_total_nonoverlapping_windows": 30},
            "window_selection": "Within each calibration group/context, sort by row_start and greedily retain a window only if row_start exceeds previous retained row_end. No score-dependent thinning. Groups with fewer than three retained windows do not contribute to that context.",
            "weighting": "Each qualified calibration capture has equal total weight 1/G; each of its n retained context windows has weight 1/(G*n). No claim that retained windows are temporally independent.",
            "cutoff": "q_context is the smallest observed raw calibration score with cumulative weight >= alpha=0.03 (weighted empirical inverse CDF, sorted score ascending, ties combined). Candidate flags raw_score < q_context. Retain raw scores and optionally report weighted ECDF as calibration-tail rank, not fault probability.",
            "fallback": "For unknown/abstained, boundary-ambiguous/unresolved, missing/unapproved context, or insufficient calibration support: calibration ABSTAINS, use A prediction/offset unchanged; retain the window and explicit fallback reason. Never majority vote. A/B/C all score the same eligible windows.",
            "extremity_guard": "On supported contexts, preserve every A-flagged window with raw_score < fitting-only training_score_p01, even if q_context would suppress it. This is a statistical-extremity sentinel from existing metadata, NOT a verified severity or fault cutoff. Never use test score percentiles.",
            "unknown_is_not_normal": True, "calibration_data_are_confirmed_healthy": False,
            "context_representation": "Macro axes separately; fine labels are parallel descriptive validation strata only. No joint B+C estimator or cross-axis fallback."},
        "support_preflight": support_plan(folds, windows),
        "context_review_policy": {"approved_base_sha256": CONTENT_SHA, "human_review_responses_sha256": None, "review_overlay_sha256": None,
            "required_before_execution": "Collector submits genuine decisions/time and separately approves an execution protocol revision pinning response and context-overlay hashes before any fitting. Targeted review is not whole-corpus validation. Corrections remain an audit overlay; approved-v2 unchanged. Reject/Uncertain/missing review support never becomes confirmed context or normal mechanical truth.",
            "unresolved_regions": "Windows touching rejected/uncertain review regions or unresolved 001 discrepancies must abstain from contextual calibration and fall back to A. No retrospective label changes based on IF flags."},
        "metrics_preregistered": [
            "Capture-level raw score and calibrated-tail-rank quantiles (p01,p05,p25,p50,p75,p95,p99); raw score invariant across arms; known/unknown/ambiguous/fallback strata separate.",
            "Per-capture flagged-window frequency and A-to-B/A-to-C paired differences; equal-capture primary, duration weighted secondary, pooled windows descriptive only.",
            "Consecutive flagged-window runs using existing H2 helper and original timestamps; runs/full capture-hour, flagged union-time, run duration distribution. No summing overlap durations.",
            "Event changes: bipartite matching by any common flagged window ID; count unchanged-window-set runs, changed one-to-one components, splits, merges, disappearing and newly appearing components. Retain exact window IDs; no tuned IoU threshold.",
            "Unsupported coverage by context/capture/fold: candidate-applied and every fallback-reason denominator in windows and union time; calibration groups and retained nonoverlapping counts; no unsupported zero-risk interpretation.",
            "Sensitivity: fixed predictions with each and each pair of captures removed from aggregation; explicitly 006/015/023/014. Also exclude <10-window captures and mask the six discrepant 001 windows. No threshold refit selected by these results.",
            "Post-primary fitting sensitivity: repeat each fixed fold omitting 006,015,023 or014 only when it was a fitting or calibration member; never move its role or replace it. Reseal A/B/C shared artifacts; recheck support and use fallback. All deletions specified, not chosen by test outcomes; no tuning from sensitivity.",
            "Capture bootstrap 10000 draws seed42 of fixed paired capture summaries, descriptive percentile95 interval; none with <3 supported groups. Overlapping windows and shared fold training remain dependent; no independent-window tests or diagnostic accuracy claims.",
            "CPU wall latency p50/p95 over 10 fixed-order inference repeats after warm-up, peak RSS, calibration runtime and artifact bytes; report hardware/versions, IF inference separate from lookup overhead. No deployment-limit change.",
            "Masking audit: enumerate every baseline-flagged window/event removed, raw score, fitting-only p01 extremity status and measured evidence refs. Require zero lost sentinel windows. Preserve raw evidence and any independently supplied high-severity mechanical evidence; absent verified physical severity truth, masking risk remains unresolved. No RCA/Contextual Battery change."],
        "success_and_stop_rules": {
            "engineering": "Abort on hash/group leakage, raw-score/window/model mismatch, lost baseline fallback, sentinel loss, invalid context mapping, or provenance mismatch.",
            "selection": "Reduced alert counts alone cannot pass a candidate. Report effect heterogeneity, support, overhead and masking without tuning. Any further research decision requires explicit human assessment; no automatic model/cutoff promotion.",
            "accuracy": "No fault sensitivity, true false-positive rate, normal-condition verification, or cross-motorcycle generalization claim without independent physical evidence.",
            "execution_in_this_phase": False, "automatic_progression_after_review": False},
        "future_artifact_policy": "All fitted IF/scaler/calibration artifacts disposable research-only; seal fit/calibration inventories, features, hashes, cutoffs/support/fallback metadata before outer-test inference. No production routes/models/threshold writes."}


def render_plot(region):
    svg = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 1200 940", "width": "1200", "height": "940"})
    def tag(name, attrs=None, text=None):
        node = ET.SubElement(svg, name, {k: str(v) for k, v in (attrs or {}).items()})
        if text is not None:
            node.text = str(text)
        return node
    def text(x, y, value, size=14, color="#1f2937"):
        tag("text", {"x": x, "y": y, "font-family": "sans-serif", "font-size": size, "fill": color}, value)
    tag("rect", {"width": 1200, "height": 940, "fill": "white"})
    text(20, 28, region["capture_id"] + " | " + region["region_id"], 20)
    text(20, 52, f"Source elapsed ms: {region['start_timestamp_ms']:.3f} - {region['end_timestamp_ms']:.3f}; all {region['sample_count']} samples retained")
    text(20, 75, "Dashed: focal boundary/window edges. Lower tracks: original interval boundaries; shaded unknown states.")
    start, end = region["start_s"], region["end_s"]
    x = lambda t: 150 + (t - start) / (end - start) * 1020
    for panel, (key, title, color) in enumerate(PLOT_SIGNALS):
        top, height = 112 + panel * 140, 100
        values = [p[key] for p in region["samples"]]
        lo, hi = min(values), max(values)
        pad = max((hi - lo) * .08, .1)
        lo, hi = lo - pad, hi + pad
        y = lambda v: top + height - (v - lo) / (hi - lo) * height
        text(14, top - 10, title)
        for tick in range(4):
            value = lo + (hi - lo) * tick / 3
            yy = y(value)
            tag("line", {"x1": 150, "x2": 1170, "y1": yy, "y2": yy, "stroke": "#e5e7eb"})
            text(80, yy + 4, f"{value:.2f}", 12)
        tag("polyline", {"data-signal": key, "fill": "none", "stroke": color, "stroke-width": 2,
            "points": " ".join(f"{x(p['elapsed_s']):.3f},{y(p[key]):.3f}" for p in region["samples"])})
        for edge in {region["focus"]["start_s"], region["focus"]["end_s"]}:
            tag("line", {"x1": x(edge), "x2": x(edge), "y1": top, "y2": top + height,
                         "stroke": "#111827", "stroke-dasharray": "5 4", "stroke-width": 1.5})
        if key == "vehicle_speed_kmh":
            for index in region.get("review_sample_indices", []):
                point = next(p for p in region["samples"] if p["canonical_index"] == index)
                tag("circle", {"cx": x(point["elapsed_s"]), "cy": y(point[key]), "r": 3, "fill": "#111827"})
    colors = {"unknown": "#e5e7eb", "stationary_running": "#d1fae5", "moving_episode": "#dbeafe", "moving_steady": "#cffafe",
              "warming": "#fce7f3", "stable": "#dcfce7", "engine_off": "#fde68a"}
    for i, track in enumerate(TRACKS):
        top = 675 + i * 48
        text(12, top + 18, track.replace("_", " "), 13)
        for interval in region["proposed_context"][track]:
            left, right = x(max(start, interval["start_s"])), x(min(end, interval["end_s"]))
            rect = tag("rect", {"x": left, "y": top, "width": right - left, "height": 30,
                        "fill": colors.get(interval["label"], "#e0e7ff"), "stroke": "#374151", "stroke-width": .5})
            ET.SubElement(rect, "title").text = f"{interval['id']} {interval['label']} [{interval['start_s']}, {interval['end_s']})"
            label = interval["label"].replace("moving_", "move ").replace("stationary_running", "stationary")
            if right - left > len(label) * 7 + 8:
                text(left + 4, top + 19, label, 12)
    for tick in range(7):
        t = start + (end - start) * tick / 6
        tag("text", {"x": x(t), "y": 895, "font-family": "sans-serif", "font-size": 12, "text-anchor": "middle", "fill": "#1f2937"}, f"{t:.3f}")
    text(490, 925, "Original elapsed seconds", 14)
    return ET.tostring(svg, encoding="unicode") + "\n"


def review_markdown(regions):
    lines = ["# Targeted Context Review Worksheet", "", "40 pending regions. Context consistency is not verified normal mechanical condition.",
        "", "Record Confirm / Correct / Reject / Uncertain, actual review seconds, reviewer, and date in `review_responses.template.json`.",
        "Corrections must identify track, interval ID and exact elapsed-second boundary positions. No detector scores or flags are shown.",
        "The capture selection was informed by prior research; window/boundary sampling is score-independent, not fully blind study enrollment.",
        "All timestamps are source elapsed time, not inferred wall-clock capture time. All original annotations remain unchanged.", ""]
    for r in regions:
        lines += [f"## {r['review_order']:02d}: {r['capture_id']} / {r['region_id']}", "",
            f"Source SHA-256: `{r['source_sha256']}`", "",
            f"Evidence: {r['start_timestamp_ms']:.3f}-{r['end_timestamp_ms']:.3f} ms; indices {r['start_index']}-{r['end_index_inclusive']} inclusive.",
            f"Focus: {r['focus']['start_s']:.4f}-{r['focus']['end_s']:.4f} s. Selection: {r['selection']['reason']}.", "",
            f"![Measured signals and original contexts]({r['plot']['path']})", "",
            "| Track | Label | Original Interval s | Interval ID |", "| --- | --- | --- | --- |"]
        for track, intervals in r["proposed_context"].items():
            for interval in intervals:
                lines.append(f"| {track} | {interval['label']} | {interval['start_s']:.4f}-{interval['end_s']:.4f} | `{interval['id']}` |")
        lines += ["", "Action: **pending**. Human review seconds: **not recorded**.", ""]
    return "\n".join(lines)


def validate_packet(packet):
    regions = packet["regions"]
    require(len(regions) == len({r["region_id"] for r in regions}) == 40, "packet must have 40 distinct regions")
    require(all(r["review"] == pending_review() for r in regions), "no automatic human review permitted")
    forbidden = {"score_sample", "decision_score", "is_anomaly", "prediction", "learned_offset", "flag_frequency"}
    def check(value):
        if isinstance(value, dict):
            require(not forbidden.intersection(value), "detector outcome leaked into reviewer evidence")
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    check(regions)
    for r in regions:
        require(len(r["samples"]) == r["sample_count"] == r["end_index_inclusive"] - r["start_index"] + 1, "region truncation")
        require([s["canonical_index"] for s in r["samples"]] == list(range(r["start_index"], r["end_index_inclusive"] + 1)), "sample identity/order mismatch")
        require(r["samples"][0]["timestamp_ms"] == r["start_timestamp_ms"] and r["samples"][-1]["timestamp_ms"] == r["end_timestamp_ms"], "inexact timestamps")
        require(all(a["timestamp_ms"] < b["timestamp_ms"] for a, b in zip(r["samples"], r["samples"][1:])), "nonmonotonic region time")
    unique = {i for r in regions if r["capture_id"] == "session_000001" for i in r.get("review_sample_indices", [])}
    require(unique == set(range(13, 30)) | {49, 50}, "missing 001 discrepancies")
    return {"regions": 40, "pending": 40, "selection_kinds": dict(Counter(r["focus"]["kind"] for r in regions)),
        "capture_counts": dict(sorted(Counter(r["capture_id"] for r in regions).items())), "unique_001_discrepancies": len(unique),
        "region_samples_not_decimated": True, "detector_outcomes_not_in_regions": True, "automatic_acceptance": False}


def validate_protocol(value):
    folds = value["partition_policy"]["folds"]
    require(len(folds) == 18, "expected 18 outer folds")
    roles = {role: Counter() for role in ("fitting", "calibration", "test")}
    for f in folds:
        groups = [{s["physical_capture_group_id"] for s in f[role]} for role in roles]
        require([len(g) for g in groups] == [12, 5, 1] and len(set.union(*groups)) == 18, "fitting/calibration/test capture leakage")
        hashes = [{s["source_sha256"] for s in f[role]} for role in roles]
        require(len(set.union(*hashes)) == 18, "source hash leakage")
        for role in roles:
            roles[role].update(s["session_id"] for s in f[role])
    require(all(set(roles[role].values()) == {count} for role, count in (("fitting", 12), ("calibration", 5), ("test", 1))), "unbalanced role inventory")
    require(value["success_and_stop_rules"]["execution_in_this_phase"] is False, "H10.2A cannot execute calibration")
    return {"outer_folds": 18, "disjoint_roles_verified": True, "balanced_role_counts": {role: dict(counts) for role, counts in roles.items()},
            "cutoffs_learned": 0, "models_fitted": 0}


def build():
    require(not STUDY.exists(), "H10.2A output already exists; refusing overwrite")
    protected = protect()
    with prohibit_fitting():
        manifest, audit = verify_references()
        selected = {f"session_{i:06}" for i in SELECTION}
        captures = {s["session_id"]: load_capture(s) for s in audit["sessions"] if s["session_id"] in selected}
        regions = select_regions(captures)
        discrepancy = read_json(ROOT / "data/evaluation/h10/h10_context_window_eligibility.json")["signal_label_discrepancies"]
        indices = sorted({i for d in discrepancy if d["session_id"] == "session_000001" for i in d["canonical_indices"]})
        for r in regions:
            r["review_sample_indices"] = [i for i in indices if r["capture_id"] == "session_000001" and r["start_index"] <= i <= r["end_index_inclusive"]]
            r["plot"] = {"path": "plots/" + r["region_id"] + ".svg", "sha256": hashlib.sha256(render_plot(r).encode()).hexdigest()}
        sources = [{k: s[k] for k in ("session_id", "source_path", "source_sha256", "physical_capture_group_id", "duration_s", "canonical_sample_count")} for s in audit["sessions"]]
        windows = {s["session_id"]: read_jsonl(EXPORT / "sessions" / s["session_id"] / "windows.jsonl") for s in sources}
        design = protocol(sources, windows, read_json(h101.STUDY / "protocol.json"))
        packet = {"schema_version": "h10.2a-targeted-context-review-v1", "export_content_sha256": CONTENT_SHA, "seed": SEED,
            "timestamp_basis": "original elapsed_ms, absolute UTC unavailable", "selection_plan": {str(k): v for k, v in SELECTION.items()},
            "blinding": "Window-level IF scores/flags/offsets omitted. Capture priorities came from prior results; full enrollment blinding is impossible. Within-stratum windows and boundaries sampled without detector outcomes.",
            "signal_provenance": {"native_decoder": "honda_keihin_71_17:1.0.0", "speed_decoder": SPEED_DECODER_VERSION,
                "speed": "original validated Table17 byte19, rider corroborated not GPS calibrated", "tps": "native decoded voltage and raw; no rescaling", "ect": "provisional high confidence, not independently calibrated"},
            "review_actions": ACTIONS, "mechanical_truth": "context consistency is not verified normal mechanical condition",
            "response_requirements": {"genuine_human_fields": ["action", "reviewer", "reviewed_at", "manual_review_time_s"],
                "correct": "Supply corrected context track/interval IDs and/or boundary_corrections with original position, proposed elapsed seconds and rationale. Each of the four tracks is independently reviewable; do not merge operating and thermal labels.",
                "reject_or_uncertain": "Record reason and affected tracks/interval IDs; do not replace with confirmed normal or silently drop the region.",
                "pending": "Null action, time, identity and date mean unreviewed, never zero seconds or a negative review decision."},
            "response_template": "review_responses.template.json", "regions": regions}
        packet["content_sha256"] = stable_hash(packet)
        packet_check, design_check = validate_packet(packet), validate_protocol(design)
        reproduced = select_regions(captures) == [{k: v for k, v in r.items() if k not in ("plot", "review_sample_indices")} for r in regions]
        require(reproduced, "selection did not reproduce from seed and source contexts")
        check = unchanged(protected)
        STUDY.mkdir(parents=True)
        (STUDY / "plots").mkdir()
        write_json(STUDY / "protected_inputs.json", protected)
        write_json(STUDY / "review_packet.json", packet)
        write_json(STUDY / "calibration_protocol.json", design)
        for region in regions:
            with (STUDY / region["plot"]["path"]).open("x") as handle:
                handle.write(render_plot(region))
        with (STUDY / "review_packet.md").open("x") as handle:
            handle.write(review_markdown(regions))
        write_json(STUDY / "review_responses.template.json", {"packet_content_sha256": packet["content_sha256"],
            "instruction": "Only collector/reviewer supplies decisions and actual review time. No automatic approval or annotation writeback.",
            "regions": [{"region_id": r["region_id"], "capture_id": r["capture_id"], "source_sha256": r["source_sha256"], **pending_review()} for r in regions]})
        files = {str(p.relative_to(STUDY)): file_sha256(p) for p in sorted(STUDY.rglob("*")) if p.is_file()}
        support = {arm: {label: sum(row["eligible_to_learn_future_cutoff"] for row in design["support_preflight"] if row["arm"] == arm and row["context"] == label)
                         for label in design["arms"][arm]["contexts"]} for arm in ("B", "C")}
        write_json(STUDY / "readiness.json", {"schema_version": "h10.2a-readiness-v1", "created_at": datetime.now(UTC).isoformat(),
            "verdict": "awaiting_targeted_human_review", "engineering": {"review_packet": "complete", "calibration_design": "preregistered_design_only",
                "packet_validation": packet_check, "design_validation": design_check, "source_and_historical_immutability": check,
                "preflight_supported_folds_of_18": support, "selection_reproduced": reproduced},
            "human": {"status": "pending", "completed_regions": 0, "pending_regions": 40, "boundary_accuracy": None, "manual_review_time_s": None,
                "mechanical_condition_verified": False, "review_response_sha256": None},
            "execution_allowed": False, "automatic_progression": False, "models_trained": 0, "thresholds_fitted": 0,
            "source_hashes": manifest["source_hashes"], "file_sha256": files})
    print(json.dumps(read_json(STUDY / "readiness.json")["engineering"], indent=2))


def verify():
    with prohibit_fitting():
        verify_references(published=True)
        readiness = read_json(STUDY / "readiness.json")
        for path, sha in readiness["file_sha256"].items():
            require(file_sha256(STUDY / path) == sha, f"packet/protocol file changed: {path}")
        packet = read_json(STUDY / "review_packet.json")
        require(stable_hash({k: v for k, v in packet.items() if k != "content_sha256"}) == packet["content_sha256"], "packet content hash mismatch")
        validation = validate_packet(packet)
        validate_protocol(read_json(STUDY / "calibration_protocol.json"))
        require(readiness["execution_allowed"] is False and readiness["human"]["completed_regions"] == 0, "manual gate misreported")
        from app.evaluation.published_integrity import verify_published_inputs
        verify_published_inputs(read_json(STUDY / "protected_inputs.json"))
        for r in packet["regions"]:
            require((STUDY / r["plot"]["path"]).read_text() == render_plot(r), "plot/evidence mismatch")
        print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    {"build": build, "verify": verify}[parser.parse_args().command]()
