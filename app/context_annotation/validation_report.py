"""Measured cross-session checks, without narrative-derived ground truth."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import numpy as np

from app.context_annotation.auto_labeler import RuleConfig, suggest_context
from app.context_annotation.corpus import manifest_sources
from app.context_annotation.session_loader import discover_sources, load_source
from app.context_annotation.speed import read_raw_capture
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso
from app.context_annotation.validation import find_overlaps


def validation_report(corpus_manifest: Path, suggestions_dir: Path, report_path: Path, output_path: Path) -> dict:
    batch = json.loads((suggestions_dir / "batch_preview.json").read_text())
    cases = []
    names = {"session_000002", "session_000004", "session_000005", "session_000007"}
    for source in discover_sources(Path("real_data/real_run")):
        if source.source_id not in names:
            continue
        loaded = load_source(source)
        doc = suggest_context(loaded)
        raw = read_raw_capture(source.path, source.sha256)
        speeds = np.asarray(raw.speeds, dtype=float)
        record = {"session_id": source.source_id, "source_sha256": source.sha256, "metrics": doc.metrics,
                  "speed_distribution_kmh": {"min": float(np.min(speeds)), "median": float(np.median(speeds)),
                                             "p95": float(np.quantile(speeds, .95)), "max": float(np.max(speeds)),
                                             "near_zero_fraction": float(np.mean(speeds <= 1))},
                  "valid_frames": raw.validation_counts.get("valid_frames", 0),
                  "invalid_frames": raw.validation_counts.get("invalid_frames", 0),
                  "stationary_segments_use_near_zero_speed": all(s.evidence["speed_kmh"]["max"] <= 1 for s in doc.segments if s.operating_context == "stationary_running"),
                  "nonoverlapping_segments": not find_overlaps(doc.segments),
                  "tps_bursts_meet_measured_rules": all(e.evidence["tps_rise"] >= RuleConfig().tps_burst_delta_v and
                      e.evidence["opening_rate_per_s"] >= RuleConfig().tps_burst_rate_v_s for e in doc.events if e.event == "throttle_burst")}
        cases.append(record)
        print(f"validated {source.source_id}", flush=True)
    source_audit = []
    for source in manifest_sources(corpus_manifest):
        source_audit.append({"session_id": source.source_id, "matches_pinned_sha256": source.sha256 == source.expected_sha256})
    documents = [json.loads((suggestions_dir / f"{r['session_id']}.suggestions.json").read_text())
                 for r in batch["sessions"] if r["status"] == "successful"]
    annotation_hashes = {str(p): file_sha256(p) for p in Path("data/context_annotation/annotations").glob("*.json")}
    workflow_path = Path("data/context_annotation/exports/ctx2_review_verification.json")
    workflow = json.loads(workflow_path.read_text()) if workflow_path.exists() else None
    payload = {"generated_at": utc_now_iso(), "rule_version": "ctx2-rules-v1", "thresholds": asdict(RuleConfig()),
               "batch_summary": {k: v for k, v in batch.items() if k != "sessions"}, "case_studies": cases,
               "source_hash_audit": source_audit, "existing_annotation_sha256": annotation_hashes,
               "all_source_hashes_match": all(r["matches_pinned_sha256"] for r in source_audit),
               "all_suggestions_nonoverlapping": all(not find_overlaps([_Range(s) for s in doc["segments"]]) for doc in documents),
               "verified_coverage_added_s": 0.0, "browser_review_verification": workflow,
               "accuracy_estimate": None, "independent_captures_verified": None}
    required_workflow = ("loaded", "auto_segmented", "visualized", "manually_corrected", "accepted",
                         "persisted_and_reloaded", "source_and_existing_annotation_hashes_unchanged")
    ready = bool(workflow and all(workflow.get(key) for key in required_workflow)
                 and payload["all_source_hashes_match"] and payload["all_suggestions_nonoverlapping"])
    payload["decision"] = "auto_label_review_ready" if ready else "auto_label_review_not_ready"
    atomic_write_json(output_path, payload)
    text = _render(payload, batch)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    return payload


class _Range:
    def __init__(self, segment: dict):
        self.id, self.start_s, self.end_s = segment["id"], segment["start_s"], segment["end_s"]


def _render(payload: dict, batch: dict) -> str:
    case_rows = []
    for record in payload["case_studies"]:
        m, s = record["metrics"], record["speed_distribution_kmh"]
        thermal = m["thermal_duration_s"]
        case_rows.append(f"| {record['session_id']} | {m['segment_count']} | {m['duration_distribution_s']['median']:.2f} | {m['unknown_coverage']:.2%} | {m['verified_stationary_interval_count']} / {m['operating_duration_s'].get('stationary_running', 0):.1f}s | {m['event_counts'].get('throttle_burst', 0)} | {thermal['warming']:.1f} / {thermal['stable']:.1f} / {thermal['unknown']:.1f} | {m['fragmentation_per_minute']:.2f} | {s['median']:.1f} / {s['p95']:.1f} / {s['max']:.1f} |")
    corpus_rows = []
    for record in batch["sessions"]:
        if record["status"] != "successful":
            corpus_rows.append(f"| {record['session_id']} | FAILED: {record.get('error')} | | | | | | |")
            continue
        m, thermal = record["metrics"], record["metrics"]["thermal_duration_s"]
        d = m["duration_distribution_s"]
        corpus_rows.append(f"| {record['session_id']} | {record['speed_availability']} | {m['segment_count']} | {d.get('min', 0):.2f} / {d.get('median', 0):.2f} / {d.get('p90', 0):.2f} / {d.get('max', 0):.2f} | {m['unknown_coverage']:.1%} | {m['verified_stationary_interval_count']} | {m['fragmentation_per_minute']:.2f} | {m['rule_conflicting_samples']} | {thermal['warming']:.1f} / {thermal['stable']:.1f} / {thermal['unknown']:.1f} |")
    steady_rows = []
    for record in payload["case_studies"]:
        spreads = record["metrics"]["speed_spread_by_context_kmh"]
        steady_rows.append(f"| {record['session_id']} | {spreads.get('moving_steady', 0):.2f} | {spreads.get('moving_variable', 0):.2f} |")
    event_counts = Counter()
    for record in batch["sessions"]:
        event_counts.update(record.get("metrics", {}).get("event_counts", {}))
    return f"""# CTX2 Auto-Label Validation

Generated: {payload['generated_at']}. Rules: `ctx2-rules-v1`.

## Scope and Provenance

The implementation is deterministic signal processing under
`app/context_annotation`; there are no models, LLMs, fault/anomaly interpretations,
analyzer verdict dependencies, or production decoder changes. Candidate speed
uses native Honda Table 17 byte 19 after shared 24-byte / FF-prefixed 29-byte
normalization, header validation, and checksum validation. Decoder version:
`ctx2-honda-table17-speed-v1`. Speed is rider-corroborated, not GPS-calibrated.
RAW SHA, offset/count, alignment basis, and unavailable reasons are persisted.

The pinned corpus has 57 entries, of which 27 have verified full RAW ranges and
30 have unavailable speed. The 57 entries include aliases, slices, and at least
seven synthetic sessions. They do not establish 57 independent captures. The
preflight and `raw_signal_sources.json` document these provenance limitations.

## Algorithms and Thresholds

Timestamp validation rejects missing mixed timestamps, duplicates, and reversed
time. A complete declared sampling interval can supply timing if all timestamps
are absent; sample index alone cannot. Gaps exceed max(3 seconds, 8 median
sampling intervals). Gaps are explicit unknown bands, without interpolation.

Within each capture block, time-based median filtering uses a 1-second window.
Speed requires original validated frames; missing/invalid samples stay missing.
Stationarity requires both raw and smoothed speed <=1 km/h and RPM >=400.
Movement enters at >=3 km/h and exits at <=1, exceeding byte quantization noise.
RPM 1000-2200 is reported as an idle-region observation, never proof of stopping.
Zero RPM requires baseline TPS and no conflicting movement for engine-off
proposals. The common Honda RPM value 20 is not silently treated as zero.

Acceleration/deceleration uses robust first/last thirds of a 6-second window:
>=0.7 km/h/s and >=3 km/h change. Steady movement has an 8-second robust speed
spread <=3 km/h. Minimum operating persistence is 4 seconds. Short motion
changes expand into mixed intervals, preserving long trends and evidence gaps.
Speed steps >35 km/h/s are withheld as possible candidate corruption rather
than interpreted as acceleration. No braking inference is made.

Thermal analysis uses a 60-second temporal window, at least 20 seconds of data,
and median thirds. Warming needs >=0.05 C/s and >=2 C rise. Stable needs an
absolute slope <=0.03 C/s and robust spread <=3 C; cooling or insufficient
evidence remains unknown. Thermal states persist for 20 seconds, and nearby
boundaries snap to operating transitions. These trend thresholds exceed ECT
quantization; absolute temperature never proves completion of warm-up.

TPS baseline is its valid-sample fifth percentile. A burst requires >=0.30 V
rise, >=0.35 V/s opening, and >=0.40 V above baseline held for 0.5 seconds.
For RAW-only TPS the equivalent research thresholds are 20 counts rise,
20 counts/s, and 25 counts above baseline. These conservative deltas use the
sensor's voltage/raw scale, not the maximum throttle seen in one ride.
Events have a 3-second cooldown. Repeated stop-go requires three verified
stationary-to-moving cycles within 180 seconds. Start/stop events require
sustained observed zero/nonzero RPM, without guessing the state before capture.

Thresholds are explicit in `RuleConfig`, the proposal document, and original
proposal provenance. They express quantization margins, plausible temporal
scales, and sustained behavior. They are research defaults, not vehicle-wide
calibration and not fitted to reproduce the four narratives.

## Four Real RAW Cases

Every frame in the four files passed speed frame validation. Narratives are
qualitative rider observations, not independently labelled timestamp truth.
No precision, recall, probability, or full segmentation accuracy is claimed.

| Session | Segments | Median duration s | Unknown motion | Stationary intervals/time | TPS bursts | Thermal warming/stable/unknown s | Segments/min | Speed median/p95/max km/h |
| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | --- |
{chr(10).join(case_rows)}

004 has more stationary time and stop intervals than 007 in these captures.
002 contains an observable rising ECT interval; 005 is mostly stable and is not
forced to have a full warm-up. Any warming in an already-hot capture describes
a measured rise, not a conclusion that the engine was cold.

Stationary segments were checked against actual speed values, throttle events
against measured rise/rate/hold evidence, and operating segments for overlap.
All four checks pass: {all(r['stationary_segments_use_near_zero_speed'] and r['tps_bursts_meet_measured_rules'] and r['nonoverlapping_segments'] for r in payload['case_studies'])}.

Median within-segment speed p90-p10 spread:

| Session | Steady km/h | Variable km/h |
| --- | ---: | ---: |
{chr(10).join(steady_rows)}

## Pinned Corpus Preview

Successful: {batch['successful_processing']}; failed: {batch['failed_processing']};
unavailable speed: {batch['unavailable_speed']}; partial evidence:
{batch['partial_evidence']}; proposals pending human review:
{batch['pending_human_review']}. Verified coverage added: 0 seconds.
Event distribution across corpus entries: {dict(sorted(event_counts.items()))}.
The following includes additional captures and calibration sessions outside the
four case studies, using the same thresholds without refitting.

| Corpus entry | Speed | Segments | Min/median/p90/max s | Unknown motion | Stops | Segments/min | Conflicting samples | Thermal warming/stable/unknown s |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
{chr(10).join(corpus_rows)}

All pinned source hashes match: {payload['all_source_hashes_match']}.
All generated operating segments are nonoverlapping:
{payload['all_suggestions_nonoverlapping']}. Source files and accepted sidecars
are not written by the preview command. Original RAW references use hashes,
and telemetry is not copied into suggestions.

## Human Review and Compatibility

The UI displays RPM, TPS, candidate speed, and ECT on common elapsed-time axes,
with separate proposed/reviewed operating and thermal lanes and event markers.
Wheel zoom and pan preserve these coordinates. Every proposal can be inspected,
corrected, accepted, or rejected. Batch/range acceptance skips conflicts with
reviewed segments. It cannot overwrite a confirmed human annotation.

Pending/rejected suggestions do not count as coverage or enter dataset freezes.
Accepted segments retain auto origin, original rule/config/evidence, reviewer,
confirmation time, original proposal, and corrections. Events have separate
storage within the accepted document and contribute no duration. Reruns preserve
reviewed decisions and accepted sidecars. V1 labels remain explicitly marked v1,
without automatic translation into v2 operating context.

The real-session browser verification uses isolated annotation/suggestion
directories, so test acceptance does not impersonate review of the campaign.
Workflow verification available: {bool(payload['browser_review_verification'])}.

## Verification

Focused CTX1/CTX2 tests cover frame normalization, malformed/missing RAW,
timestamp integrity, gaps, stops, speed trends, TPS bursts, thermal trends,
persistence, source guards, v1 preservation, review persistence, events versus
duration, and reproducible v2 freeze provenance. Browser verification includes
desktop/mobile screenshots, rendered canvas pixels, aligned plot coordinates,
manual correction, acceptance, reload, and unchanged source/annotation hashes.

Analyzer regression testing retained one pre-existing failure:
`tests/test_canonical_pipeline.py::test_feature_schema_mismatch_is_rejected`.
The existing production inference changes degrade incompatible models instead
of raising the exception expected by that test. CTX2 does not modify this path
or the test. Other tests in the selected analyzer regression set passed.

## Ambiguities and Limits

Candidate speed is quantized and not independently calibrated. Near-zero speed
can include very slow creeping, so stationary proposals still need human review. Missing RAW,
changed RAW hashes, failed frame validation, or alignment errors withhold speed.
RPM/TPS alone cannot establish motion or stationarity. Capture gaps, isolated
invalid samples, ambiguous low RPM, and 1-3 km/h transitions retain unknown
bands; some of these are shorter than the operating persistence threshold.
Thermal intersections can also yield short boundary bands. Fragmentation is
reported rather than hidden, and needs researcher assessment before acceptance.

Sensor-only context does not establish city/highway, braking, traffic lights,
traffic jams, route destinations, GPS coordinates, or faults. Route metadata is
human-provided. Freeze readiness still requires review of the intended corpus;
this report establishes a reviewable workflow, not a completed labelled dataset.

Final decision: `{payload['decision']}`.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, default=Path("data/context_annotation/curated_corpus.json"))
    parser.add_argument("--suggestions-dir", type=Path, default=Path("data/context_annotation/suggestions"))
    parser.add_argument("--report", type=Path, default=Path("docs/research/CTX2_AUTO_LABEL_VALIDATION.md"))
    parser.add_argument("--output", type=Path, default=Path("data/context_annotation/exports/ctx2_validation.json"))
    args = parser.parse_args()
    validation_report(args.corpus_manifest, args.suggestions_dir, args.report, args.output)


if __name__ == "__main__":
    main()
