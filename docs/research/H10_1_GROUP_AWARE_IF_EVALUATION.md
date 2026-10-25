# H10.1 Capture-Group IF Refit and Context-Stratified Evaluation

## Scope and Status

**Verdict: `context_experiment_justified`.** This justifies a later, carefully
preregistered context-calibration experiment after boundary review, not a
conditioned-model deployment or a claim of diagnostic improvement. Both
preregistered macro contrasts pass the exploratory decision rule, with
substantial heterogeneity and sparse-context limitations described below.

This is **EXPLORATORY research**, not independent confirmatory validation. All
18 captures have influenced previous model comparison or selection. Holding
out groups here removes their rows from these particular fits; it cannot undo
prior selection exposure. Context labels are analysis strata, not fault truth.

Production IF models, thresholds, Model Harness routes, DriveSafe, RAW sources,
approved annotations, and historical research results must remain unchanged.
No PCA, conditioned IF, context cutoff fitting, model promotion, or dataset
freeze belongs to this experiment. Genuine manual boundary adjudication and
timed human review remain **pending**.

## Authoritative Input and Preregistration

- Campaign: `ecuread-filtered-raw-18-v1`.
- Input: `data/context_annotation/datasets/ecuread-filtered-raw-18-v1-approved-v2`.
- Verified content SHA-256:
  `5fd672ff45b69681e310efff7661cc8313989cfc10191047dc476e8161e156c1`.
- Inventory: 18 originals, 100,688 canonical samples, 9,989 windows,
  25,189.348 seconds (6.997 hours). No aliases, historical splits, synthetic
  records, or augmentation are training inputs.
- Groups: `operator-capture:<source SHA-256>`, under the explicit
  `operator_attested_capture_groups_v1` evaluation policy. Independence is
  operator-attested for 18 physical runs, **not machine verified**. The original
  strict machine-verified policy remains unchanged.
- Protocol registered at `2026-10-11T12:04:52.108141+00:00`, before corpus fitting.
- Protocol SHA-256:
  `213fb4f4d007dc9181ec6a0c13d707d4d8138c480b15292554c80b866d9c7a4f`.
- Fold-manifest SHA-256:
  `8c74e64b47beb699f4d2ed5aae3eca9a092fec5989a19f47f2b5cf6fc1815341`.

`data/evaluation/h10_1/preregistration.json` pins the protocol, fold manifest,
and protected-input inventory. Subsequent result files do not alter these
registration hashes. The prior H10.0 study remains unchanged.

## Fitting and Scoring Contract

There are 18 leave-one-physical-capture-group-out outer folds. Each fit uses
all eligible windows from the other 17 groups, ordered by source ID/window.
Session ID, physical group, and source hash are disjoint between train and test.
Every eligible held-out window is scored exactly once.

The runner calls the existing production `train_isolation_forest`, feature
extractor, native importer, and IF inference path, without editing them:

- Decoder `honda_keihin_71_17:1.0.0`; `canonical-telemetry-v2`.
- Feature schema `ecu-window-features-v1`: the identical ordered 81 features
  from RPM, TPS voltage/raw, battery voltage, IAT, and ECT. Speed is used for
  context evidence, **not added to the detector feature contract**.
- 50-sample windows, stride 10, existing quality policy. Window duration is
  approximately 12.25 seconds; adjacent windows overlap by 80% of samples.
- Default `RobustScaler`, center/IQR fitted on training rows only. No global
  scaling, resampling, balancing, or new imputation. Existing per-window
  feature preprocessing is unchanged.
- IF: 200 trees, `max_samples="auto"`, `max_features=1.0`, `bootstrap=False`,
  contamination 0.03, seed 42, `n_jobs=-1`. No hyperparameter search.
- Each fold learns its offset from training scores using the existing 3%
  contamination policy; held-out flags use unchanged `decision_function < 0`.
  Relearning a research fold offset is not a production threshold change.
- Training captures are **unlabeled**, not independently confirmed healthy.
  Long captures contribute more training windows, as in the original baseline.

The protocol records Python/scikit-learn/NumPy/pandas/joblib versions. All
fitted models, scalers, and the feature cache live exclusively in disposable
`/tmp/ecuread-h10-1-*` directories. Repository outputs contain results,
metadata, and artifact hashes, not deployable fitted artifacts.

## Context and Statistical Policy

The unchanged approved-v2 mapper requires a whole window to occupy one
interval. Windows crossing any retained boundary, even between identically
named intervals, are ambiguous; explicit unknown is abstention. There is no
majority voting, interpolation of labels, or replacement of human annotations.
Macro operating, macro thermal, fine operating, and fine thermal results are
separate. Fine thermal retains joint fine boundaries; macro thermal remains
independent. This explains different eligibility without deleting evidence.

The principal unit is the capture. Equal-capture summaries come first;
duration-weighted capture summaries and pooled-window descriptions are
secondary. Paired contrasts use only captures supporting both contexts.
Uncertainty uses 10,000 capture-bootstrap resamples, seed 42, with descriptive
2.5/97.5 percentiles; fewer than three supporting captures get no interval.
These are not confirmatory confidence claims: folds share training data,
windows overlap, and prior selection exposure remains. The bootstrap resamples
fixed held-out capture summaries, not model fits; it does not estimate training
variance or establish a wider population sampling frame. No independent-window
p-values, fault-sensitivity estimates, or cross-motorcycle generalization claims
are made.

Consecutive flagged-window runs use the existing H2 event helper and original
sample times. Non-stratum windows break stratum runs. Eligible/flagged time
uses interval unions, not sums of overlapping durations. Rates are provided
both per eligible-window hour and per labeled-context hour; overall labeled
time is full capture time. A run is not a diagnosed fault or necessarily a
production notification. Different context run counts must not be added as
if they were distinct physical events.

Preregistered sensitivity views change aggregation only, not fitting:

1. Exclude captures with fewer than the existing minimum of 10 windows:
   001 (7 windows) and 020 (4 windows). Both remain in the primary analysis.
2. Mask 001 windows 0-5 intersecting 19 retained speed/context discrepancies.
   No speed calibration, annotation correction, or manual adjudication is
   inferred from this sensitivity view.

Leave-one/two-capture deletion ranges assess dominance without parameter
tuning. A future-conditioning feasibility flag requires at least three
training groups and one homogeneous known held-out window; it is not a claim
that a conditioned estimator is adequately powered. Unknown and boundary
windows are retained and reported separately.

The preregistered exploratory decision requires a positive paired macro
stationary or warming flag contrast in at least three common-support groups,
positive direction after every single-capture deletion, and positive direction
in both sensitivities. Failure means insufficient evidence, not proof that
the baseline is adequate. Human review and uncertainty still constrain any
subsequent study.

## Results

All 18 folds completed. **9,989/9,989 windows were scored once**, with no
missing scores, capture splits, duplicate test identities, or source changes.
Training sets contain 8,196-9,985 windows from 17 groups each. Fold offsets
range from -0.558002734 to -0.541313090; every offset and scaler was verified
against its training-only matrix.

### Overall and Per-Capture Results

There are **412 flagged windows and 96 consecutive flagged-window runs**.
The principal equal-capture mean flag frequency is **20.39%**, with a broad
descriptive capture-bootstrap interval of **5.12-38.01%**. Duration weighting
gives **4.25%** and pooled windows give **4.12%**. Short captures and capture
014 explain much of this weighting difference; these are different estimands,
not interchangeable estimates.

The flagged-window time union is **1,814.927 s**, or 7.21% of the
25,169.841 s eligible-window union. There are **13.72 runs/full capture-hour**
(13.73/eligible-window hour). The equal-capture run-rate mean is much larger,
36.26/full capture-hour, partly because one run occupies most of a short
capture. Unknown or stationary time is not assumed to be riding time.

| Capture | Duration s | Test Windows | Flagged | Flag % | Runs | Runs/Capture-Hour |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 001 | 28.999 | 7 | 7 | 100.00 | 1 | 124.14 |
| 002 | 1446.503 | 574 | 16 | 2.79 | 11 | 27.38 |
| 003 | 312.499 | 120 | 6 | 5.00 | 2 | 23.04 |
| 004 | 755.001 | 297 | 1 | 0.34 | 1 | 4.77 |
| 005 | 1963.997 | 781 | 15 | 1.92 | 9 | 16.50 |
| 006 | 230.750 | 88 | 14 | 15.91 | 6 | 93.61 |
| 007 | 1598.002 | 634 | 5 | 0.79 | 4 | 9.01 |
| 014 | 300.500 | 116 | 115 | 99.14 | 2 | 23.96 |
| 015 | 132.501 | 49 | 11 | 22.45 | 3 | 81.51 |
| 016 | 4494.502 | 1792 | 7 | 0.39 | 5 | 4.01 |
| 017 | 873.500 | 345 | 5 | 1.45 | 2 | 8.24 |
| 018 | 4496.001 | 1793 | 13 | 0.73 | 4 | 3.20 |
| 019 | 548.670 | 215 | 7 | 3.26 | 2 | 13.12 |
| 020 | 21.501 | 4 | 4 | 100.00 | 1 | 167.43 |
| 021 | 891.250 | 352 | 3 | 0.85 | 2 | 8.08 |
| 022 | 755.001 | 298 | 2 | 0.67 | 1 | 4.77 |
| 023 | 4391.172 | 1749 | 168 | 9.61 | 33 | 27.05 |
| 024 | 1948.999 | 775 | 13 | 1.68 | 7 | 12.93 |

014 and 023 contribute 283/412 flags (68.69%). Their importance for absolute
burden must not be confused with the within-capture context contrasts below.
The old selection-exposed baseline had 719 flags/183 runs; this is not a
controlled accuracy comparison. Training composition and scaling changed,
and fewer flags do not establish fewer false alarms or better fault sensitivity.

### Macro Strata

Each axis partitions all windows separately. These rows cannot be summed
across operating and thermal axes. Raw score is `score_samples`: lower means
more unusual. Quantiles and decision-score distributions, including per-capture
distributions, are recorded in the machine-readable results.

| Stratum | Windows | Groups | Equal-Capture Flag % | Duration-Weighted % | Pooled % | Pooled Score Median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Stationary running | 657 | 15 | 24.94 | 18.33 | 21.46 | -0.48722 |
| Moving episode | 5502 | 15 | 3.12 | 2.61 | 2.51 | -0.41720 |
| Steady moving | 647 | 10 | 0.23 | 0.75 | 0.77 | -0.41664 |
| Operating ambiguous | 3183 | 18 | 20.81 | 4.17 | 4.02 | -0.42930 |
| Warming | 482 | 11 | 28.71 | 30.62 | 33.20 | -0.51731 |
| Thermally stable | 7393 | 14 | 1.68 | 1.51 | 1.50 | -0.41674 |
| Thermal unknown | 1050 | 14 | 12.12 | 7.09 | 6.76 | -0.44471 |
| Thermal ambiguous | 1064 | 15 | 9.47 | 6.42 | 6.58 | -0.44360 |

Macro stratum run counts/rates per eligible hour are: stationary 14/22.90,
moving episode 50/10.84, steady moving 3/4.22, warming 24/58.77, stable
45/8.29, and explicit thermal unknown 23/23.89. Boundary windows can separate
runs; these rates describe classified window sequences, not physical faults.
Per-labeled-context-hour rates and exact time denominators are also exported.

### Fine Strata

Fine labels are not pooled with macro labels. Their conservative eligibility
is much smaller, and several captures contribute only a handful of windows.

| Fine Stratum | Windows | Groups | Equal-Capture Flag % | Duration-Weighted % | Pooled % | Pooled Score Median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Stationary running | 489 | 14 | 18.55 | 19.81 | 26.38 | -0.49155 |
| Moving variable | 461 | 15 | 7.47 | 2.04 | 1.95 | -0.39779 |
| Steady moving | 629 | 10 | 0.14 | 0.45 | 0.48 | -0.41586 |
| Warming | 250 | 8 | 34.34 | 42.79 | 50.80 | -0.56044 |
| Thermally stable | 1261 | 14 | 1.19 | 1.47 | 1.59 | -0.41973 |
| Thermal unknown | 160 | 13 | 2.28 | 3.14 | 3.12 | -0.46514 |

Fine acceleration/deceleration have only 41/51 homogeneous windows across
nine groups each (9/2 flagged). Both fine axes have 8,318 ambiguous windows,
260 flagged; they remain scored rather than disappearing from denominators.

### Paired Context Contrasts and Dominance

Differences below are **percentage points**, left minus right, averaged equally
across captures with both contexts. Bootstrap ranges are descriptive, not
independent-window inference or multiplicity-adjusted confirmatory intervals.

| Contrast | Paired Groups | Mean Difference pp | Bootstrap Range pp | Positive/Negative/Tied Groups |
| --- | ---: | ---: | --- | --- |
| Macro stationary - moving episode | 13 | +10.04 | +0.50 to +21.49 | 6 / 6 / 1 |
| Macro steady - moving episode | 10 | -1.85 | -3.60 to -0.67 | 0 / 8 / 2 |
| Macro warming - stable | 8 | +9.86 | +1.98 to +19.89 | 5 / 2 / 1 |
| Fine stationary - moving variable | 13 | +3.74 | -3.79 to +13.22 | 5 / 2 / 6 |
| Fine steady - moving variable | 10 | -1.07 | -3.20 to 0.00 | 0 / 1 / 9 |
| Fine warming - stable | 6 | +14.58 | +0.50 to +28.88 | 3 / 1 / 2 |

Stationary macro differences are heterogeneous, not universally positive.
006 and 015 contribute **80.48% of the summed signed paired effect**. Removing
any one capture leaves +6.29 to +11.45 pp; removing any two leaves +2.31 to
+12.66 pp. A positive residual mean does not make the effect evenly distributed.
All 13 paired captures nevertheless have lower stationary median raw scores
than moving-episode scores; the mean paired median-score difference is -0.06985.

Warming macro effects are also uneven: 006 and 023 contribute **73.92% of the
summed signed paired effect**. Single-deletion means range +5.63 to +11.49 pp;
two-deletion means +3.43 to +13.47 pp. All eight paired captures have lower
warming median scores, mean difference -0.08487. Warming is the recorded
thermal-rule state, not proof of a cold start; it can occur during an otherwise
hot ride.

Fine stationary is **not robust** to capture influence: its interval includes
zero, and removing 006 makes the mean slightly negative. Fine warming remains
positive after any one/two supported-capture deletions, but support is only six
groups. Steady moving does not retain the prior baseline's high pooled burden
after these refits. This instability further cautions against interpreting the
old detector's context ordering as a universal property.

### Sensitivity and Context Eligibility

Excluding only 001/020 from aggregation leaves 16 captures and 9,978 windows:
401 flags, 94 runs, equal-capture mean **10.43%** (bootstrap 2.36-24.10%),
duration-weighted **4.06%**, pooled **4.02%**, 13.46 runs/full capture-hour.
No fold was refitted and neither capture was removed from training.

Masking the six discrepant 001 windows leaves 9,983 scored observations in
that view, 406 flags, and 96 runs. Equal-capture frequency stays 20.39% because
001's one remaining window is still flagged. Both macro paired effects are
**unchanged in both sensitivity views**: 001/020 do not supply both contexts
to those paired comparisons. This is transparent denominator behavior, not an
adjudication or evidence that short/discrepant data are harmless in other uses.

| Macro Context | Homogeneous Known Windows | Supported Held-Out Folds | Training Groups per Fold |
| --- | ---: | ---: | --- |
| Stationary running | 657 | 15/18 | 14-15 |
| Moving episode | 5502 | 15/18 | 14-15 |
| Steady moving | 647 | 10/18 | 9-10 |
| Warming | 482 | 11/18 | 10-11 |
| Thermally stable | 7393 | 14/18 | 13-14 |

There is enough marginal macro support for exploratory stratification, **not
for every context in every fold**. Examples of sparse common support are 002's
one stationary window, 007's five warming windows, and 023's nine warming
windows. Such overlapping windows do not constitute that many independent
episodes. Fine eligibility is poorer. Joint macro known-known coverage is
5,420/9,989 windows, versus 1,511 fine windows; H10.0's one-window/one-group
steady-warming intersection remains unsupported for a joint conditioned model.

Operating engine-off/unknown have **zero homogeneous eligible windows**, not
zero labeled intervals or zero anomaly risk. Their original intervals remain
inside boundary windows. Thermal unknown is abstention, never a fitted known
context. Macro either-axis ambiguity affects 3,871 windows. All unknown and
ambiguous windows retain predictions and provenance in the primary output.

## Special-Capture Investigation

The following separates measured values and distribution differences from
physical interpretation. All five captures passed native decoder/schema,
source-hash, sample-count, feature-order, and context-mapping checks. There
were no missing model signals or invalid frames. Temperature values remain
provisional high-confidence decodes; speed is RAW-derived and operator
corroborated, not GPS calibrated. Feature percentile departures are descriptive
univariate comparisons, **not causal IF feature attribution or physical limits**.

### 001: Retained Speed/Context Discrepancies

The 19 discrepant samples are indices 13-29 and 49-50, with stationary labels
but measured speed above 1 km/h, in two fine intervals/one macro episode.
Speed is 0-6 km/h (median 1), RPM 789-3,562 (median 1,261), ECT 45-48 C,
battery 11.0-14.8 V. All seven windows are flagged, as with the old baseline.
All seven battery-mean features lie outside the training p01/p99 range;
held-out median battery mean is 14.032 V versus training median 14.518 V.
Low-temperature features also fall outside training central percentiles.
These are distribution differences, not a diagnosed battery or motion fault.
The six-window sensitivity preserves the labels. Actual boundary/speed
adjudication remains pending; only one homogeneous operating window exists.

### 014: High Burden During Low-Temperature, Mostly Stationary Operation

115/116 windows are flagged (99.14%; old baseline 114/116), forming two runs.
RPM median is 1,359; RAW speed median/p95 are both 0 km/h; ECT rises within
32-65 C, IAT is 31-33 C, and battery ranges 10.7-15.0 V (median 14.5 V).
Macro stationary has 114/115 flagged windows; warming has 109/110.
Every held-out ECT-min window lies outside its fold's training p01/p99
range of 74-94 C; median ECT-min is 51.5 C versus training median 90 C.
This is strong measured thermal/training-distribution separation, not evidence
of overheating or a decoder failure. There is no moving or stable common
support here, so 014 is **absent from the paired macro contrasts**, although
it strongly influences unpaired and absolute-burden summaries.

### 020: Extremely Short, Transition-Dominated Capture

87 samples, 21.501 seconds, four overlapping windows; all four are flagged in
one run. RPM spans 0-4,319 (median 1,073), battery 10.6-14.7 V (median 12.7),
ECT 93-95 C, speed 0-10 km/h. Every battery-mean window is outside training
p01/p99; held-out median window mean is 13.553 V versus 14.518 V in training.
All operating windows are boundary ambiguous and all thermal windows unknown.
Thus no known operational or thermal effect can be estimated within 020.
Its 100% frequency and 167.43 runs/hour are unstable short-denominator
descriptions, not evidence of a sustained fault. It remains in the primary fit
and analysis, with the declared aggregation-only sensitivity.

### 023: Long-Ride Burden and Changed Operating Distribution

This operator-attested out-of-city continuous ride has 17,539 samples and
1,749 windows: 168 flags/33 runs (9.61%, 27.05 runs/capture-hour), compared
with 303 flags/63 runs in the old selection-exposed baseline. RPM median is
6,362, speed median 57 km/h, TPS voltage median 1.328 V, ECT median 93 C
(89-98), IAT 48-53 C, battery 13.5-15.1 V. These describe the capture; they
do not establish harmful riding or a physical fault.

11.61% of window TPS-median features are outside training p01/p99. Window
TPS-raw median is 33 versus training median 16; about 10.2% of ECT features
are outside training central percentiles. Maximum adjacent sample gap is
751 ms, preserved by the importer; no artificial splitting or schema mismatch
was found. Macro moving episode has 90/803 flags, steady 5/220, stationary
3/70, stable 64/1,279, unknown 47/225, and warming 4/9. The warming contrast
therefore has only nine overlapping windows; it is influential and requires
real boundary review. High absolute burden persists but is not synonymous
with diagnostic error.

### 004: Low-Burden Comparison

One of 297 windows is flagged (0.34%), the same count as the old baseline.
RPM median is 4,073, speed median 23 km/h, ECT 89-93 C, IAT 46-47 C,
battery median 14.5 V. Macro moving episode 0/197 and stationary 0/19 give a
tied flag contrast; there is no known warming support. Its top univariate
departure is RPM delta/slope, with 6.40% of windows outside training p01/p99,
yet IF burden remains low. This illustrates why marginal deviations or context
descriptions alone do not explain every IF flag. Low burden does not establish
confirmed healthy operation or detector sensitivity.

## Decision Analysis

1. **Does warming/stationary burden persist without fit leakage?** Yes for
   the preregistered macro paired means and median-score differences. Fine
   warming supports the direction; fine stationary flag frequency is unstable.
   Neither held-out group nor its source rows enters that fold's scaler/IF fit.
2. **Is it consistent across captures?** No universal flag-frequency effect.
   Stationary has six positive/six negative/one tied pairs; warming five/two/one.
   The two largest effects dominate magnitudes, although any two deletions
   leave positive macro means. All paired stationary/warming median-score
   differences are negative. Capture 014 drives absolute burden but not those
   paired comparisons.
3. **Enough macro windows per fold?** Enough for limited marginal descriptive
   work, not complete fold coverage or joint conditioned estimators. Reported
   support flags are feasibility checks; sparse overlapping windows, missing
   states, and only 18 operator-attested groups limit effective information.
4. **Further calibration justified?** A later exploratory, same-IF calibration
   study is justified **after genuine boundary review**, with calibration groups
   inside each training partition and no use of outer-test scores to choose
   cutoffs. This task fits no context cutoff. More cold/stationary capture
   coverage and independently measured physical truth would improve the design.
5. **Conditioned model or unchanged IF?** Keep the current production IF and
   thresholds unchanged. These data justify testing a simpler context-aware
   calibration hypothesis first, not selecting/training a conditioned IF or PCA
   now. There is insufficient evidence of fault accuracy to promote anything.

The verdict is **`context_experiment_justified`**, under the preregistered
exploratory rule. It is not `baseline_sufficient_for_now`: absence of a uniform
effect is not proof of baseline adequacy. This study cannot establish fault
sensitivity, false-alarm rates, physical diagnoses, or generalization across
motorcycle models. Context rules reuse some IF input signals, adding circularity
to any naive causal interpretation. Machine-independent capture metadata,
independent context adjudication, and fault truth are still unavailable.

## Engineering Verification and Reproduction

The automated run independently checked all canonical sample mappings,
window identities, event references, fine/macro traceability, and retained
boundaries against approved-v2. Every fold verifies source/group disjointness,
training-only scaler center/IQR, and training-only percentile offset.

All **18 refits were repeated** from the pinned feature cache in another
disposable directory. Held-out features were changed by +1,000,000 during
each repeat fit; training matrices and fitted model/scaler bytes stayed
identical. All **9,989 raw scores, decision scores, and predictions are
bit-identical** on the restored held-out features. Model metadata timestamps
and training runtime intentionally vary and are not claimed byte-reproducible.
No production artifact was used as a silent replacement for a research fold.

All **837 pinned protected files** remained unchanged through fitting,
repeated fitting, and the full regression suite, including production models,
analyzer routes, RAW, annotations, historical studies, and tests. All original
H10.0 pinned input hashes also still match. The H10.0 test was adjusted only to permit
additive research files while still requiring every previously pinned file's
original hash; no historical H10.0 result was rewritten.

Local observations, **not production benchmarks**: primary verification and
18-fold execution took **172.46 s**, peak RSS **508.01 MiB**; repeated fitting
took **36.55 s**, peak RSS **312.66 MiB**. No application memory/time limits,
worker behavior, production models, or thresholds were changed.

Full regression: **373 passed, 0 failed, 0 skipped**, 63.62 s pytest time
(65.08 s process wall time; peak RSS 325.21 MiB). This includes 15 new tests
covering group/source separation, held-out poisoning, train-only scaler/offset,
disposable storage, conservative ambiguity, overlap-safe run burden, sparse
support, seeded uncertainty, decision safeguards, registration tampering, and
the complete published experiment. Existing API/schema/context persistence
regressions also pass. External Starlette/httpx and joblib/NumPy deprecation
warnings remain; no test failure or skip was suppressed.

The final completion validation independently checks unique window identities,
all prediction/decision/offset relationships, every fitted artifact hash and
81-feature schema, all fold/source inventories, all repeated-fit flags,
unchanged input hashes, and the JUnit results. Native production hashes remain:

- IF: `9f0ca2e687db31d4e5caabc220442f3884c4566c78bd321e67369be36c93abec`.
- Scaler: `38e50fb497fe4b7c4368c60932b913c24f4b35cb7a66030831d91e9487192f97`.
- Metadata: `0113ebd014374232b5e063f8ee3759a9c6aa732b7d30a8a99fb98f9f72de5945`.

```bash
.venv/bin/python -m app.evaluation.group_aware_if preregister
.venv/bin/python -m app.evaluation.group_aware_if run
.venv/bin/python -m app.evaluation.group_aware_if verify-refits
.venv/bin/python -m pytest --junitxml=data/evaluation/h10_1/regression_tests.junit.xml
```

Preregistration/run outputs are exclusive-create and refuse overwrite. The
commands above describe a clean study invocation; this completed study is
already registered and must not be silently rerun over its results. The
recorded disposable cache/models can be removed when no longer needed; their
hashes and provenance remain. Repeating verification requires that cache.

## Machine-Readable Deliverables

All outputs are under `data/evaluation/h10_1/`:

| Artifact | Contents |
| --- | --- |
| `protocol.json`, `preregistration.json` | Pre-fit rules, seeds, contracts, versions, registration hashes |
| `fold_manifest.json` | All 18 folds; every training/test source path, SHA, physical group, duration and count |
| `protected_inputs.json` | 837 protected file hashes pinned before training |
| `fitted_artifact_provenance.json` | Train-only feature hashes, source inventories, offsets, model/scaler/metadata hashes and disposable locations |
| `held_out_predictions.jsonl` | All 9,989 held-out scores/predictions, source/feature/model hashes, context status/boundaries/interval provenance and event IDs |
| `per_capture_results.json` | Per-capture and per-context counts, score distributions, run boundaries and union-time burden |
| `context_results.json` | Separate fine/macro summaries, capture-level uncertainty, duration weights and secondary pooled distributions |
| `context_eligibility.json` | Every fold/context denominator, supporting groups/windows/time, unsupported reasons and axis ambiguity counts |
| `uncertainty_sensitivity.json` | Paired capture effects, bootstrap uncertainty, one/two-capture influence and both declared sensitivities |
| `special_capture_investigation.json` | Measured signals, training-feature departures and current/prior descriptive burden for the five requested captures |
| `leakage_invariance_validation.json` | Initial input/fit separation, mapping/count and protected-file checks |
| `refit_reproducibility.json` | All 18 poisoned-held-out repeat checks, exact artifact/prediction reproduction and protected hashes |
| `final_decision.json` | Exploratory verdict, decision rule, production unchanged and manual review pending |
| `completion_validation.json` | Post-regression integrity checks, output/report hashes, exact score/offset semantics, test totals and unchanged-protected verification |
| `regression_tests.junit.xml`, `*_resources.txt` | Regression results and local runtime/RSS observations |

**Engineering readiness and human readiness are separate.** Automated refit
and invariance checks pass. Timed human review and manual boundary-quality
checks remain pending, including pilots 002/004/005/007/018 and the disclosed
001 discrepancies. No human review time, boundary accuracy, detector accuracy,
or manual acceptance gate has been fabricated or marked passed.
