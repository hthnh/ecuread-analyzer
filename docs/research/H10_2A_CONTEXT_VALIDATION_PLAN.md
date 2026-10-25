# H10.2A Targeted Context Validation and Calibration Design

## Status and Scope

**Verdict: `awaiting_targeted_human_review`.** Engineering can prepare and
verify the materials, but all 40 human decisions, boundary checks, and actual
review times remain pending. The calibration design is registered, not
executed. No context cutoff, scaler, or IF is fitted by the packet generator.
There is no automatic progression to calibration after receiving responses.

This remains **EXPLORATORY**. H10.0/H10.1 and the broader corpus have already
influenced model/design choices; new group separation cannot undo that prior
selection exposure. Context consistency is not evidence of normal mechanical
condition, fault sensitivity, or a real false-positive rate.

Production IF, Contextual Battery, RCA, DriveSafe, model files, RAW, approved
annotations/dataset, and H10.0/H10.1 reference results remain unchanged.

## Inputs and Integrity

The sole source corpus is the 18-original approved-v2 campaign:

`data/context_annotation/datasets/ecuread-filtered-raw-18-v1-approved-v2`

Verified content SHA-256:
`5fd672ff45b69681e310efff7661cc8313989cfc10191047dc476e8161e156c1`.

The campaign has 100,688 samples and 9,989 production-geometry windows.
Physical capture groups use the explicit operator-attestation policy;
machine-verified independence remains **null**. Original source paths, hashes,
groups, and counts are pinned in the protocol. Aliases, historical splits,
synthetic records, and augmentation are not inputs.

The generator verifies H10.1's completion hashes, original protected inputs,
approved-v2 export hashes, and every selected source. It uses the existing
native importer and existing CTX2 RAW-speed decoder, checks original timestamp
alignment, and independently validates the selected captures' exported
sample/window/interval/event mappings. It does not implement another decoder
or use IF predictions to choose individual regions.

`readiness.json` pins the packet, protocol, source-protection inventory,
response template, worksheet, and all 40 plots by file SHA-256. The packet
also has a stable content hash; the protocol records its registration time.
Human responses must cite the released packet's content hash.

Released packet content SHA-256:
`aa76483a28913628d2a14bfeb78490fb2fda457c0faf39fdc405f4d48f06ed12`.

Protocol file SHA-256:
`5b2f3d867b458d847e57b9ba567c8612792f81193254db6cf61e5a43501e4e62`,
registered `2026-10-11T13:02:54.685509+00:00`, before any context-calibration
fitting. No corpus model or context cutoff has been fitted in this phase.

## Review Set

The packet has **40 distinct review regions**: 20 seeded random representative
ML windows within supported macro contexts, 19 seeded random boundaries, and
the complete short capture 001. Windows/boundaries are chosen without scores,
flags, offsets, or detector-outcome ranking. The seed is 42; per-selector
deterministic keys and a shuffled review order are recorded.

| Capture | Regions | Selection Strata |
| --- | ---: | --- |
| 001 | 1 | Entire short capture; all 19 reported speed/context discrepancy samples |
| 014 | 5 | Two warming windows, stationary window, thermal and operating boundaries |
| 006 | 4 | Stationary and moving windows, operating and thermal boundaries |
| 015 | 4 | Stationary and moving windows, two operating boundaries |
| 023 | 6 | Warming, steady, moving and stable windows; both boundary axes |
| 002 | 4 | Warming and stable windows; two thermal boundaries |
| 004 | 4 | Stationary and moving windows; two boundaries adjoining stationary episodes |
| 005 | 4 | Stable and moving windows; both boundary axes |
| 007 | 4 | Moving and steady windows; two operating boundaries |
| 018 | 4 | Steady window; thermal and two operating boundaries |

Capture 001's discrepancy indices are **13-29 and 49-50**, all present in its
117-sample, 0-28,999 ms review region. The discrepancy samples are marked on
the speed trace. This discloses an evidence inconsistency, not a predetermined
review decision. Previously manually corrected labels are preserved too.

Boundary regions show ten seconds of neighboring data on either side,
clipped/snapped outward to actual sample timestamps at source endpoints.
Representative windows include the same evidence margin. The whole 001
capture is retained. Every displayed sample is included without decimation;
there are 4,251 displayed sample records, including overlaps between regions.
Their within-capture time union is 997.246 seconds. **Neither quantity is
human review time or an independent-sample count.**

### Reviewer Materials

Start with the score-hidden worksheet:

`data/evaluation/h10_2a/review_packet.md`

For every region it links a self-contained SVG with measured speed, RPM, TPS
voltage, and ECT traces, focal window/boundary lines, and all four original
context tracks. Exact interval IDs, proposed labels, thermal state, and original
boundary positions are listed in the worksheet/JSON. Narrow intervals without
room for a label in the plot are not deleted: their exact spans/labels remain
in the interval table, SVG titles, and JSON. TPS raw is also supplied numerically.
Original throttle/event markers and macro child references remain in JSON.

Each JSON region contains source SHA, exact start/end timestamps and sample
indices, every measured point, signal summaries, proposed fine/macro context,
original review provenance, boundaries, selection reason, and pending review
fields. Focal ML windows use their conservative assignments; boundary regions
are assessed across their evidence span, not mislabeled as homogeneous
zero-duration point windows.

Timestamps are **original elapsed milliseconds**, not invented wall-clock UTC.
Speed comes from validated Table 17 byte 19, rider-corroborated but not GPS
calibrated. ECT remains provisional high-confidence decoding. Labels such as
warming describe a local thermal-rule state and do not necessarily mean a cold
start; the selected 023 warming region has ECT 94-97 C. Session descriptions
are not precise time-specific ground truth, and no new trip metadata is added.

### Human Responses and Blinding

`review_responses.template.json` is separate from both the immutable approved
annotations and the released packet. All action, reviewer, date, actual review
seconds, corrections, and notes are initially null/pending.

- **Confirm:** context and boundaries are acceptable for the specified region.
- **Correct:** identify affected track/interval IDs, corrected context and/or
  original/new elapsed-second boundaries, and rationale.
- **Reject:** specify affected tracks/intervals and why the proposal is unsupported.
- **Uncertain:** record ambiguity or missing evidence rather than guessing.

Review fine operating, fine thermal, macro operating, and macro thermal
separately; a shared boundary does not establish the same judgment on both
axes. Record actual human review time, identity and date. Null time means
unrecorded, not zero seconds. No response is automatically applied to approved
annotations, treated as normal mechanical truth, or used to fit a threshold.

The worksheet/region evidence contains **no IF scores, flags, predictions, or
offsets**. The collector should use it before consulting anomaly results.
Full enrollment blinding is impossible: capture priorities were informed by
H10.1 and the collector may know prior findings. Selection reasons in the packet
are neutral context-coverage descriptions. This is score-hidden targeted
validation, not a new independently enrolled confirmatory study.

## Future Calibration Protocol

`calibration_protocol.json` is the preregistered design artifact. It includes
all 18 exact outer folds, source identities, contract, gates, metrics, fallback,
and stop rules. Its execution flag is **false**. A later explicitly approved
revision must pin genuine responses and any correction-overlay hash before
any model or cutoff fitting; approved-v2 itself must remain untouched.

### Partitions and Shared Control

Each outer fold has **12 fitting groups, 5 calibration groups, and 1 final test
group**, disjoint by physical group and source hash. IDs are sorted, shuffled
once with `random.Random(42)`, then cyclically assigned: the next five groups
after the held-out group calibrate and the remaining twelve fit. Every group
tests once, calibrates five times, and fits twelve times. The explicit fold
inventory is authoritative; there is no context/outcome-driven reassignment.

Default RobustScaler and the existing IF are fitted only on fitting groups.
The native-v2 ordered 81-feature contract, 50/10 window geometry, quality
policy, 200 trees, auto max-samples, 0.03 contamination, and seed 42 are unchanged.
No balancing, architecture change, hyperparameter search, or healthy-data
assumption is introduced. Fitting-only contamination learns the control offset.

One fresh scaler/IF is shared byte-identically across A/B/C per fold:

| Arm | Rule |
| --- | --- |
| A | Existing IF architecture and fitting-only offset, unchanged flag rule |
| B | Same IF, macro thermal calibration only: warming/stable |
| C | Same IF, macro operating calibration only: stationary/moving episode/steady |

H10.1 fitted artifacts **cannot be reused**: their 17 training groups include
the newly separated calibration groups. Final test telemetry is used only for
fixed extraction/inference after fitting/calibration artifacts are sealed. No
test scores/outcomes choose parameters, cutoffs, support rules, or partitions.
Every arm uses identical test windows and raw IF scores. There is no joint B+C
model; fine labels remain separate descriptive validation strata.

### Cutoff and Fallback

Calibration uses only homogeneous known-context windows in the calibration
partition. Within each capture/context, greedily retain windows in sample-index
order whose start exceeds the prior retained end. This reduces overlapping
sample reuse; it does not make rides or retained windows independent, delete
short annotation states, or remove any primary test windows.

Support requires **at least 3 calibration groups**, each contributing at least
**3 nonoverlapping windows**, and at least **30 retained windows** in total.
Captures below the per-context minimum do not contribute to that cutoff.
These are fixed exploratory feasibility gates, not a tail-estimation precision
guarantee: a 3% empirical tail with small capture support can be very coarse.

Each qualified calibration capture gets total weight 1/G, with weight 1/(G*n)
per retained window. At the unchanged alpha 0.03, select the smallest observed
score with cumulative weight at least alpha, combining tied-score weight.
Candidate flags use `raw_score < q_context`. Optional weighted-ECDF tail ranks
are not calibrated fault probabilities or measured false-positive rates.
Calibration captures are unlabeled, not independently confirmed healthy.

Unknown/abstained, boundary-ambiguous, unresolved, missing/unapproved context,
and insufficient support cause **calibration abstention plus exact unchanged
baseline fallback**. The IF window is not dropped. Preserve raw score, control
offset/prediction, candidate decision, support and fallback reason. No majority
voting across boundaries and no cross-axis or fine-label substitution is allowed.

A supported candidate must additionally retain every A-flagged window below
the **fitting-only training p01 raw score**, even if its context cutoff would
suppress it. This uses an existing statistical-extremity reference, not a new
production cutoff or proof of physical severity. It cannot guarantee retention
of all genuinely severe faults in the absence of physical truth. Raw evidence,
baseline results and independent severity evidence must always remain inspectable;
Contextual Battery/RCA outputs are neither changed nor substituted.

### Support Preflight

These counts use approved-base context availability in calibration partitions
only, **not IF scores and not test outcomes**. No numerical context cutoff was
computed. Actual reviewed-overlay support must be audited again without
altering the pinned fold assignment.

| Candidate Context | Folds Qualifying out of 18 |
| --- | ---: |
| B: Warming | 4 |
| B: Stable | 16 |
| C: Stationary running | 9 |
| C: Moving episode | 18 |
| C: Steady moving | 7 |

Warming calibration is particularly under-supported. The four qualifying test
folds are 005/016/019/020; many interesting warming captures would still use A
fallback. This design cannot promise calibrated coverage for all 18 captures.
Report this limitation rather than lowering gates after inspecting test effects.
Additional genuinely separate cold/warming captures may be more useful than
forcing a complex conditioned estimator on this corpus.

Targeted review does not certify all uninspected contexts. Approved-base labels
remain provisional research strata, with their bulk-endorsement provenance.
Any collector-approved research use after spot checking must retain that
limitation. Corrected labels stay in a separately hashed overlay; windows touching
Reject/Uncertain regions or unresolved 001 discrepancies must fall back to A.
The present protocol has null response/overlay hashes and grants no such approval.

## Metrics and Decision Safeguards

The protocol defines metrics before any calibration run:

- Capture-level raw-score/tail-rank quantiles; raw-score/model/window invariance
  across arms; separate known, ambiguous, unknown and fallback strata.
- Per-capture flag frequencies and paired A-to-B/A-to-C changes, equal-capture
  primary; duration weighting and pooled windows secondary descriptive summaries.
- Existing H2 consecutive-flag runs per full capture-hour, run-duration
  distributions and interval-union flagged time; no overlapping-duration sums.
- Event changes using bipartite shared-flagged-window matching: unchanged,
  changed one-to-one, split, merged, disappeared, and new components. Retain
  exact IDs rather than tuning an overlap/IoU cutoff.
- Unsupported windows/time by context, fold and reason, including explicit
  calibration abstention. Unsupported does not mean normal or zero risk.
- One/two-capture deletion aggregation sensitivity, especially 006/015/023/014;
  short-capture and 001-discrepancy views, without outcome-driven threshold refits.
- A separately declared fitting sensitivity omitting each influential capture
  only from its original fit/calibration role, with no replacement/reassignment,
  shared A/B/C artifacts and support fallback. It is not a parameter search.
- Descriptive capture-bootstrap uncertainty, 10,000 draws/seed 42; no interval
  below three supported groups. Shared folds and temporal dependence remain;
  fixed-summary resampling does not quantify model-fitting variance.
- Ten fixed-order inference repeats after warm-up: p50/p95 wall latency, peak
  RSS, lookup overhead separate from IF inference, calibration cost/artifact
  size, and explicit hardware/software. No deployment-limit changes.
- Every suppressed baseline window/run must retain measured evidence, raw
  score and extremity status. Zero lost p01 sentinel windows is mandatory;
  potential masking of other high-severity evidence remains unresolved without
  independent physical severity truth.

Abort on partition/provenance/hash leakage, differing A/B/C raw scores or test
identities, altered fallback, sentinel loss, or invalid context mappings.
**Reduced alert counts alone cannot qualify a candidate as better.** Capture
heterogeneity, coverage, overhead, signal evidence and masking risk require
explicit human assessment. There is no automatic selection/promotion rule,
fault-sensitivity claim, true false-positive-rate estimate, or inference across
motorcycle models. All future fitted artifacts must remain disposable research
storage; no production writes belong to this design.

## Engineering Verification

The packet generator has process-local guards that raise if IF or RobustScaler
fitting is attempted. It neither calls inference nor learns context thresholds.
Tests cover deterministic score-independent sampling, discrepancy completeness,
exact native signal/timestamp parity, no decimation, pending/null review fields,
detector-output exclusion, conservative boundary assignments, plot provenance,
sample-based calibration thinning, balanced/disjoint partitions, support
abstention, and fitting prohibition.

Browser checks render all 40 self-contained plots and verify all 160 measured
traces by canvas pixel checks, text framing, desktop/mobile screenshots and no
JavaScript errors. These are **engineering rendering checks**, not human
context accuracy, review usability timing, or acceptance of any label.

Final regression/protected-input results and file hashes are recorded in
`completion_validation.json`. Existing H10.0/H10.1 files are hash-checked,
not regenerated. All 40 review actions/times remain pending throughout.

Final full suite: **389 passed, 0 failed, 0 skipped**, including 16 new packet/
design tests. Pytest time was 82.75 seconds. All **859 pinned files** remained
unchanged after testing; native production IF/scaler/metadata hashes and all
approved/H10 reference hashes match. External Starlette/httpx and joblib/NumPy
deprecation warnings remain; no test failure or skip was suppressed.

Local packet generation took 23.37 seconds, peak RSS 482.21 MiB; full test
process wall time was 84.37 seconds, peak RSS 448.07 MiB. These include research
verification work and are not production benchmarks. No resource/deployment
limits or production dependencies were changed. The browser check uses the
existing local Playwright tooling; no plotting dependency was added.

```bash
.venv/bin/python -m app.evaluation.context_validation_plan build
.venv/bin/python -m app.evaluation.context_validation_plan verify
.venv/bin/python -m pytest --junitxml=data/evaluation/h10_2a/regression_tests.junit.xml
```

`build` refuses overwrite of an existing packet. The completed packet can be
verified with the second command. There is deliberately **no calibration-fit
command** in H10.2A. The collector's next action is to review the worksheet and
submit genuine decisions/corrections/time referencing its response template
and packet hash, before separately approving any future fitting study.
