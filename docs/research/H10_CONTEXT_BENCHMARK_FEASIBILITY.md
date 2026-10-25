# H10.0 Context-Aware Model Benchmark Readiness

## Decision

**`descriptive_analysis_only`**. The approved export is usable for reproducible
context-stratified research, but the existing artifacts cannot provide an unseen
benchmark on this corpus. Seven captures overlap native IF training, eight
overlap candidate IF fitting and Contextual Battery calibration, and all 18
appeared in prior IF model comparison. Historical candidate train/holdout slices
share physical parent groups 016 and 018.

No corpus or production model was trained, threshold tuned, artifact promoted, annotation
changed, dataset frozen, or DriveSafe behavior modified. Human boundary-quality
and timed review checks remain pending. Operator bulk endorsement is neither
independently verified boundaries nor normal/fault ground truth.

## Inputs and Verification

Only `data/context_annotation/datasets/ecuread-filtered-raw-18-v1-approved-v2/`
supplies context labels. Its verified content SHA-256 is:

```text
5fd672ff45b69681e310efff7661cc8313989cfc10191047dc476e8161e156c1
```

All 59 referenced files and all 18 original source hashes validate. The audit
independently checks 100,688 canonical sample identities and all 9,989 complete
50-sample/stride-10 windows against RAW decoding and the production feature path.
Sequence, timestamps, elapsed duration, sample-to-interval/event references,
window bounds, every axis assignment and macro-to-micro time coverage validate.
No source trimming, resampling, cross-capture concatenation or majority voting
is used. A proposal may legitimately link to multiple adjacent macro episodes;
its linked time union, not a one-child/one-parent assumption, is checked.

The native decoder is `honda_keihin_71_17:1.0.0`, telemetry is
`canonical-telemetry-v2`, and features are `ecu-window-features-v1`: 81 features
from ordered RPM, TPS voltage, TPS raw, battery voltage, IAT and ECT signals.
Taxonomy `context-taxonomy-v2`, rules `ctx2-rules-v1`, config hash
`fd67888efcb842876003fd3140c48c88af78fab2a0f84df985ab18f1e41a2a6b`,
and macro rule `ctx24-macro-v1` remain unchanged.

There are 18 **operator-attested** physical capture groups, not 18
machine-verified independent captures. Machine-verified independence remains
null. The explicit `operator_attested_capture_groups_v1` policy is required;
strict machine-verified policy is unchanged. Only 016/018/023 have confirmed
out-of-city continuous-ride metadata. Speed is RAW byte 19, rider-corroborated,
not GPS calibrated; ECT/IAT remain provisional high-confidence signals.

The export remains non-frozen. Its existing snapshot-v1 gate remains blocked
for missing named individual human review provenance. H10 does not rerun or
weaken that gate.

## Leakage Evidence

The per-capture matrix, source/model/scaler/reference SHA values, offsets,
historical artifact inventory and unresolved provenance are in
`data/evaluation/h10/h10_source_leakage_audit.json`.

The retired 57-session archive is read in place, never restored. Archive and
per-file hashes, historical source-tree hashes, full six-signal sequences plus
four reserved bytes and every relative timestamp are checked for all 43 linked
records. Embedded replay RAW must additionally match every native frame byte.
Historical replay canonical values are not silently substituted for native
values: the 018 replay has incompatible battery/ECT values; 024's historical
decoder differences are likewise retained in the machine-readable links.

| Capture | Native IF Fit/Scaler/Offset | Candidate Fit/Scaler/Reference | Historical Holdout Shares Training Group |
| --- | --- | --- | --- |
| 001 | Yes | No observed declared-input overlap | No |
| 002 | Yes | Yes | No |
| 003 | Yes | Yes | No |
| 004 | Yes | No observed declared-input overlap | No |
| 005 | Yes | Yes | No |
| 006 | Yes | No observed declared-input overlap | No |
| 007 | Yes | Yes | No |
| 014 | No observed declared-input overlap | No observed declared-input overlap | No |
| 015 | No observed declared-input overlap | No observed declared-input overlap | No |
| 016 | No observed declared-input overlap | Yes | **Yes** |
| 017 | No observed declared-input overlap | No observed declared-input overlap | No |
| 018 | No observed declared-input overlap | Yes | **Yes** |
| 019 | No observed declared-input overlap | No observed declared-input overlap | No |
| 020 | No observed declared-input overlap | No observed declared-input overlap | No |
| 021 | No observed declared-input overlap | No observed declared-input overlap | No |
| 022 | No observed declared-input overlap | No observed declared-input overlap | No |
| 023 | No observed declared-input overlap | Yes | No |
| 024 | No observed declared-input overlap | Yes | No |

Here "Yes" means declared training provenance corroborated by complete source
sequences and scaler reconstruction, not independent authentication of the
historical fitting transaction. Original IF metadata did not pin training
source hashes; that limitation remains explicitly unresolved.

The native nine-source training inventory reconstructs 3,874 windows and its
saved RobustScaler center/scale. Candidate training uses 12 historical IDs but
only eight current parent groups; 4,155 reconstructed windows match its saved
scaler. Contextual Battery uses those same 12 IDs: observed context counts and
all modeled battery reference medians/scales reconstruct without fitting.
Identical validated windows reuse production features; other inputs use the
existing feature extractor. Matching statistics are corroboration, not proof
that no unrecorded training data ever existed.

The older v0.2 IF declares the same nine RAW sources, but has an incompatible
seven-signal feature contract and unresolved original fitting bytes. The root
legacy artifact's serial fixture reconstructs its 75 windows and scaler, but
historical source identity is still not authenticated. Neither is a native-v2
fallback. Exact 16-frame sequence overlap checks supplement full-source checks;
absence of such overlap does not establish physical independence or exclude
shorter/modified derivations.

All 18 have linked rows in `data/calibration/human_label_model_comparison.csv`.
`app/audit/human_normal_retraining.py` consumed this table for model
recommendation and threshold-policy exploration. Distinguish learned IF offset
exposure from these later policy comparisons: H10 retains the production
decision cutoff and does not assert that a tested policy was deployed.
H2-H9 artifact references and H2/H3 recorded sample-hash comparisons are
inventoried separately: all **114** H2/H3 historical sample hashes match the
archive. H3/H3.1 excluded reference IDs and duplicate whole-file sample hashes,
but that did not exclude other slices of parent captures 016/018.
Catalog/exclusion-list presence is not mislabeled as
model fitting; historical selection exposure is independently established by
the comparison table. No current capture is called unseen.

## Context Eligibility

All counts are over the same original 9,989 windows. Categories across axes can
overlap; unknown inside a boundary-crossing window is also reported separately.

| Measure | Fine/Micro | Macro |
| --- | ---: | ---: |
| Homogeneous known operating | 1,671 | 6,806 |
| Homogeneous known thermal | 1,511 | 7,875 |
| Homogeneous known on both axes | 1,511 | 5,420 |
| Operating boundary ambiguous | 8,318 | 3,183 |
| Thermal boundary ambiguous | 8,318 | 1,064 |
| Either axis boundary ambiguous | 8,318 | 3,871 |
| Homogeneous explicit thermal unknown/abstained | 160 | 1,050 |
| Touches unknown on either axis, including ambiguous | 2,337 | 2,337 |
| Missing mapping or contradictory mapping | 0 | 0 |

There are no homogeneous operating-unknown windows: the unknown operating
intervals are too short for this window geometry, but are retained inside
ambiguous windows. Fine thermal conservatively inherits joint interval
boundaries, including unchanged thermal labels. Macro thermal is independently
coalesced. Thus the increased eligible macro coverage is not evidence deletion.

The raw evidence check identifies **19 unique samples in capture 001** with
stationary labels but speed above the unchanged 1 km/h stationary rule.
They occur in two retained fine intervals and one macro episode, affect windows
0-5, and include one both-known macro window. These are disclosed review
candidates, not an adjudication of the human labels or speed calibration.
Annotations are preserved. Excluding affected windows leaves **5,419** macro
both-known windows; the fine both-known count remains **1,511**. There are no
missing native model signals or missing RAW speed samples.

| Macro Stratum | Eligible Windows | Operator-Attested Groups |
| --- | ---: | ---: |
| Moving episode | 5,502 | 15 |
| Steady moving | 647 | 10 |
| Stationary running | 657 | 15 |
| Stable thermal | 7,393 | 14 |
| Warming | 482 | 11 |
| Moving episode + stable | 4,276 | 14 |
| Moving episode + warming | 126 | 7 |
| Steady moving + stable | 612 | 10 |
| Steady moving + warming | **1** | **1** |
| Stationary + stable | 168 | 8 |
| Stationary + warming | 237 | 8 |

These are eligibility counts, not independent sample sizes. The single
steady/warming window cannot support a conditioned estimator. No engine-off
episode is long enough to yield a homogeneous operating window. The largest
capture contributes 46.0% of stationary/warming windows. Even supported contexts
need fold-specific capture/time checks, not just a pooled window threshold.

Median window duration is 12.250 s; range 12.247-14.243 s. Full per-label segment
duration distributions, label-switch rates, interval-boundary rates and
eligible time unions per capture are in `h10_context_window_eligibility.json`.
No minimum-duration deletion or context flattening is performed.
Fine variable/accelerating/decelerating median interval durations are
4.750/5.999/5.750 s; macro moving episodes have median 25.001 s.
Stationary medians are 14.250 s fine versus 22.751 s macro. Median operating
label changes per capture-hour decrease from 436.425 to 140.417; thermal label
changes remain identical. This measures representation granularity, not human
review time or boundary quality.

## Existing Baseline: Descriptive Only

Pinned model: `data/models/honda_keihin_71_17_v2/`, version
`iforest-baseline-20260920T091709Z`. All 9,989 windows scored. There are **719
flagged windows (7.198%)**, not 719 faults or false alarms. Native IF score
samples are lower when more unusual; the existing prediction/decision threshold
is unchanged.

| Homogeneous Stratum | Windows | Flagged | Flag Frequency |
| --- | ---: | ---: | ---: |
| Macro stationary running | 657 | 138 | 21.00% |
| Macro moving episode | 5,502 | 265 | 4.82% |
| Macro steady moving | 647 | 83 | 12.83% |
| Fine variable motion | 461 | 19 | 4.12% |
| Fine steady motion | 629 | 79 | 12.56% |
| Macro warming | 482 | 152 | 31.54% |
| Macro thermally stable | 7,393 | 368 | 4.98% |

Fine and macro results are separate representations, not additive cohorts.
Condition contrasts may mix captures and training exposure; they are not causal
effects, recognition accuracy or held-out detector performance. All four tracks
also retain ambiguous/abstained strata, score quantiles and per-capture results.

| Capture | Windows | Flagged Windows | Consecutive-Window Runs |
| --- | ---: | ---: | ---: |
| 001 | 7 | 7 | 1 |
| 002 | 574 | 15 | 11 |
| 003 | 120 | 6 | 2 |
| 004 | 297 | 1 | 1 |
| 005 | 781 | 8 | 7 |
| 006 | 88 | 9 | 4 |
| 007 | 634 | 6 | 5 |
| 014 | 116 | 114 | 1 |
| 015 | 49 | 8 | 3 |
| 016 | 1,792 | 30 | 16 |
| 017 | 345 | 10 | 8 |
| 018 | 1,793 | 126 | 34 |
| 019 | 215 | 8 | 3 |
| 020 | 4 | 4 | 1 |
| 021 | 352 | 7 | 3 |
| 022 | 298 | 29 | 11 |
| 023 | 1,749 | 303 | 63 |
| 024 | 775 | 28 | 9 |

Existing H2 `anomaly_events` defines the **183** consecutive flagged-window
runs. These are neither physical-fault events nor necessarily production
notifications. Across 25,189.348 s (6.997 h), burden is **26.154 runs/capture-hour**.
Flagged-window time union is **3,310.933 s**, avoiding overlapping-window
double-counting. Per-capture results separately provide labeled-moving-time
denominators, intersecting runs/hour and flagged moving-time unions; unknown
time is not assumed riding.
Across 22,440.984 s of labeled moving time, 164 runs intersect motion and the
flagged moving-time union is 2,605.884 s. Moving-time normalization retains
stationary/unknown time separately rather than calling the entire session riding.

The three operator-attested long rides contribute 5,334 windows and 459 flags
(8.61%); the other 15 contribute 4,655 windows and 260 flags (5.59%). These are
not comparable independent-window trials. Equal-capture mean flag frequency is
8.68% for the three long rides versus 23.78% for the other 15; medians are 7.03%
and 3.72%. Very short captures 001/020 have only seven/four windows, both fully
flagged. Window weighting and equal-capture weighting answer different questions.

Contextual Battery completes for all captures: **9,880 applicable windows**,
**109 explicit skips**, all `context_not_modeled`. Its RPM/TPS load contexts are
not CTX2 motion or trip labels. Reference calibration overlaps eight parent
captures; applicability is reported, not independent detector accuracy.

## Feasibility and Smallest Next Experiment

| Approach | Recommendation | Cost and Interpretation |
| --- | --- | --- |
| A: Native IF | Existing artifact descriptive only; refit the SAME algorithm for a future fair comparison | 200 trees, existing 81-feature contract; unusual features are not causes |
| B: IF plus context stratification | **Start here**; optional later train/calibration-only conditional cutoffs | Same IF plus context lookup; interpretable conditional burden |
| C: Context-conditioned IF | Later, only with adequate train and test capture diversity per fold | Multiple estimators, sparse-cell variance; abstain for unsupported contexts |
| D: PCA residual | Defer until a specific residual/sensitivity question justifies it | Simple linear projection, but another fitted family does not supply missing fault labels |

No hypothetical accuracy ranking is made. Proposed next experiment, **not
executed by H10**:

1. Independently review boundaries, prioritizing 002/004/005/007/018, blind to
   IF flags; adjudicate capture 001's measured discrepancy. Record actual review
   time rather than estimating it. Context labels still do not become normal/fault labels.
2. Pre-register 18 leave-one-capture-group-out outer folds. Whole operator-
   attested physical groups are indivisible; aliases/splits/replays/derivatives
   inherit their parent. Exclude unresolved/synthetic records. Publish hashes
   and group manifests before fitting.
3. Refit the **same** baseline IF and scaler using only the other groups, in
   temporary research artifacts. Start with A and B stratification; no new
   estimator is necessary for B. Use identical outer folds for any later C/D.
   Without independently verified normal-only data, call training unlabeled,
   not confirmed normal.
4. Any threshold/component/context-reference calibration uses only explicitly
   designated training/calibration groups inside the outer fold. Never choose
   settings from the outer test. A proposed research support gate is at least
   three training groups and one eligible test group per conditioned context;
   otherwise report unsupported. This is not a changed production threshold.
5. Report paired per-capture distributions and time-normalized run burden;
   retain unknown/crossing/discrepant strata and unsupported-fold denominators.
   Bootstrap capture groups, not overlapping windows. Folds themselves share
   training data. Pre-register contrasts and reserve genuinely new captures
   for confirmatory claims because this corpus already influenced selection.

Only 18 declared groups exist, and adjacent windows share 40/50 samples (80%).
Neither pooled window count nor resampling overlapping windows creates
independent evidence. Macro coverage supports a small exploratory operating-
context study, not every joint operating/thermal conditioned estimator.

## Claims and Human Gates

- Operational context recognition needs independent boundary/context review;
  bulk endorsement is not a measured recognition-accuracy test.
- Anomaly score behavior can be described now, with leakage disclosed.
- Alarm burden under normal-looking riding is not a false-positive rate
  without independently verified normal conditions.
- Sensitivity to verified physical faults is **not evaluable**: the approved
  corpus supplies no verified fault labels. IF predictions, RCA observations
  and candidate labels must not become fault ground truth.
- CTX2 and IF share RPM/TPS/ECT-related signals; stratification/recognition
  claims can be circular. Speed adds evidence but is not GPS calibrated.
- Synthetic perturbations, if later used, are sensitivity tests only, not
  demonstrated real-world diagnostic recall.

Engineering readiness and human/diagnostic validation readiness are separate.
Timed human review and independent manual boundary checks remain **pending**.
No human result is fabricated, and no manual gate is marked passed.

## Reproduction and Artifacts

```bash
.venv/bin/python -m app.evaluation.context_benchmark_readiness \
  --output /tmp/ecuread-h10-reproduction
.venv/bin/python -m pytest tests/test_context_benchmark_readiness.py
```

The output directory must not exist; existing results are never overwritten.
Only new H10 artifacts are written. The runner verifies protected input hashes
before publication. The independent repeat disabled `IsolationForest.fit`,
`RobustScaler.fit` and `RobustScaler.fit_transform` with raising guards. It
completed successfully and reproduced all four JSON files byte-for-byte.

Machine-readable deliverables in `data/evaluation/h10/`:

- `h10_source_leakage_audit.json`: export verification, protected SHA inventory,
  per-capture leakage matrix, archive/lineage checks and historical exposure.
- `h10_context_window_eligibility.json`: separate fine/macro eligibility,
  per-label/group counts, durations, switching and measured discrepancies.
- `h10_baseline_context_audit.json`: every original window score/flag and
  mapping identity, context/capture distributions, event/time burden and
  Contextual Battery applicability.
- `h10_benchmark_design.json`: candidate feasibility, group-aware next protocol,
  claim limitations and the descriptive-only verdict.

## Completed Engineering Verification

Verified on 2026-10-11 using Python 3.14.4, scikit-learn 1.9.0, NumPy 2.5.1 and
pandas 2.3.3. Both full audit runs preserved all **690** protected existing files;
the post-regression check also compares that same inventory. This includes RAW,
model artifacts, live annotations, the approved export, production code,
snapshot policy and historical research inputs/reports.

`pytest` completed with **358 passed, 0 failed, 0 skipped** in 59.15 s, including
11 H10 tests. JUnit evidence is `data/evaluation/h10/h10_tests.junit.xml`.
Warnings are existing Starlette/httpx and joblib/NumPy deprecations. Fixture-based
regression tests may fit disposable test models; the H10 corpus audit itself
performs no fitting, as independently guarded above.

All four deterministic JSON SHA-256 values match the guarded reproduction:

| Artifact | SHA-256 |
| --- | --- |
| Source leakage | `2f14925cadaa254e2ed5fcacdc0d9713372a1a97c7c26f0356836e1e4414ce5e` |
| Context eligibility | `343876328228ca265a516c65838343f3916e1782719ac72760c0f2423613ca98` |
| Baseline audit | `b317d24e72e264f3fa70eccea33e70fc461fab6870c930151db40407203e96fe` |
| Benchmark design | `ab3874d9e4802828e24c9cc6b3b15cbdb962f39add007d815b136e54ceecaea9` |

Linux `/usr/bin/time -v` measured 169.78 s and 975,628 KiB peak RSS (952.76 MiB)
for the full audit; the guarded repeat took 169.95 s and 977,452 KiB (954.54 MiB).
This workload includes all 18 captures, the 57-entry historical archive,
training/reference reconstruction, mapping checks, hashes and inference. These
are local research observations, not production upload/latency benchmarks or
capacity guarantees. No deployment/resource limit was changed.

**Engineering verification: complete. Human boundary/timed validation: pending.
Held-out detector/fault-performance claims: not supported. Final verdict:
`descriptive_analysis_only`.**
