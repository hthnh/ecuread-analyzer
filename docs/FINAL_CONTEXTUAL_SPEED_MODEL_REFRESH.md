# Final Contextual Speed Model Refresh

Date: 2026-10-11. Verdict: `final_dashboard_integrated` (local only).

## Scope and authorization

The collector's FINAL MODEL PASS instruction explicitly authorizes full-corpus
training and local activation. It does not authorize public deployment. The
authorization, exact source hashes, capture groups and original annotation
content hash are recorded in the new bundle's `training_authorization.json`
and manifest. Existing manual-review records remain unchanged, including the
pending H10.2A packet. Those historical records are not gates for this task.

Authoritative input:
`data/context_annotation/datasets/ecuread-filtered-raw-18-v1-approved-v2/`.
Content SHA-256:
`5fd672ff45b69681e310efff7661cc8313989cfc10191047dc476e8161e156c1`.

All 18 exact originals were imported, with 100,688 samples and 9,989 eligible
50-sample/stride-10 windows. All source hashes are unique. No aliases,
historical splits, replays or synthetic sources entered training. Physical
independence remains operator-attested (18), not machine-verified (null).
RAW files and approved annotations were not modified. Annotation mappings
were verified; they are not treated as independently established mechanical
normality. Context detectors retain measured operating-context semantics.

## Artifacts

Bundle: `native-full18-20261011T135838Z`.
Manifest:
`data/models/bundles/native-full18-20261011T135838Z/manifest.json`.
Manifest SHA-256:
`e84a8350aae4708a9e429a313e90f8ee1a625209705a6df48141133f1a8bd5ae`.

| Artifact | Version / SHA-256 |
| --- | --- |
| IF version | `iforest-full18-20261011T135838Z` |
| IF model | `ca902dd40e828c2b668f4cddb5ed244a6f387804049c2f10f9fd5f5e8bf44085` |
| RobustScaler | `2b780a9635327789aeb6a26ca8a7531d42512207fffe0dc152eccb0a1a971dfc` |
| IF metadata | `b0f9f6e13255d69e14af8f7b8a0af6e6887c7b01f52ad342937e489382d0ea35` |
| Battery version | `contextual-battery-robust-v1-ef5579cf38a0` |
| Battery reference | `6b0cebd6d7038b124a3eb94bbebf7d2a3f6c25b5393bf2331999423de3bcbecf` |
| Speed version | `contextual-speed-robust-v1-fce8419c1951` |
| Speed reference | `3aec3788f91ab07ce6286c4d606c428a4ac5413e57ac8695516dff34ad1b0348` |

IF was fitted from scratch with a fresh RobustScaler. Every IF parameter
matches the previous native model: 200 trees, contamination 0.03, random seed
42, max_samples=auto, max_features=1.0, bootstrap=false, warm_start=false,
n_jobs=-1. RobustScaler uses centering/scaling and the 25th/75th percentiles,
without unit-variance scaling. There is no new preprocessing, incremental
learning, hyperparameter search, context-conditioned IF or calibrated cutoff.
The feature extractor and ordered `ecu-window-features-v1` 81-feature contract
remain unchanged. Native decoder: `honda_keihin_71_17:1.0.0`;
telemetry: `canonical-telemetry-v2`. Training metadata timestamp:
`2026-10-11T14:00:52.370269+00:00`.

The refitted IF's learned offset is -0.5525607523078415. The existing decision
rule remains decision_function < 0. A learned offset changing after refitting
is not manual threshold tuning. The training corpus is observed operating
data, not independently verified healthy data. No accuracy improvement is
claimed from fitting more captures or from the resulting flag counts.

## Battery reference

The existing RPM/TPS context assignment, three modeled load contexts, scored
battery features, robust-z rule (4.5) and voltage scale floor (0.35 V) are
retained. Quantiles and MAD/IQR now give each contributing physical capture
equal total weight within each context. Thus a long ride contributes no more
weight to that context than another capture. All 18 captures contribute to
the build; supported contexts require 50 windows and three captures.
Insufficiently supported and unmodeled contexts remain explicitly skipped.
The old reference at `data/evaluation/h3/contextual_reference.json` is intact.

## Contextual Speed

Implementation: `app/ml/contextual_speed.py`. The existing verified native
decoder exposes Table17 byte 19 as optional `vehicle_speed_kmh`, with separate
`speed_provenance` (`honda-table17-speed-v1`). Importer, native RAW JSON API
and canonical/live API use this one path. Speed is operator-corroborated,
not GPS-calibrated. Invalid native frames do not supply usable speed.
Legacy clients can omit both new fields. Speed is auxiliary, not an IF input.

The detector uses the existing eligible window identities. Five elapsed-time
blocks produce median speeds and timestamp-based rates. Median rate and
median rate dispersion suppress quantization and short single-sample bursts.
Missing speed, incomplete speed, missing/non-increasing clocks and sparse
contexts abstain; speed is never imputed or manufactured from RPM/TPS.

Contexts distinguish stationary (all speeds <=1 km/h), stop transitions,
and moving windows, combined with existing RPM/TPS load contexts. Moving
speed bands are learned from capture-balanced quartiles. Ten contexts meet
the 50-window/three-capture support gate. Each supported context stores
capture-balanced medians, quantiles, MAD and IQR scales for rate, rate
dispersion, RPM and TPS. Floors are pinned in the reference configuration.

Temporal evidence uses rate deviations. Moving relationship evidence requires
both RPM and TPS deviation, taking the smaller of RPM deviation and the larger
TPS deviation. RPM alone cannot trigger it. Stationary operation has no
RPM/TPS relationship alert. The local robust-z threshold is 4.5, with no
held-out tuning. Scores are statistical deviations, not fault probabilities.
There is no constant CVT ratio, engine-off assumption at zero speed,
throttle-burst fault rule, road speed limit or component diagnosis.

Each window retains speed/RPM/TPS measurements, context, score, applicability,
skip reason, original start/end timestamps and reference support. Detector
and bundle versions plus configuration/source hashes are persisted. Missing
speed skips only Speed; IF/Battery results are invariant.

## Harness and persistence

The existing harness now runs IF as primary with Battery and Speed as
supporting evidence when an active native bundle is selected. Aggregation
receives the explicit registered detector set. Detector-local scores are not
fused or voted, and an extra detector does not change IF health/status/severity.

RCA v2 (`deterministic-rca-engine-v2-speed-v1`) preserves speed observations
without inventing mechanical causes. When matching IF RPM/TPS and Speed
relationship evidence overlap, one operating observation carries both
provenances. Independent speed-change, IF and electrical evidence remain
separate. Historical processing supports the new speed observation family;
old history lacking speed does not become a comparable speed reference.

Analysis windows retain all three detector payloads, aggregate evidence and
source timestamps. Analysis records, session/upload responses and
`GET /api/v1/model` report the actual bundle/detector versions. Historical
diagnoses are not rewritten. Identical upload retries reuse their original
analysis; an explicit reanalysis creates a new versioned result.

## Verification

Machine-readable results: `data/model_refresh/`:
`build.json`, `corpus_smoke.json`, `integrity.json`, `analyzer_tests.xml`,
`drivesafe_tests.log`, `drivesafe_browser.json` and `completion.json`.

- All 18 full-file native uploads pass canonical value, quality flag, speed,
  timestamp and sequence parity with the direct native importer.
- All 9,989 expected windows persist without duplicate identities or dropped
  eligible windows. IF scores all 9,989; Battery scores 9,880/skips 109;
  Speed scores 9,920/skips 69. Statistical flags are respectively 300, 16, 8.
  These are in-corpus engineering observations, not accuracy measurements.
- Aggregation, RCA and historical evidence complete for every capture.
- Capture 018: 17,976 input frames = 17,976 canonical samples, 4,496.001 s,
  1,793 windows; IF 1,793 scored/0 skipped, Battery 1,785/8, Speed 1,790/3.
  Upload/direct scores match, missing-speed isolation passes and retry reuses
  the same session and analysis. Six trailing samples are retained canonically
  but do not form another complete 50-sample window, as required by geometry.
- Analyzer: 410 passed, zero failures/skips. DriveSafe: 83 passed.
  Tests include native/live speed parity, invalid speed/frame rejection,
  quantization, sparse-context skips, high-RPM/low-speed and stationary safety,
  schema/hash failures, API versions, rollback inference, persistence,
  independent evidence preservation and published-result integrity.
- Playwright: 16 page/viewport/language combinations pass at 1440x1000 and
  390x844, English and Vietnamese, with zero page errors or horizontal page
  overflow. Screenshots verify actual supported-negative capture 001 and
  positive capture 003 findings plus expandable three-detector details.
- Existing dependency deprecation warnings and DriveSafe SQLite resource
  warnings remain; they are not suppressed.

Published H10.0/H10.1/H10.2A results, source data, annotations and previous
models are verified unchanged. Pre-refresh application/test code is archived
in `data/model_refresh/pre_refresh_code.tar`. The exact before/after hashes
are recorded in `authorized_code_transition.json`. Published-result checks
can verify that archive only for explicitly listed code changes; data/model
changes are never exempted. Strict current-input guards for starting new
research runs remain strict.

## DriveSafe integration

Changes are in `/home/hthnh/drisafe`, using its existing design and templates.
The new finding is `Speed and operation` / `Tốc độ và vận hành`. Messages are
derived only from an applicable stored detector finding. Supported-negative
messages are limited to supported intervals; positive messages recommend
monitoring repeatability without claiming failure. Missing/skipped/failed
Speed produces no fabricated normal finding. Existing temperature, battery,
RPM and history findings remain.

Three detectors appear in existing technical/AI views. Speed and provenance
round-trip through existing JSON fields, with no database migration.
Visible session terminology is neutral (recorded/analyzed), including removal
of `Phiên demo` session labels. Research metadata remains internal or in
expandable technical views, not a persistent user-facing warning.

## Activation and rollback

One pointer, `data/models/active_bundle.json`, selects the local native bundle.
The loader verifies manifest/artifact hashes, actual IF metadata, ordered
features, native schema, reference schemas and window geometry before use.
An invalid active bundle errors rather than falling back silently to legacy.
Legacy-v1 model selection remains unchanged.

Build and automatic checks (each build creates a new version):

```bash
.venv/bin/python scripts/refresh_native_model_bundle.py build
.venv/bin/python scripts/refresh_native_model_bundle.py smoke
.venv/bin/python scripts/refresh_native_model_bundle.py activate
```

Immediate artifact rollback, without rewriting analyses:

```bash
.venv/bin/python -m app.ml.active_bundle data/models/bundles/native-pre-full18/manifest.json
```

Restore this refreshed version:

```bash
.venv/bin/python -m app.ml.active_bundle data/models/bundles/native-full18-20261011T135838Z/manifest.json
```

Both manifests load and rollback reproduces direct old-native IF scores. The
previous IF model/scaler/metadata and Battery reference remain at their
original paths, with their original hashes. Rollback has no Speed detector.

Local analyzer: `http://127.0.0.1:8770`.
DriveSafe preview: `http://127.0.0.1:5071`, using actual refreshed results and
a disposable SQLite database. Preview login:
`preview@localhost` / `local-preview-only-password` (loopback preview only).
The preview does not modify the public database or copy originals into it.

## Resources and deployment

Build/import/feature/reference/refit verification: 135.85 s, 391.40 MiB peak
RSS; the scaler+IF training portion is 1.3863 s. Final all-18 upload/parity
suite: 310.14 s, 708.19 MiB process peak RSS. Capture 018 upload processing:
29.91 s; its complete verification block, including importer parity, two
additional full analyses, persistence inspection and retry, is 102.33 s.
These are local observations with concurrent regression work, not production
benchmarks or per-request memory guarantees.

Analyzer upload limit remains 25 MiB and the RAW JSON-array contract remains
5,000 records. Full captures use `/api/v1/import/raw-session` or
`/api/v1/sessions` multipart file upload. DriveSafe's existing 2 MiB proxy/body
limit, 2.5 s analyzer-client timeout and 120 s Gunicorn timeout are unchanged.
The 2.5 s client timeout is not suitable for synchronous full long-capture
analysis. No proxy/worker/memory/concurrency limits were increased. Concurrent
long analyses need bounded deployment resource testing before public rollout.

Local artifacts were built/loaded with Python 3.14.4, sklearn 1.9.0,
NumPy 2.5.1 and pandas 2.3.3. Cross-version public-container artifact loading
has not been claimed or silently attempted.

No public image rebuild, container restart, migration or database change was
performed. The running analyzer image predates this code; its previous native
artifacts are preserved. Its data bind mount can see the additive artifacts,
but its installed selector does not support the new active-bundle pointer.
Public deployment remains an explicit separate operation, not an automatic
consequence of this local engineering verdict.
