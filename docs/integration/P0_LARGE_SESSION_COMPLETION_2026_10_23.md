# P0 completion: complete RAW sessions and faithful evidence rendering

Date: 2026-10-23, Asia/Saigon. Status: **implementation and disposable regression passed; no production deployment or reanalysis performed**.

The existing RAW endpoint now supports complete sessions through 20,000 records with finite byte, admission and execution guards. All five required input sizes reached the actual active IF/battery/speed harness, retained their complete canonical data and window boundaries, and persisted diagnostic evidence. DriveSafe renders actual RCA statement objects and reports consistent detector counters. No model, anomaly threshold, health formula, detector or database schema was changed.

## Strategy and limits

The file and canonical routes were considered before expanding RAW JSON. Native file ingestion assigns upload/session and sample identities and returns a different envelope; replacing the existing rerun contract would require additional identity/readback adaptation. The canonical endpoint would bypass the requested fresh decoding of stored RAW. Retaining `POST /api/v1/raw/decode-analyze` therefore preserves the existing decoder, AnalysisService, Harness and DriveSafe validation with the smallest compatible change.

| Guard / behavior | New contract |
|---|---|
| Complete RAW records | Maximum **20,000**, one logical session, supplied ordering preserved. Above the ceiling: 422 with the record validation message. |
| JSON body | Maximum **8,388,608 bytes (8 MiB)**, checked before JSON parsing, including chunked bodies. Above the limit: 413 / `RAW_REQUEST_TOO_LARGE`. |
| Admission | **One RAW request per shared data directory**, enforced with a Linux file lock across processes. Extra requests are rejected 429 / `RAW_ANALYSIS_BUSY`, with `Retry-After: 5`, before buffering their body. This guard is specific to RAW JSON; other existing routes were not redesigned. |
| Body receipt | **10 seconds**; expiry: 408 / `RAW_BODY_TIMEOUT`. |
| Compute | **45 seconds**, checked after decoding, validation, feature extraction, primary inference and diagnostic generation, before result persistence. Expiry: 504 / `RAW_ANALYSIS_TIMEOUT`. A current stage finishes before its checkpoint; this is not a hard thread interruption or an OS memory quota. |
| Windows | Existing **50 / 10** geometry. Every eligible full window is retained, including windows crossing the former 5,000-record boundary. Partial tails remain in canonical data and are not padded. |
| Client timeout | Default **60 seconds**, aligned in DriveSafe configuration, environment example, compose default and client construction. This matches the previously observed live 60-second configuration; no running environment was changed. No increase to the existing 90-second proxy configuration was needed for the measured requests. |
| Other validation | Existing record/frame/provenance validation retained; timestamps must be finite. Session identifiers cannot escape the file repository path. |

The full canonical response remains part of this endpoint's contract. For 17,976 samples the measured request was **1,836,191 bytes** and response **17,155,119 bytes**. The request fits the 8 MiB guard. Returning the canonical samples enables DriveSafe to validate the decoder, count, ordering and timestamps rather than accepting a partial result. The finite record ceiling bounds this response; an additional response byte rejection after persistence was not introduced.

Implementation: `app/api/raw.py:27-79`, `app/config.py:67-71`, `app/services/analysis_service.py`.

## Retry and historical-result semantics

RAW JSON accepts an optional body field `idempotency_key`. A key is scoped to device/session and a normalized payload fingerprint. A successful retry returns the same run and canonical result without inference; conflicting input returns 409. Unkeyed callers retain their previous behavior of creating a new analysis.

For keyed requests the server reserves a run ID and canonical snapshot before execution. Window persistence finishes before the analysis run record, which acts as the completion marker. The persisted run includes its original response summary. This allows a retry to recover after a restart between run completion and final response-cache replacement, and allows an interrupted write to be repaired under the same reserved run ID instead of creating another run. These cases are explicitly tested.

DriveSafe derives its key from the RAW request, configured model selection and latest stored analysis ID. Failed/lost-response retries retain the key; a later explicit rerun after success gets a new key. A new result is stored beside the historical Pi result. Response validation now verifies ordering as well as sample membership and timestamps.

## Final full-session regressions and benchmark

Each case ran in a fresh Python process with disposable file storage and copied, hash-verified active artifacts. The workload uses the first N records of the existing `session_000018.jsonl` as a complete disposable input, not an existing production session. The source file SHA256 is `6ef183818d3743c5bae2527928487f7f5991264f2f07528b965bddd0979e4b04`.

| Input records | Persisted samples | Windows | Last full window, inclusive | Retained unwindowed tail | First request | Peak worker RSS | Cached retry |
|---:|---:|---:|---|---:|---:|---:|---:|
| 5,000 | 5,000 | 496 | 4950-4999 | 0 | 10.414 s | 283.58 MiB | 0.227 s |
| 5,001 | 5,001 | 496 | 4950-4999 | 1 | 9.274 s | 283.89 MiB | 0.228 s |
| 9,917 | 9,917 | 987 | 9860-9909 | 7 | 17.175 s | 348.57 MiB | 0.488 s |
| 12,625 | 12,625 | 1,258 | 12570-12619 | 5 | 22.389 s | 384.83 MiB | 0.522 s |
| 17,976 | 17,976 | 1,793 | 17920-17969 | 6 | 31.734 s | 504.36 MiB | 0.838 s |

Timing is the complete initial ASGI request/response. RSS is the fresh worker's process high-water mark through that request and its output validation, before the cached retry. It includes Python imports, actual model inference, server persistence, TestClient and client-side objects; it is not incremental model memory or a production-container capacity certification. Measurements used the available Python 3.14.4 environment. The deployed container's Python 3.12/runtime performance remains a staging validation item. The 20,000-record guard is finite; the requested successful workload matrix was tested through 17,976, with 20,001 explicitly rejected.

Every case validated all source-to-canonical signal values, timestamps, speed provenance, persisted sample counts, **every** window boundary, all per-window detector payloads, aggregate window count, persisted RCA, same-run retry and unchanged source/artifact hashes. Full-window exclusion of the final tail is intentional; no input samples were truncated.

All cases used bundle `native-full18-20261011T135838Z` and IF `iforest-full18-20261011T135838Z`, with battery `contextual-battery-robust-v1-ef5579cf38a0` and speed `contextual-speed-robust-v1-fce8419c1951`. No artifact was trained or modified.

For the 12,625-record case:

| Detector | Execution | Scored windows | Anomalous windows | Unsupported windows |
|---|---|---:|---:|---:|
| Isolation Forest | ok | 1,258 | 5 | 0 |
| Contextual battery | ok | 1,253 | 1 | 5 |
| Contextual speed | ok | 1,258 | 0 | 0 |

Skipped individual battery windows retain their applicability reasons; a negative speed finding is not fabricated missing evidence. These are statistical detector outputs, not a vehicle fault diagnosis.

The machine-readable results, run IDs and implementation hashes are in `docs/integration/P0_LARGE_SESSION_REGRESSION_2026_10_23.json`.

## DriveSafe rendering and actual HTTP integration

The consumer now accepts `statement` alongside all previously supported text keys. Observations, symptoms and possible causes retain their own sections. Recommended checks and priorities keep their existing descriptions, and empty causes remain empty.

An unchanged-content fixture was copied from stored server run `analysis_20261012T141946Z_e49a483543ba`; the fixture records the original source path/hash. Both consumer construction and the actual Flask session template were checked:

| Required stored-fixture regression | Result |
|---|---:|
| Observations displayed, with actual statement text | **6** |
| Symptoms displayed, with actual statement text | **1** |
| Recommended checks retained | **8** |
| Possible causes | **0**, represented without inventing a cause |
| Executed / applicable detectors | **3 / 3** |

Guest detector objects now use canonical nested applicability/score/threshold fields. Its one-detector scenarios report **1 executed / 1 applicable**, with their existing full coverage; Guest content remains isolated from real results. The consumer also understands the older top-level applicability boolean. Counts now distinguish executed, applicable, skipped, and unavailable/failed rather than merging skipped into unavailable. It does not count skipped/failed detectors as applicable or infer their outcomes.

Missing diagnostic evidence is classified **`missing_evidence` / “Evidence missing”**. It no longer asserts that a recent Pi result predates harness enablement. The existing rerun action remains available for that state. The small badge-style change reuses the existing neutral state styling.

A separate disposable loopback Uvicorn server then handled a real HTTP request from the existing DriveSafe submission/client code. DriveSafe used in-memory SQLite and ingested 12,625 copied native records in 600-record batches. The result was:

- **12,625 samples / 1,258 windows**, actual active IF/battery/speed evidence, persisted in the isolated Analyzer.
- Submission, validation and Cloud-side storage completed in **24.589 seconds**.
- Run `analysis_20261023T154447Z_4357c522186f`; Analyzer evidence matched the stored DriveSafe block exactly.
- Run ID, model version, bundle provenance, detector versions/status/applicability, aggregate and RCA survived the integration.
- The actual DriveSafe session HTML returned **200** and rendered **5 observations / 1 symptom / 7 checks / 0 causes / 3 detectors** for this new input. These counts are telemetry-derived and need not equal the separate historical fixture's counts.
- The historical Pi result remained a distinct, unchanged result. Original RAW and canonical telemetry rows retained their exact fingerprints. Input files and active artifacts retained their hashes.

No deployed endpoint or production database was used for this verification.

## Tests and changed files

**90 Analyzer tests passed** across RAW bounds/decode, native upload, long-session compatibility, live research integration, diagnostic contracts, features and contextual speed. **100 DriveSafe tests passed** across the application suite, structured-evidence contract and existing contextual-speed consumer checks. No training suite was run. Test runs reported existing Starlette/httpx and NumPy/joblib deprecation notices; no test failures remained. `git diff --check` passed for both repositories.

Commands:

```bash
# Analyzer
.venv/bin/python -m pytest tests/test_raw_request_limits.py tests/test_raw_decode_analyze_api.py tests/test_native_upload_compatibility.py tests/test_long_session_compatibility.py -q
.venv/bin/python -m pytest tests/test_live_research_harness.py tests/test_diagnostic_integration.py tests/test_features.py tests/test_contextual_speed_bundle.py -q
.venv/bin/python scripts/verify_large_raw_sessions.py --with-drivesafe

# DriveSafe, from /home/hthnh/drisafe
.venv/bin/python -W ignore::ResourceWarning -m unittest tests.test_app tests.test_diagnostic_evidence_contract tests.test_contextual_speed -q
```

Task files in Analyzer:

- `app/api/raw.py`, `app/config.py`, `app/services/analysis_service.py`: capacity, admission/body/deadline guards, keyed recovery and persistence completion ordering.
- `tests/test_raw_request_limits.py`, `tests/test_native_upload_compatibility.py`: limits, rejection before analysis, busy/deadline behavior, retry/restart recovery, conflict and preserved historical runs; the old 5,001-rejection test now tests the actual maximum plus one.
- `scripts/verify_large_raw_sessions.py`: required size matrix, fresh-worker timing/RSS, complete telemetry/window checks, copied active models and the loopback integration orchestrator.
- `docs/architecture/ANALYSIS_PIPELINE.md` and the two P0 completion/result files in `docs/integration`.

Task files in `/home/hthnh/drisafe`:

- `services/analysis.py`, `services/demo_data.py`, `templates/session_detail.html`, `static/css/dashboard.css`: actual RCA text, canonical Guest shape, truthful absence/counters and neutral badge styling.
- `services/analysis_submission.py`, `integrations/analyzer_client.py`: stable attempt keys, sample-order validation, useful timeout/HTTP errors and the client default.
- `config.py`, `.env.example`, `docker-compose.yml`: consistent 60-second default. These are source changes, not applied deployment configuration.
- `tests/test_app.py`, `tests/test_diagnostic_evidence_contract.py`, `tests/fixtures/server_harness_20261012.json`: actual stored-output rendering, legacy compatibility, empty causes, counters, missing-evidence classification, timeout and new-attempt behavior.
- `scripts/verify_large_session_evidence.py`: disposable SQLite/loopback HTTP round trip with preserved prior result and immutable source rows.

Both working trees already contained substantial unrelated changes. This list identifies task-touched files; the full diff against Git HEAD includes pre-existing work and must not be treated as a clean task-only patch. No commit, reset or deployment was performed.

## Remaining producer-to-Cloud gaps and readiness

**Ready for review and staging as a coordinated Analyzer/DriveSafe change.** The requested 12,625-record acceptance path and 17,976-record regression pass using actual active artifacts and deterministic evidence. Production remains on its existing image/configuration until separately authorized deployment.

Remaining integration gaps are distinct from this completed server rerun path:

- The remote Pi producer was not changed. Its future uploads must actually compute/include the existing diagnostic evidence block and appropriate artifact provenance; this change cannot supply evidence that the producer never sent.
- Pi sync still has its existing whitelist for top-level analysis fields. Nested diagnostic evidence survives where supplied; arbitrary top-level `detectors`/`model_bundle` on that separate producer contract still need explicit treatment. The verified server rerun path retains both fields.
- Historical Pi sessions are not automatically backfilled. Any production rerun requires separate authorization and produces a new result, preserving the old one.
- The earlier audit's session/window coverage-summary and disagreement-scope inconsistencies remain. They do not remove per-window detector data or the successfully rendered observations, and changing aggregation semantics was outside this P0's prediction-preserving scope.
- Stage deadlines are cooperative and the measurements establish the stated workload on this host, not a fleet capacity guarantee. Confirm the candidate image's timing and available memory in staging before production rollout; no new architecture, training or LLM is needed for that check.

No contextual ECT detector or causal evidence was invented. Empty possible causes remain a correct outcome under existing RCA gates.
