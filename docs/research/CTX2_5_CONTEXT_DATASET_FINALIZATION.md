# CTX2.5 Context Dataset Finalization

Campaign: `ecuread-filtered-raw-18-v1`. Recorded 2026-10-11.

**Decision: `approval_recorded_snapshot_blocked`.** Engineering verification and
the approved research handoff are complete. No frozen snapshot was created.
Bulk plausibility endorsement is not individual boundary validation. Measured
segmentation accuracy and additional timed human review remain unperformed.

## Approval And Identity

The collector explicitly authorized acceptance of the current suggestions for
all 18 pinned originals after inspecting Studio results. The versioned record is
`data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/provenance/operator_context_approval.v1.json`.

- Source: `data_collector`; type: `bulk_operator_endorsement`.
- Scope: `all_18_pinned_sessions`; method: `inspected_auto_label_results`.
- `individual_interval_review: false`.
- Actual record timestamp: `2026-10-11T10:28:40.220387+00:00`.
- Approval file SHA-256: `65b6d38fd76efb187010cf3629e2339ee1e8ac44144b1bc639c1f5369fee32f7`.
- Exact per-session source hashes, pre-acceptance fine/macro file hashes and rule/config/schema versions are embedded in the approval.

Rules remain `ctx2-rules-v1` and `ctx24-macro-v1`; schemas remain
`context-suggestions-v2`, `context-annotation-v2`, `context-macro-review-v1`,
and `context-taxonomy-v2`. No campaign rules were rerun or thresholds changed.
Optional typed `bulk_endorsement` provenance is new; older extra-forbid readers
need the updated annotation package. No `HumanConfirmation` was fabricated.

Decoder provenance remains `honda_keihin_71_17` / `1.0.0`, native Honda Table 17,
`canonical-telemetry-v2`. Speed uses original byte 19 via
`ctx2-honda-table17-speed-v1`, rider-corroborated but not GPS-calibrated.
ECT/IAT remain provisional-high-confidence signals, not newly calibrated data.

## Acceptance And Coverage

All **5,833** annotation items are accepted; **0** unresolved/pending/uncertain
items, **0** acceptance conflicts, and **0** accepted within-track overlaps.
Counts are 3,084 joint fine operating/thermal intervals, 1,559 event markers,
941 operating macros and 249 independent thermal macros. These are separate
representations, not 5,833 independent observations or physical captures.

| Review Provenance | Items | Fine Coverage (s) | Operating Macro Coverage (s) | Thermal Macro Coverage (s) |
| --- | ---: | ---: | ---: | ---: |
| Bulk operator endorsement | 5,531 | 23,713.846 | 24,876.849 | 25,189.348 |
| Earlier multi-item human confirmation | 290 | 1,454.127 | 0 | 0 |
| Earlier single-item human confirmation | 10 | 13.2495 | 312.499 | 0 |
| Earlier manual correction | 2 | 8.1255 | 0 | 0 |

Fine coverage applies independently to both fine axes. Do not sum coverage
across representations. All 302 prior human records are preserved exactly.
The 290 multi-item confirmations are identifiable by shared action IDs; they
must not be reported as individual inspection. Single-item actions/corrections
also do not establish independently verified boundary accuracy.

All four accepted tracks cover **25,189.348 seconds** across **100,688 samples**.
Truly unannotated duration is **0**. Explicit operating unknown is **174.614 s**;
thermal unknown is **3,881.476 s**, not a missing-annotation substitute.
Unknown durations can overlap across axes and must not be added as a union.
The union with at least one unknown fine axis is **4,016.484 s**, reported in
the current campaign-status export; it is fully annotated abstention coverage.

| Context | Fine Intervals | Operating Macros | Duration (s) |
| --- | ---: | ---: | ---: |
| Engine off | 7 | 7 | 67.878 |
| Stationary running | 126 | 83 | 2,505.872 |
| Moving accelerating | 561 | grouped | 3,839.825 |
| Moving decelerating | 554 | grouped | 3,795.8225 |
| Moving variable | 1,409 | grouped | 10,537.9675 |
| Mixed movement macro | n/a | 429 | 18,173.615 |
| Moving steady | 315 | 309 | 4,267.369 |
| Unknown operating | 112 | 113 | 174.614 |

Mixed movement is the aggregate of accelerating/decelerating/variable duration,
not additional coverage. All 126 fine stationary intervals remain exported;
their complete spans are traced into 83 contiguous stationary macro episodes.
No short states, original timestamps, transitions or event markers were removed.

Thermal: stable 19,782.497 s (106 macros), warming 1,525.375 s (27), unknown
3,881.476 s (116). Events: 1,547 throttle bursts, 8 engine stops and 4 repeated
stop/go markers. Events can overlap and are not mandatory operating labels.

## Per-Session Inventory

| Session | Samples | Duration (s) | Fine Intervals | Macros (Both Axes) | Events | 50-Sample Windows |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 001 | 117 | 28.999 | 7 | 6 | 1 | 7 |
| 002 | 5,781 | 1,446.503 | 203 | 55 | 84 | 574 |
| 003 | 1,249 | 312.499 | 41 | 9 | 32 | 120 |
| 004 | 3,017 | 755.001 | 110 | 38 | 64 | 297 |
| 005 | 7,851 | 1,963.997 | 255 | 58 | 138 | 781 |
| 006 | 923 | 230.750 | 30 | 16 | 5 | 88 |
| 007 | 6,389 | 1,598.002 | 216 | 52 | 65 | 634 |
| 014 | 1,202 | 300.500 | 5 | 6 | 1 | 116 |
| 015 | 531 | 132.501 | 17 | 10 | 7 | 49 |
| 016 | 17,965 | 4,494.502 | 576 | 230 | 301 | 1,792 |
| 017 | 3,492 | 873.500 | 108 | 46 | 46 | 345 |
| 018 | 17,976 | 4,496.001 | 534 | 225 | 312 | 1,793 |
| 019 | 2,194 | 548.670 | 67 | 30 | 36 | 215 |
| 020 | 87 | 21.501 | 4 | 6 | 2 | 4 |
| 021 | 3,564 | 891.250 | 95 | 50 | 30 | 352 |
| 022 | 3,021 | 755.001 | 66 | 38 | 28 | 298 |
| 023 | 17,539 | 4,391.172 | 514 | 233 | 277 | 1,749 |
| 024 | 7,790 | 1,948.999 | 236 | 82 | 130 | 775 |

The 18 distinct source hashes and 18 physical groups are preserved.
`operator_attested_independent_captures: 18`;
`machine_verified_independent_captures: null`. The independence declaration is
copied into the handoff; strict machine-verified evaluation remains unchanged.
Evaluation using these groups must explicitly opt into the attestation policy,
keep aliases/derivatives with their parent, and exclude synthetic/unresolved
records. No train/test partition or detector evaluation was performed here.
Only 016/018/023 have operator-confirmed out-of-city continuous-road metadata;
operating context remains separate from trip metadata.

## Handoff And Freeze Blocker

Authoritative non-frozen export:
`data/context_annotation/datasets/ecuread-filtered-raw-18-v1-approved-v2/`.

Content SHA-256:
`5fd672ff45b69681e310efff7661cc8313989cfc10191047dc476e8161e156c1`.

`export_manifest.json` hashes all label/mapping/audit/attestation/approval files,
pins source identities and implementation hashes, and explicitly sets
`frozen: false`. `created_at` is excluded from the deterministic content hash.
Independent re-export produced the same hash and all artifact hashes verified.
Existing versioned output directories are never overwritten. RAW is referenced,
not copied or modified. `quality_audit.json` provides per-session provenance,
coverage, label counts, source/annotation hashes and window-status distributions.

The existing `annotation_snapshot` implementation was actually invoked. It
refused with `missing named human review provenance: session_000003`.
Bulk-endorsed items intentionally have no named per-item `HumanConfirmation` or
forged individual `accept` history. There is no missing-coverage blocker, but
the unchanged snapshot-v1 provenance gate cannot represent this authorization.
It was not weakened. An explicitly approved future snapshot policy or genuine
review reconciliation is needed before a frozen snapshot can be published.

An initial non-frozen `approved-v1` export incorrectly classified every named
confirmation as individual. Its files are retained unchanged for audit, but it
is superseded before handoff by v2. The authoritative publication record is
`campaigns/ecuread-filtered-raw-18-v1/exports/ctx25_export_publication.json`.
Use only v2; never concatenate versions as additional captures.

## Research Mapping

Schema: `context-research-mapping-v1`, documented in `mapping_schema.json`.
Each session has `labels.json`, `samples.jsonl` and `windows.jsonl`.
Canonical identity is `(session_id, canonical_index)` with original sequence,
timestamp and elapsed seconds. Label ranges use zero-based `[start_index,
end_index)` and `[start_s, end_s)`; the final source sample is included when a
label ends at session duration. Empty sample ranges are retained, not discarded.

There are **9,989** complete 50-sample windows, stride 10, with original row
identities. No resampling, padding, capture joining or invalid-row removal occurs.
All window identities/counts match the existing production window constructor
on this all-valid corpus. Trailing samples remain in the sample export even
when not part of a complete window. Future feature eligibility is a separate check.

Each window reports four independent track assignments and separate event IDs.
A boundary in `(first_sample_time, last_sample_time]` makes that track ambiguous,
including same-label boundaries or short intervals with no enclosed samples.
All intersecting IDs and boundary times are retained; `assigned_label` is null
for ambiguous/unresolved windows. A homogeneous `unknown` is `abstained`, not
unresolved. This conservative policy does not silently majority-vote.
No anomaly score, detector prediction, diagnosis or RCA conclusion is used as a
context ground-truth label. Select a representation explicitly; do not flatten
macro and fine tracks into contradictory duplicate labels.

## Commands And Recovery

Stop Studio writers before the offline operation. Explicit collector
authorization is required; the command is not a default auto-label action.

```bash
python -m app.context_annotation.bulk_endorsement \
  --corpus-manifest data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json \
  --operator-authorized
python -m app.context_annotation.approved_export \
  --corpus-manifest data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json \
  --output data/context_annotation/datasets/ecuread-filtered-raw-18-v1-approved-v3
```

Recovery lives in `provenance/bulk_endorsement.v1/`: exact pre-operation sidecars,
staged outputs, before/after hashes, transaction and completion records. Fifty
files changed; four already-identical files were not rewritten. Retrying wrote
zero files and created no duplicates. Each atomic write checks recorded hashes;
later/concurrent human changes fail closed, not overwrite. An interrupted batch
can resume through the same command. Restore backups only after reconciling later
human writes; there is no unsafe blanket rollback. This is an offline local
workflow, not a concurrent multi-reviewer transaction service.

## Verification

- Full regression suite: **347 passed**, zero failures/errors/skips, 60.40 s.
- Known dependency warnings: Starlette/httpx and joblib/NumPy deprecations.
- All 18 original frame counts match canonical samples and mapped sample counts.
- All 3,084 fine proposals and 1,559 original markers retained without relabeling.
- Per-child macro intersections cover each original interval on both axes.
- Accepted annotations, manual corrections and review histories survive reload;
  isolated rerun tests preserve them without rerunning the real campaign.
- All 431 protected RAW/model/historical files match the prior integrity baseline.
- Five-pilot Playwright checks: desktop/mobile layout, nonblank four-signal
  canvases, fine expansion, bulk provenance, accepted-state protection and reload.
  No JavaScript errors; no live sidecar or RAW hashes changed by browser testing.
- Reports: `exports/ctx25_engineering_verification.json`,
  `ctx25_browser_verification.json`, `ctx25_tests.junit.xml`, and
  `ctx25_annotation_campaign_status.json` under the campaign.

Engineering readiness does not imply individual human validation readiness.
No models, production thresholds, DriveSafe routes, source data or earlier
research reports were changed. No fault accuracy or independent expert validation
is claimed, and no automatic training/evaluation follows this export.
