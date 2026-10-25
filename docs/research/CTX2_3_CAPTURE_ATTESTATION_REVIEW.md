# CTX2.3 Capture Attestation And Human Context Review

Campaign: `ecuread-filtered-raw-18-v1`. Collector declaration date: 2026-10-11.
Decision: `attestation_recorded_review_ready`, meaning provenance is stored and
the review workflow is functional, **not** that human context review is complete
or that segmentation/detector accuracy has been established.

## Attestation And Technical Scope

`data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/provenance/operator_attestation.v1.json`
records `data_collector` / `operator_declared_independence`, the exact 18 source
IDs and SHA-256 hashes, one physical capture group per original, declaration date,
and evidence limitations. The collector confirms 18 separate runs, varied
motorcycle conditions, continuous out-of-city rides for 016/018/023, and no
intentional splitting of one selected file to create another selected file.

Registry: `data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json`.
It reports **18 operator-attested independent captures**, **machine-verified
independent captures: null**; the legacy `independent_captured_sessions` remains
null. The pre-attestation registry is preserved byte-for-byte in provenance.
This attestation does not extend to unrelated sources, new hashes or synthetic data.

Technically checked: all 18 unique hashes, 100,688 valid native frames, 25,189.348s
aggregate duration, strictly increasing elapsed time, no inter-frame gaps above
one second. The pinned prior full 16-frame overlap audit found no cross-file
matches. These facts are consistent with, but do not prove, the declaration.
There are no independently authenticated device-run IDs, absolute acquisition
clocks or GPS evidence. Physical independence, route and collector identity are
not falsely reported as machine-verified facts.

The new registry is versioned to `context-raw-corpus-v2`; no selected source IDs,
paths or SHA values changed. Re-import preserves the attestation rather than
downgrading the registry. The old 57-entry manifest, accepted annotations and
historical research outputs remain unchanged.

## Evaluation Policy

Default `strict_machine_verified` remains blocked for these captures.
`operator_attested_capture_groups_v1` is an explicitly selected alternative:

```bash
python -m app.context_annotation.raw_campaign \
  --evaluation-policy operator_attested_capture_groups_v1 \
  --validate-split proposed_split.json
```

This validates a proposed source-ID partition only; no evaluation is run. It
rechecks exact source and provenance hashes and rejects cross-partition capture
groups. Results using this policy must disclose the operator-attestation basis.

`provenance/capture_groups.v1.json` overlays, rather than rewrites, old lineage.
All 43 linked historical entries inherit a parent's physical group: 11 full,
26 slices, four aliases and two replays. The 41 decoder-equivalent linked entries
are eligible only under the explicit operator policy. The two decoder-conflicted
replays inherit groups but remain excluded. Seven synthetic/demo and seven
outside-selected-corpus entries remain excluded. No synthetic data was promoted.
Old offsets and parent hashes are retained. Future derivations must inherit an
exact validated parent group, not mint their own; unknown derived IDs are refused
until explicitly registered and validated. No augmented records were produced.

## Confirmed Trip Metadata

Only 016, 018 and 023 receive `capture_type: road`,
`trip_context: out_of_city`, `ride_pattern: continuous_long_ride`, and
`metadata_source: operator_attestation`. It is shown as separate read-only
collector metadata, not inserted into operating labels or auto-label inputs.
Other sessions retain unknown collector trip context. No GPS, location,
traffic conditions or time-specific truth is inferred.

## Pilot Effort And Evidence

`exports/ctx23_review_effort.json` includes full measured evidence, proposed
boundaries and representative initial segments, thermal changes, long segments
and throttle bursts for each pilot. Priorities are descriptions for reviewer
attention, not accepted labels or precise real-world ground truth.

| Session | Joint operating proposals | Operating runs | Median run (s) | Events | A-B-A / low amplitude |
|---|---:|---:|---:|---:|---:|
| 002 | 203 | 203 | 5.751 | 84 | 82 / 0 |
| 004 | 110 | 108 | 4.999 | 64 | 35 / 1 |
| 005 | 255 | 252 | 5.500 | 138 | 108 / 0 |
| 007 | 216 | 215 | 5.999 | 65 | 88 / 0 |
| 018 | 534 | 529 | 5.999 | 312 | 184 / 1 |

Joint proposals intersect operating and thermal axes; coalesced runs remove
thermal-only boundaries. A-B-A means contiguous same/different/same labels,
middle <=8s and total span <=40s. Low amplitude means full measured speed range
<=3 km/h. There are no thermal A-B-A cases under the same duration criterion.
These are measured queue/oscillation statistics, not a precision or recall result.
Higher-amplitude cases still require human boundary inspection.

Initial measured evidence illustrates why review cannot simply follow narratives:

| Session | Proposed range (s) | Measured speed range (km/h) | RPM range | ECT (C) | Proposed context / thermal |
|---|---|---|---|---|---|
| 002 | 0-2.627 | 0-2 | 929-3460 | 48 | unknown / warming |
| 004 | 0-0.125 | 0 | 20 | 89 | unknown / stable |
| 005 | 0-2.6235 | 0-3 | 862-3804 | 90 | unknown / unknown |
| 007 | 0-8.876 | 0-2 | 20-3569 | 89 | unknown / stable |
| 018 | 0-6.126 | 0-1 | 896-4102 | 92-93 | stationary_running / unknown |

For 005, a near-constant initial ECT does not force the unchanged window-based
thermal engine to label it stable. For 018, a continuous capture does not imply
the vehicle moves at every instant. RAW byte-19 speed evidence is available
throughout each pilot, rider-corroborated but not GPS-calibrated. RPM alone is
not a motion measurement. Full evidence includes TPS voltage and thermal-window
trends; reviewers must inspect those when correcting or accepting boundaries.

All five pilots currently have **0 accepted, 0 corrected, 0 rejected and 0
uncertain campaign proposals**. Manual review time is **null / unmeasured** and
boundary-correction count is zero. All 4,643 campaign suggestions remain pending;
all 18 sessions remain unstarted. Absent annotations have zero explicit-unknown
coverage, not unknown labels standing in for missing work.

Five-to-six-second medians and 312 events on 018 imply a substantial inspection
queue. No arbitrary reduction or segmentation tuning was performed. The minimal
UX improvement is a Kind filter (Operating, Events, Throttle bursts), applied to
Next pending and range acceptance; the existing pagination, evidence and review
controls are retained. Additional filtering by evidence quality or bookmarked
resume points should be considered only after actual reviewer timing is available.

## Workflow And Snapshot Gate

Browser verification exercised all five pilot views and speed/RPM/TPS/ECT
evidence, then boundary correction, Accept, Reject, Uncertain, throttle markers,
reload, and unchanged-rule regeneration on **temporary sidecars only**. Accepted
annotation bytes persisted through reload and regeneration; accepted suggestions
cannot be overwritten through the pending correction endpoint. Named manual
labels retain confirmation provenance. Mobile/desktop layouts and nonblank speed
canvases passed; no browser errors. Automated timing is not human review time.

The separate `annotation_snapshot` command requires every original reviewed,
full accepted segment coverage, named human provenance, and no pending/uncertain
segment or event decisions. Explicit unknown context is allowed and counted
separately from unannotated duration. It records source/annotation hashes,
attestation, decoder/schema/rule versions, human decisions, labels, coverage and
creation timestamp; RAW is referenced, not copied. Existing output cannot be
overwritten, and deterministic content identity excludes the timestamp. It
neither trains models nor evaluates detector accuracy.

```bash
python -m app.context_annotation.annotation_snapshot \
  --corpus-manifest data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json \
  --output data/context_annotation/datasets/ecuread-filtered-raw-18-v1-annotations-v1
```

The real campaign currently refuses that command with **18 sessions remaining**.
No snapshot was created. The historical 57-entry freeze remains separate.

## Verification

- `exports/ctx23_verification.json`: 480 protected files unchanged, including all
  RAW sources, old accepted labels, historical exports, model artifacts and the
  unchanged auto-label engine. All 18 exact hashes and unique groups preserved.
- `exports/ctx23_browser_verification.json` and `exports/ctx23-browser/`: isolated
  browser results/screenshots; zero real campaign annotation writes or acceptance.
- Regression tests cover declaration scope/counts/hash tampering, strict-policy
  preservation, group inheritance/leakage, idempotent re-import, review controls,
  correction/time accounting, incomplete snapshot refusal, deterministic complete
  toy snapshots, concurrent-write refusal and no overwrite. Final full suite:
  **306 passed, 0 failed, 0 errors, 0 skipped**; 19 are new CTX2.3 regressions.
  `exports/ctx23_tests.json` records the command, runtime and tested-code hashes.
  There are 6,631 existing Starlette/httpx and joblib/NumPy deprecation warnings.
  An earlier full run was interrupted by SIGTERM (exit 143), not counted as a
  pass; the subsequent complete final run above passed without suppressed tests.
- No RAW/model/threshold/taxonomy changes, training, detector evaluation,
  automatic acceptance, old-label migration or original-campaign rewrites.

Studio: `http://127.0.0.1:8768/context-annotation`. Start with 002, 004, 005, 007,
then 018. Readiness here authorizes human review, not large-scale label acceptance.
