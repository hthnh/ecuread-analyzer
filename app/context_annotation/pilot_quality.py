"""Read-only CTX2 proposal audit and conservative capture-lineage reconciliation."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from app.audit.loading import parse_raw_hex
from app.context_annotation.auto_labeler import _array, _stats, suggest_context
from app.context_annotation.corpus import manifest_sources, repo_root
from app.context_annotation.models import SuggestionDocument
from app.context_annotation.session_loader import discover_sources, load_source
from app.context_annotation.speed import (
    SIGNATURE_FIELDS, SpeedResolver, decode_speed, read_raw_capture, sample_signature,
)
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso
from app.ecu.profiles.honda_keihin_71_17 import decode_production_values, normalize_frame


def coalesce(segments: list[dict], field: str) -> list[dict]:
    """Recover one label axis without counting the other axis's intersections."""
    runs: list[dict] = []
    for segment in sorted(segments, key=lambda s: (s["start_s"], s["end_s"])):
        if (runs and runs[-1]["label"] == segment[field]
                and abs(runs[-1]["end_s"] - segment["start_s"]) <= 1e-6):
            runs[-1]["end_s"] = segment["end_s"]
            runs[-1]["proposal_ids"].append(segment["id"])
        else:
            runs.append({"start_s": segment["start_s"], "end_s": segment["end_s"],
                         "label": segment[field], "proposal_ids": [segment["id"]]})
    return runs


def distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "total_s": 0.0}
    return {"count": len(values), "total_s": float(sum(values)),
            **{name: float(np.quantile(values, q)) for name, q in
               (("min_s", 0), ("p10_s", .1), ("median_s", .5), ("p90_s", .9), ("max_s", 1))}}


def axis_stats(runs: list[dict], minimum_s: float) -> dict:
    return {"duration_distribution": distribution([r["end_s"] - r["start_s"] for r in runs]),
            "by_label": {label: distribution([r["end_s"] - r["start_s"] for r in runs if r["label"] == label])
                         for label in sorted({r["label"] for r in runs})},
            "short_counts": {f"below_{threshold:g}s": sum(r["end_s"] - r["start_s"] < threshold - 1e-6 for r in runs)
                             for threshold in sorted({1, 4, 10, minimum_s})},
            "below_rule_minimum_by_label": dict(Counter(r["label"] for r in runs
                if r["end_s"] - r["start_s"] < minimum_s - 1e-6))}


def oscillations(runs: list[dict], times: np.ndarray, values: np.ndarray,
                 middle_limit: float, window_limit: float, fluctuation_limit: float) -> list[dict]:
    results = []
    for left, middle, right in zip(runs, runs[1:], runs[2:]):
        if (left["label"] != right["label"] or middle["label"] == left["label"]
                or middle["end_s"] - middle["start_s"] > middle_limit
                or right["end_s"] - left["start_s"] > window_limit
                or abs(left["end_s"] - middle["start_s"]) > 1e-6
                or abs(middle["end_s"] - right["start_s"]) > 1e-6):
            continue
        measured = values[(times >= left["start_s"]) & (times <= right["end_s"])]
        summary = _stats(measured)
        span = summary["max"] - summary["min"] if summary else None
        results.append({"start_s": left["start_s"], "end_s": right["end_s"],
                        "labels": [left["label"], middle["label"], right["label"]],
                        "middle_duration_s": middle["end_s"] - middle["start_s"],
                        "measured": summary, "low_amplitude": span is not None and span <= fluctuation_limit})
    return results


def content_sequences(loaded: dict) -> tuple[dict, dict]:
    sequences, embedded = {}, {}
    for sid, session in loaded.items():
        signatures, valid, invalid = [], 0, 0
        mismatches: Counter = Counter()
        for sample in session.samples:
            raw = (sample.get("quality_flags") or {}).get("raw_hex")
            if not raw:
                signatures.append(sample_signature(sample))
                continue
            try:
                frame = parse_raw_hex(raw)
                speed, _ = decode_speed(frame)
                if speed is None:
                    raise ValueError("invalid embedded frame")
                normalized, _ = normalize_frame(frame)
                decoded = decode_production_values(normalized)
                signature = tuple(decoded.get(k) for k in SIGNATURE_FIELDS) + tuple(decoded["unknown_reserved_raw"])
                valid += 1
                for key in SIGNATURE_FIELDS:
                    value = sample.get(key)
                    if value is None:
                        value = sample.get({"tps_raw": "tps_raw_candidate", "ect_c": "ect_c_candidate"}.get(key, key))
                    if value != decoded.get(key):
                        mismatches[key] += 1
                signatures.append(signature)
            except (ValueError, TypeError, KeyError):
                invalid += 1
                signatures.append(None)
        sequences[sid] = signatures
        if valid or invalid:
            embedded[sid] = {"valid_frames": valid, "invalid_frames": invalid,
                             "canonical_vs_redecoded_raw_mismatch_samples_by_signal": dict(mismatches),
                             "evidence_path": f"data/telemetry/{sid}/samples.jsonl:quality_flags.raw_hex",
                             "note": "Embedded RAW validates content lineage, not independent real-world capture origin. CTX2 did not resolve speed from this path."}
    return sequences, embedded


def containing_ranges(sequences: dict, loaded: dict) -> dict:
    index: dict = defaultdict(list)
    for sid, sequence in sequences.items():
        for offset, signature in enumerate(sequence):
            if signature is not None:
                index[signature].append((sid, offset))
    matches: dict = defaultdict(list)
    for sid, sequence in sequences.items():
        if not sequence or any(value is None for value in sequence):
            continue
        for parent, offset in index.get(sequence[0], []):
            if parent == sid or sequences[parent][offset:offset + len(sequence)] != sequence:
                continue
            times = loaded[sid].elapsed_s
            other = loaded[parent].elapsed_s[offset:offset + len(sequence)]
            error = max(abs((t - times[0]) - (u - other[0])) for t, u in zip(times, other, strict=True))
            if error <= .005:
                matches[sid].append({"parent_entry_id": parent, "sample_offset": offset,
                                     "time_offset_s": other[0], "sample_count": len(sequence),
                                     "relative_timestamp_max_error_s": error,
                                     "basis": "complete six production signals + four reserved bytes + relative timestamps; embedded RAW redecoded where present"})
    return dict(matches)


def reconcile(loaded: dict, source_map: dict, manifest_sha: str) -> dict:
    if source_map["corpus_manifest_sha256"] != manifest_sha:
        raise ValueError("RAW source map manifest SHA mismatch")
    sequences, embedded = content_sequences(loaded)
    matches = containing_ranges(sequences, loaded)
    mapped = {s["session_id"]: s for s in source_map["sessions"]}
    with (repo_root() / "data/calibration/session_labels.csv").open(newline="", encoding="utf-8") as handle:
        seeds = {row["session_id"]: row for row in csv.DictReader(handle)
                 if row["label_source"] == "explicit_demo_seed_generation"}
    # Content-connected entries remain together even when their origin is unresolved.
    parents = {sid: sid for sid in loaded}

    def root(sid):
        while parents[sid] != sid:
            sid = parents[sid]
        return sid

    def union(a, b):
        ra, rb = sorted((root(a), root(b)))
        parents[rb] = ra

    raw_groups: dict = defaultdict(list)
    for sid, mapping in mapped.items():
        if mapping.get("raw_source_sha256"):
            raw_groups[mapping["raw_source_sha256"]].append(sid)
    for ids in raw_groups.values():
        for sid in ids[1:]:
            union(ids[0], sid)
    for sid, ranges in matches.items():
        for interval in ranges:
            union(sid, interval["parent_entry_id"])
    members: dict = defaultdict(list)
    for sid in loaded:
        members[root(sid)].append(sid)
    representatives = {}
    for sha, ids in raw_groups.items():
        mapping = mapped[ids[0]]
        raw = read_raw_capture(str(repo_root() / mapping["raw_source_path"]), sha)
        full = [sid for sid in ids if mapped[sid]["sample_offset"] == 0
                and mapped[sid]["sample_count"] == len(raw.speeds)]
        if full:
            representatives[sha] = sorted(full)[0]
    records = []
    for sid, session in loaded.items():
        mapping = mapped[sid]
        ranges = matches.get(sid, [])
        raw_sha = mapping.get("raw_source_sha256")
        raw_path = mapping.get("raw_source_path")
        lineage = {"parent_capture_id": None, "parent_source_sha256": None,
                   "parent_source_path": None, "sample_offset": None, "time_offset_s": None,
                   "end_time_offset_s": None, "sample_count": len(session.samples)}
        if raw_sha:
            raw = read_raw_capture(str(repo_root() / raw_path), raw_sha)
            offset = mapping["sample_offset"]
            times = raw.timestamp_candidates[mapping["timestamp_field"]]
            lineage.update(parent_capture_id=Path(raw_path).parent.name if Path(raw_path).name == "raw_frames.jsonl" else Path(raw_path).stem,
                           parent_source_sha256=raw_sha, parent_source_path=raw_path, sample_offset=offset,
                           time_offset_s=(times[offset] - times[0]) / 1000,
                           end_time_offset_s=(times[offset + len(session.samples) - 1] - times[0]) / 1000)
            classification = ("independent real capture" if representatives.get(raw_sha) == sid else
                              "alias" if len(session.samples) == len(raw.speeds) else "split capture")
            reason = "Verified full contiguous RAW signal/byte/timestamp match. Distinct files identify capture groups, not 57 independent observations."
        elif sid in seeds or sid.startswith("esp-fake-"):
            classification = "synthetic/derived"
            reason = seeds[sid]["notes"] if sid in seeds else "Explicit esp-fake demo identity, HTTP simulator metadata and synthetic signal series. Original real capture parent not established."
        elif any(len(sequences[r["parent_entry_id"]]) > len(sequences[sid]) for r in ranges):
            parent_range = max(ranges, key=lambda r: len(sequences[r["parent_entry_id"]]))
            parent = parent_range["parent_entry_id"]
            lineage.update(parent_capture_id=f"unresolved-origin:{parent}", parent_source_path=loaded[parent].metadata.source.path,
                           parent_source_sha256=loaded[parent].metadata.source.sha256,
                           sample_offset=parent_range["sample_offset"], time_offset_s=parent_range["time_offset_s"],
                           end_time_offset_s=parent_range["time_offset_s"] + session.metadata.duration_s)
            classification = "split capture"
            reason = "Verified slice of embedded RAW replay; original recording origin/independence unresolved."
        else:
            classification = "unresolved provenance"
            reason = "Ingestion metadata or a real-session name is not proof of an independent capture; original capture lineage unavailable."
        group_raw = sorted({mapped[member].get("raw_source_sha256") for member in members[root(sid)]
                            if mapped[member].get("raw_source_sha256")})
        group = "capture:" + group_raw[0] if group_raw else "content-group:" + root(sid)
        eligible = bool(raw_sha) and classification != "synthetic/derived"
        records.append({"session_id": sid, "source_sha256": session.metadata.source.sha256,
                        "source_path": session.metadata.source.path, "classification": classification,
                        "reason": reason, "lineage": lineage, "related_entry_ranges": ranges,
                        "embedded_raw": embedded.get(sid), "correlation_group_id": group,
                        "correlated_entry_ids": sorted(set(members[root(sid)]) - {sid}),
                        "evaluation_eligible": eligible,
                        "evaluation_policy": "group_only_with_verified_parent" if eligible else "quarantine_until_origin_reconciled; synthetic excluded from real-capture evaluation"})
    return {"schema_version": "ctx2-provenance-reconciliation-v1", "generated_at": utc_now_iso(),
            "corpus_manifest_sha256": manifest_sha, "pinned_entries": len(records),
            "historical_independent_captured_sessions": 57, "corrected_independent_capture_count": None,
            "independent_capture_count_status": "unresolved",
            "verified_original_raw_capture_groups": len(raw_groups),
            "classification_counts": dict(Counter(r["classification"] for r in records)),
            "evaluation_eligible_entries": sum(r["evaluation_eligible"] for r in records),
            "quarantined_entries": sum(not r["evaluation_eligible"] for r in records),
            "evidence": {"raw_source_map_sha256": file_sha256(repo_root() / "data/context_annotation/raw_signal_sources.json"),
                         "synthetic_seed_labels_sha256": file_sha256(repo_root() / "data/calibration/session_labels.csv")},
            "policy": "Never split entries independently. Quarantine unresolved-origin and synthetic entries. All aliases/slices/shared content in one correlation group must stay in the same partition. Manifest remains unchanged; historical 57 is an entry count, not verified independence.",
            "sessions": records}


def validate_split(reconciliation: dict, split: dict) -> dict:
    """Fail closed for research partition requests; does not change analyzer behavior."""
    records = {r["session_id"]: r for r in reconciliation["sessions"]}
    seen, groups = set(), {}
    if not split or not isinstance(split, dict):
        raise ValueError("split must map partition names to session ID lists")
    for partition, ids in split.items():
        if not isinstance(ids, list):
            raise ValueError("partition must be a session ID list")
        for sid in ids:
            if sid not in records or sid in seen:
                raise ValueError(f"unknown or repeated session: {sid}")
            record = records[sid]
            if not record["evaluation_eligible"]:
                raise ValueError(f"quarantined provenance: {sid}")
            group = record["correlation_group_id"]
            if group in groups and groups[group] != partition:
                raise ValueError(f"capture group crosses partitions: {sid}")
            seen.add(sid)
            groups[group] = partition
    return {"valid": True, "entries": len(seen), "capture_groups": len(groups)}


def validate_split_inputs(report: dict, corpus_manifest: Path) -> None:
    if report["corpus_manifest_sha256"] != file_sha256(corpus_manifest):
        raise ValueError("reconciliation manifest SHA mismatch")
    records = {r["session_id"]: r for r in report["sessions"]}
    sources = manifest_sources(corpus_manifest)
    if len(records) != len(report["sessions"]) or set(records) != {s.source_id for s in sources}:
        raise ValueError("reconciliation session set differs from pinned corpus")
    for source in sources:
        if source.sha256 != source.expected_sha256 or records[source.source_id]["source_sha256"] != source.sha256:
            raise ValueError(f"pinned source SHA mismatch: {source.source_id}")
    for relative, key in (("data/context_annotation/raw_signal_sources.json", "raw_source_map_sha256"),
                          ("data/calibration/session_labels.csv", "synthetic_seed_labels_sha256")):
        if file_sha256(repo_root() / relative) != report["evidence"][key]:
            raise ValueError(f"reconciliation evidence changed: {relative}")
    originals = {r["lineage"]["parent_source_path"]: r["lineage"]["parent_source_sha256"]
                 for r in report["sessions"] if r["evaluation_eligible"]}
    for path, sha in originals.items():
        if file_sha256(repo_root() / path) != sha:
            raise ValueError(f"reconciliation RAW SHA mismatch: {path}")


def measured_interval(session, speed: np.ndarray, start: float, end: float) -> dict:
    times = np.asarray(session.elapsed_s)
    mask = (times >= start) & (times <= end)
    return {"sample_count": int(mask.sum()), **{key: _stats(values[mask]) for key, values in
        (("speed_kmh", speed), ("rpm", _array(session.samples, ("rpm",))),
         ("tps_voltage", _array(session.samples, ("tps_voltage",))),
         ("ect_c", _array(session.samples, ("ect_c", "ect_c_candidate"))))}}


def pilot_cases() -> list[dict]:
    cases = []
    for source in discover_sources(repo_root() / "real_data/real_run"):
        if source.source_id not in {"session_000002", "session_000004", "session_000005", "session_000007"}:
            continue
        session = load_source(source)
        document = suggest_context(session).model_dump(mode="json")
        raw = read_raw_capture(source.path, source.sha256)
        speed = np.asarray(raw.speeds, dtype=float)
        selected = []
        # Longest example per operating label and a middle event per type avoid cherry-picking peaks.
        for label in sorted({s["operating_context"] for s in document["segments"]}):
            item = max((s for s in document["segments"] if s["operating_context"] == label),
                       key=lambda s: s["end_s"] - s["start_s"])
            selected.append({"purpose": f"representative {label}", "proposal": item,
                             "measured": measured_interval(session, speed, item["start_s"], item["end_s"]),
                             "boundary_neighborhoods": [{"boundary_s": boundary,
                                 "before": measured_interval(session, speed, max(0, boundary - 3), boundary),
                                 "after": measured_interval(session, speed, boundary, min(session.metadata.duration_s, boundary + 3))}
                                 for boundary in (item["start_s"], item["end_s"])]})
        thermal = coalesce(document["segments"], "thermal_phase")
        boundaries = []
        for before, after in zip(thermal, thermal[1:]):
            boundary = before["end_s"]
            boundaries.append({"boundary_s": boundary, "from": before["label"], "to": after["label"],
                               "before": measured_interval(session, speed, max(0, boundary - 30), boundary),
                               "after": measured_interval(session, speed, boundary, min(session.metadata.duration_s, boundary + 30))})
        operating = coalesce(document["segments"], "operating_context")
        for kind in sorted({e["event"] for e in document["events"]}):
            events = [e for e in document["events"] if e["event"] == kind]
            item = events[len(events) // 2]
            selected.append({"purpose": f"representative {kind}", "proposal": item,
                             "measured": measured_interval(session, speed, item["start_s"], item["end_s"])})
        cases.append({"session_id": source.source_id, "source_sha256": source.sha256,
                      "scope": "full original RAW capture; overlapping pinned slices are not independent cases",
                      "rule_version": document["rule_version"], "config_sha256": document["config_sha256"],
                      "metrics": document["metrics"], "operating": axis_stats(operating, 4),
                      "thermal": axis_stats(thermal, 20), "thermal_transitions": boundaries,
                      "operating_runs": operating, "thermal_runs": thermal, "selected_proposals": selected})
    return cases


def audit(corpus_manifest: Path, suggestions_dir: Path, output_dir: Path) -> dict:
    manifest_sha = file_sha256(corpus_manifest)
    sources = list(manifest_sources(corpus_manifest))
    loaded, records, documents = {}, [], []
    resolver = SpeedResolver()
    protected = {str(p): file_sha256(p) for p in (repo_root() / "data/context_annotation/annotations").glob("*.json")}
    protected[str(corpus_manifest)] = manifest_sha
    for relative in ("data/context_annotation/raw_signal_sources.json", "data/calibration/session_labels.csv"):
        p = repo_root() / relative
        protected[str(p)] = file_sha256(p)
    all_operating, all_thermal, all_joint, event_counts = [], [], [], Counter()
    for source in sources:
        session = load_source(source)
        if session.metadata.status != "ok" or source.sha256 != source.expected_sha256:
            raise ValueError(f"pinned source unavailable or changed: {source.source_id}")
        loaded[source.source_id] = session
        path = suggestions_dir / f"{source.source_id}.suggestions.json"
        protected[str(path)] = file_sha256(path)
        document = SuggestionDocument.model_validate_json(path.read_text(encoding="utf-8")).model_dump(mode="json")
        if document["source"]["sha256"] != source.sha256:
            raise ValueError(f"proposal source SHA mismatch: {source.source_id}")
        if document["session_id"] != source.source_id:
            raise ValueError(f"proposal session ID mismatch: {source.source_id}")
        documents.append(document)
        speed_list, speed_info = resolver.resolve(session)
        speed = np.asarray([np.nan if s is None else s for s in speed_list])
        if document["signal_provenance"].get("raw_source_sha256") != speed_info.get("raw_source_sha256"):
            raise ValueError(f"proposal RAW evidence changed: {source.source_id}")
        if speed_info.get("raw_source_path"):
            p = repo_root() / speed_info["raw_source_path"]
            protected[str(p)] = file_sha256(p)
        times = np.asarray(session.elapsed_s)
        segments = sorted(document["segments"], key=lambda s: s["start_s"])
        if (not segments or abs(segments[0]["start_s"]) > 1e-6
                or abs(segments[-1]["end_s"] - session.metadata.duration_s) > 1e-6
                or any(abs(before["end_s"] - after["start_s"]) > 1e-6
                       for before, after in zip(segments, segments[1:]))):
            raise ValueError(f"joint proposals overlap or omit session duration: {source.source_id}")
        operating, thermal = (coalesce(document["segments"], key) for key in ("operating_context", "thermal_phase"))
        all_operating.extend(operating)
        all_thermal.extend(thermal)
        all_joint.extend({"start_s": s["start_s"], "end_s": s["end_s"],
                          "label": f"{s['operating_context']} / {s['thermal_phase']}"} for s in document["segments"])
        event_counts.update(e["event"] for e in document["events"])
        motion_checks = []
        for item in document["segments"]:
            label = item["operating_context"]
            if label != "stationary_running" and not label.startswith("moving_"):
                continue
            mask = (times >= item["start_s"]) & (times <= item["end_s"])
            speeds = speed[mask]
            finite = speeds[np.isfinite(speeds)]
            supported = bool(len(speeds) and len(finite) == len(speeds) and speed_info.get("raw_source_sha256"))
            stationary_ok = label != "stationary_running" or bool(len(finite) and np.max(finite) <= 1
                and np.all(_array(session.samples, ("rpm",))[mask] >= 400))
            motion_checks.append({"proposal_id": item["id"], "label": label,
                                  "verified_raw_speed_available": supported, "stationary_constraints_met": stationary_ok,
                                  "speed_min_kmh": float(np.min(finite)) if len(finite) else None,
                                  "speed_max_kmh": float(np.max(finite)) if len(finite) else None,
                                  "moving_speed_sample_fraction": float(np.mean(finite >= 3)) if len(finite) else None})
        missing = {key: int(np.sum(~np.isfinite(_array(session.samples, fields)))) for key, fields in
                   (("rpm", ("rpm",)), ("tps_voltage", ("tps_voltage",)), ("ect_c", ("ect_c", "ect_c_candidate")))}
        missing["verified_raw_speed"] = int(np.sum(~np.isfinite(speed)))
        event_checks = []
        for event in document["events"]:
            evidence, config = event["evidence"], document["config"]
            kind = event["event"]
            if kind == "throttle_burst":
                voltage_basis = evidence.get("tps_basis") == "tps_voltage"
                rise = config["tps_burst_delta_v" if voltage_basis else "tps_burst_delta_raw"]
                rate = config["tps_burst_rate_v_s" if voltage_basis else "tps_burst_rate_raw_s"]
                level = config["tps_burst_above_baseline_v" if voltage_basis else "tps_burst_above_baseline_raw"]
                supported = (evidence.get("tps_rise", 0) >= rise and evidence.get("opening_rate_per_s", 0) >= rate
                             and evidence.get("above_baseline", 0) >= level
                             and evidence.get("hold_s", 0) >= config["tps_burst_hold_s"] * .75)
            elif kind == "repeated_stop_go":
                supported = (evidence.get("verified_cycles", 0) >= config["stop_go_cycles"]
                             and event["end_s"] - event["start_s"] <= config["stop_go_window_s"])
            else:
                rpm_evidence = evidence.get("rpm", {})
                supported = (rpm_evidence.get("min") == 0 and rpm_evidence.get("max", 0) >= config["engine_running_rpm"])
            event_checks.append({"proposal_id": event["id"], "type": kind,
                                 "stored_evidence_meets_declared_rule": bool(supported),
                                 "scope": "evidence completeness/rule consistency, not independent ground truth"})
        op_osc = oscillations(operating, times, speed, 8, 40, 3)
        th_osc = oscillations(thermal, times, _array(session.samples, ("ect_c", "ect_c_candidate")), 20, 120, 2)
        records.append({"session_id": source.source_id, "source_sha256": source.sha256,
                        "proposal_sha256": protected[str(path)], "duration_s": session.metadata.duration_s,
                        "joint_segment_count": len(document["segments"]), "event_marker_count": len(document["events"]),
                        "short_operating_runs": [r for r in operating if r["end_s"] - r["start_s"] < 4 - 1e-6],
                        "very_short_joint_segments": [{k: s[k] for k in ("id", "start_s", "end_s", "operating_context", "thermal_phase")}
                                                      for s in document["segments"] if s["end_s"] - s["start_s"] < 1 - 1e-6],
                        "operating": axis_stats(operating, 4), "thermal": axis_stats(thermal, 20),
                        "speed_provenance": speed_info, "missing_sample_counts": missing,
                        "operating_oscillations": op_osc, "thermal_oscillations": th_osc,
                        "motion_evidence_checks": motion_checks,
                        "event_evidence_checks": event_checks,
                        "rule_conflicting_samples": document["metrics"]["rule_conflicting_samples"],
                        "speed_outliers_rejected": document["metrics"]["speed_outliers_rejected"],
                        "capture_gaps": document["timestamp_integrity"]["capture_gaps"],
                        "evidence_quality_counts": dict(Counter(s["evidence_quality"] for s in document["segments"] + document["events"])),
                        "review_status_counts": dict(Counter(s["review_status"] for s in document["segments"] + document["events"]))})
        print(f"audited {source.source_id}", flush=True)
    reconciliation = reconcile(loaded, json.loads((repo_root() / "data/context_annotation/raw_signal_sources.json").read_text()), manifest_sha)
    cases = pilot_cases()
    for case in cases:
        p = repo_root() / "real_data/real_run" / f"{case['session_id']}.jsonl"
        protected[str(p)] = case["source_sha256"]
    unchanged = (all(file_sha256(Path(path)) == sha for path, sha in protected.items())
                 and all(source.sha256 == source.expected_sha256 for source in manifest_sources(corpus_manifest)))
    if not unchanged:
        raise ValueError("audit inputs changed during audit")
    duration = sum(r["duration_s"] for r in records)
    unavailable_duration = sum(r["duration_s"] for r in records if r["speed_provenance"]["status"] == "unavailable")
    motion = [m for r in records for m in r["motion_evidence_checks"]]
    sample_hashes: dict = defaultdict(list)
    for source in sources:
        sample_path = Path(source.path) / "samples.jsonl" if source.is_directory else Path(source.path)
        sample_hashes[file_sha256(sample_path)].append(source.source_id)
    payload = {"schema_version": "ctx2-proposal-quality-v1", "generated_at": utc_now_iso(),
               "corpus_manifest_sha256": manifest_sha, "pinned_entries": len(records),
               "rule_versions": sorted({d["rule_version"] for d in documents}),
               "config_sha256s": sorted({d["config_sha256"] for d in documents}),
               "total_proposals": len(all_joint) + sum(event_counts.values()),
               "joint_segment_count": len(all_joint), "joint_segments": axis_stats(all_joint, 4),
               "operating": axis_stats(all_operating, 4), "thermal": axis_stats(all_thermal, 20),
               "event_marker_count": sum(event_counts.values()), "event_markers_by_type": dict(event_counts),
               "count_explanation": {"joint_segments": len(all_joint), "event_markers": sum(event_counts.values()),
                                     "thermal_intersection_additional_splits": len(all_joint) - len(all_operating),
                                     "note": "Operating and thermal run counts are separate coalesced axes, not additive proposal counts. Events contribute no coverage. Entry totals repeat aliased/split capture content."},
               "coverage": {"entry_weighted_duration_s": duration,
                            "proposed_unknown_operating_s": sum(r["end_s"] - r["start_s"] for r in all_operating if r["label"] == "unknown"),
                            "proposed_unknown_thermal_s": sum(r["end_s"] - r["start_s"] for r in all_thermal if r["label"] == "unknown"),
                            "unknown_operating_with_unavailable_speed_s": sum(r["operating"]["by_label"].get("unknown", {}).get("total_s", 0)
                                for r in records if r["speed_provenance"]["status"] == "unavailable"),
                            "unknown_operating_with_available_speed_s": sum(r["operating"]["by_label"].get("unknown", {}).get("total_s", 0)
                                for r in records if r["speed_provenance"]["status"] != "unavailable"),
                            "speed_unavailable_entries": sum(r["speed_provenance"]["status"] == "unavailable" for r in records),
                            "speed_unavailable_entry_duration_s": unavailable_duration,
                            "verified_annotation_coverage_added_s": 0.0,
                            "note": "Proposed unknown is not human-reviewed unknown. None of these proposals counts as annotated duration."},
               "motion_evidence": {"motion_and_stationary_joint_segments": len(motion),
                                   "verified_raw_speed_supported": sum(m["verified_raw_speed_available"] for m in motion),
                                   "unsupported": [m for m in motion if not m["verified_raw_speed_available"]],
                                   "stationary_constraint_failures": [m for m in motion if not m["stationary_constraints_met"]]},
               "event_evidence": {"checked": sum(len(r["event_evidence_checks"]) for r in records),
                                  "rule_inconsistencies": [{"session_id": r["session_id"], **event} for r in records
                                      for event in r["event_evidence_checks"] if not event["stored_evidence_meets_declared_rule"]],
                                  "scope": "Stored evidence against declared thresholds, not an independent rerun or precision estimate."},
               "oscillation_definition": {"operating": "A-B-A; middle <=8s; entire triplet <=40s; low amplitude = RAW speed range <=3km/h",
                                          "thermal": "A-B-A; middle <=20s; entire triplet <=120s; low amplitude = ECT range <=2C",
                                          "note": "Screening candidates, not automatically erroneous boundaries; overlapping triplets and duplicated entries are counted."},
               "operating_oscillation_count": sum(len(r["operating_oscillations"]) for r in records),
               "low_amplitude_operating_oscillation_count": sum(o["low_amplitude"] for r in records for o in r["operating_oscillations"]),
               "thermal_oscillation_count": sum(len(r["thermal_oscillations"]) for r in records),
               "low_amplitude_thermal_oscillation_count": sum(o["low_amplitude"] for r in records for o in r["thermal_oscillations"]),
               "rule_conflicting_samples": sum(r["rule_conflicting_samples"] for r in records),
               "speed_outliers_rejected": sum(r["speed_outliers_rejected"] for r in records),
               "source_hash_audit": {"all_pinned_sources_and_proposal_source_hashes_match": True,
                                     "all_joint_segments_contiguous_nonoverlapping": True,
                                     "duplicate_source_tree_sha256": [],
                                     "duplicate_samples_file_sha256": {sha: ids for sha, ids in sample_hashes.items() if len(ids) > 1},
                                     "note": "Unique directory hashes do not establish unique capture content; metadata can differ on byte-identical sample files."},
               "evidence_conflicts": {"embedded_raw_vs_canonical": [{"session_id": r["session_id"], **r["embedded_raw"]}
                    for r in reconciliation["sessions"] if r["embedded_raw"]],
                    "note": "Legacy decoder byte-contract differences and demo modifications are not RPM/speed rule conflicts; quarantine these origins. No production decoder or proposal changes made."},
               "input_hashes_unchanged": unchanged, "protected_input_sha256": protected,
               "sessions": records}
    atomic_write_json(output_dir / "ctx2_proposal_quality.json", payload)
    atomic_write_json(output_dir / "ctx2_provenance_reconciliation.json", reconciliation)
    atomic_write_json(output_dir / "ctx2_pilot_cases.json", {"generated_at": utc_now_iso(), "cases": cases,
        "scope": "Desk-review evidence pack; no independent timestamp ground truth, human acceptance or accuracy claim."})
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, default=Path("data/context_annotation/curated_corpus.json"))
    parser.add_argument("--suggestions-dir", type=Path, default=Path("data/context_annotation/suggestions"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/context_annotation/exports"))
    parser.add_argument("--validate-split", type=Path, help="Validate {partition: [session IDs]} against the existing reconciliation; no annotations changed")
    args = parser.parse_args()
    if args.validate_split:
        report = json.loads((args.output_dir / "ctx2_provenance_reconciliation.json").read_text())
        validate_split_inputs(report, args.corpus_manifest)
        print(json.dumps(validate_split(report, json.loads(args.validate_split.read_text())), indent=2))
    else:
        payload = audit(args.corpus_manifest, args.suggestions_dir, args.output_dir)
        print(json.dumps({k: payload[k] for k in ("total_proposals", "joint_segment_count", "event_marker_count", "input_hashes_unchanged")}, indent=2))


if __name__ == "__main__":
    main()
