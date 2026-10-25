# Long Session Compatibility Smoke

Run: 2026-10-11 UTC. Machine-readable result:
`data/evaluation/long_session_compatibility/smoke_report.json`.

## Verdict

**FAIL for unqualified original-RAW API compatibility. PASS for the complete
native-v2 import -> canonical analysis -> research harness -> CTX2 path.**

No silent 5,000-record truncation was observed. Two distinct compatibility
limitations prevent an overall PASS:

1. `POST /api/v1/raw/decode-analyze` rejects 17,976 records with HTTP 422:
   `List should have at most 5000 items after validation, not 17976`.
   It creates no session. This is a request record-count limit, not a decoder,
   inference, or window-count limit.
2. `POST /api/v1/sessions` accepts the entire original file but dispatches to
   the legacy v1 adapter and root legacy model. Its battery and ECT values
   differ from native v2 decoding in all 17,976 samples. At sequence 0,
   battery is 10.9 V instead of 12.5 V, and ECT is -9 C instead of 92 C.
   Contextual Battery reports `missing_features: tps_raw_median`, not a valid
   native-context not-applicable result. HTTP 200 does not establish native
   decoding compatibility. `/api/v1/import/raw-session` shares this upload
   handler; it was inspected, not separately posted.

No model, production threshold, production route, or decoder was changed.
The smoke runner deliberately does not work around the request cap by
splitting the capture into several sessions.

## Source

- Original supplied file: `real_data/fulldata_afterclean/session_000018.jsonl`.
- SHA-256: `6ef183818d3743c5bae2527928487f7f5991264f2f07528b965bddd0979e4b04`.
- Size: 1,505,540 bytes; 17,976 records, all valid native Table 17 frames.
- Captured timestamps: 0 through 4,496,001 ms, strictly increasing.
- Duration: 4,496.001 seconds, or 74 minutes 56.001 seconds.
- Measured sample intervals: 247-502 ms; no CTX2 capture gap.

The folder has 18 files / 100,688 records; seven files exceed 5,000 records.
The selected file is the largest by record count. All 18 whole-file hashes
are distinct. This is not proof of physical capture independence. The
supplied `ecu_raw_18_manifest.json` attests captured origin but explicitly
leaves physical capture identity unverified. Its selected entry matches the
measured source SHA, frame count, size, and duration. No curated campaign
manifest was replaced or expanded.

## Full Native Pipeline

The real `import_native_jsonl_file` imports the original, unsplit file.
Its complete canonical-v2 payload is submitted to the real
`POST /api/v1/analysis` ASGI route, with the existing native-v2 model directory
`data/models/honda_keihin_71_17_v2`. This explicit model selection does not
change the default upload route or its legacy model. All writable storage is
temporary; the existing contextual reference and historical artifacts are
read via symlinks.

| Measurement | Result |
| --- | ---: |
| Input records / valid RAW frames | 17,976 / 17,976 |
| Canonical samples / ML-eligible samples | 17,976 / 17,976 |
| Expected / generated / persisted ML windows | 1,793 / 1,793 / 1,793 |
| Isolation Forest scored / skipped | 1,793 / 0 |
| Contextual Battery scored / explicitly skipped context | 1,785 / 8 |
| Aggregated windows | 1,793 |
| RCA representative event items | 10 |
| Historical comparisons completed | 33 |
| CTX2 samples / verified RAW-speed samples | 17,976 / 17,976 |
| CTX2 pending segments / event markers | 534 / 312 |
| Native end-to-end processing time | 36.2495 seconds |
| Entire probe suite time | 67.7481 seconds |
| Peak native / suite process RSS | 615.82 / 615.82 MiB |

All 25 native-path assertions pass. Native Isolation Forest model:
`iforest-baseline-20260920T091709Z`. Contextual Battery:
`contextual-battery-robust-v1-d80a160236f5`.

1. Every RAW record becomes one canonical sample. All timestamps, ordering,
   sequences, quality flags, and signal values survive analysis persistence.
2. Every applicable 50-sample / 10-sample-stride window is generated. The
   independently calculated bounds match every persisted window, including
   windows crossing positions 5,000, 10,000, and 15,000. First bounds are
   `[0, 49]`; last are `[17920, 17969]`, inclusive.
3. The final six samples cannot form another complete window at the fixed
   stride. They remain in canonical telemetry and CTX2. They are not dropped
   input, a skipped applicable window, or padded model input.
4. Every IF window has a persisted prediction and decision score. All 1,793
   feature records persist. Contextual results account for every window:
   1,785 `ok`, 8 `skipped_context` (unmodeled operating contexts).
5. Aggregation, RCA v2, and historical evidence all report `available`.
   Research failures can be isolated by production code, so the runner checks
   these component statuses explicitly instead of treating HTTP 200 as proof.
6. Telemetry pagination returns 5,000 + 5,000 + 5,000 + 2,976 samples, each
   page advertising the full total. The combined records exactly match all
   canonical input fields. Only one native session and analysis are created.
7. CTX2 reloads persisted canonical telemetry with a temporary SHA-pinned
   RAW mapping. Every sample's decoded signature and timestamp interval is
   matched to the original RAW at offset zero. Its nonoverlapping segments
   cover `[0, 4496.001]` without gaps or artificial capture breaks. Speed is
   the existing rider-corroborated Table 17 candidate, not GPS ground truth.
8. The separate full-file upload probe preserves the RAW hash and timestamps
   and generates all 1,793 windows. Retrying the same device/client-session
   key reuses the same session and analysis: one of each after retry. This
   successful idempotency check does not excuse the legacy decoding issue.

CTX2 proposals remain pending in memory; none are saved or accepted. No
annotation dataset was frozen. All 19 protected files, including source,
model/scaler metadata and weights, references, threshold-bearing code, and
existing campaign/annotation files, have identical before/after hashes.
Temporary telemetry, uploads, and analysis artifacts are removed on exit.

## Limits And Timing

| Boundary | Checked behavior |
| --- | --- |
| RAW JSON request records | Actual full request rejected above 5,000; no session created. |
| RAW JSON request bytes | 1,836,071 bytes in the tested request; rejection is by record count. |
| Multipart upload bytes | Configured maximum 26,214,400 bytes (25 MiB); original is below it. Oversize 413 behavior inspected in code, not dynamically probed. |
| Canonical request | Complete 17,976 samples / 14,840,000 JSON bytes accepted; no application-level sample/body cap found on this route. |
| Read-page size | Maximum 5,000 is pagination, independently verified across all four pages. |
| Inference batch | All 1,793 windows supplied together; no application-level inference batch ceiling found. |
| ML window size | Fixed 50 samples / stride 10; no partial-window padding or threshold changes. |
| RCA/event output | Five representatives per event type and 15 response items maximum. Aggregation still consumes all windows; RCA operates on representatives by existing design. |
| Runtime | No application analysis timeout found. Tested process CPU soft/hard limits are unlimited. |
| Memory | In-memory processing, not streaming. Tested process address-space limits are unlimited; repository compose has no explicit memory cap. |

Native import took 2.5901 seconds; canonical HTTP analysis including
serialization/persistence took 26.8944 seconds; CTX2 loading, RAW alignment,
and suggestions took 4.7805 seconds. Native total additionally includes
pagination and completeness checks. Within analysis, measured IF inference
was 931.01 ms, Contextual Battery 152.13 ms, aggregation 2,884.89 ms, RCA
0.83 ms, and historical lookup 80.35 ms. These nested timings must not be
added again to the end-to-end total.

Peak RSS is Linux `getrusage(RUSAGE_SELF).ru_maxrss`, including interpreter,
libraries, original/canonical records, HTTP serialization, persistence,
verification, CTX2, and model loading. It is a process high-water mark, not
incremental model memory or a Pi 5 measurement. Focused tests were run
concurrently, so elapsed timings are smoke observations, not a performance
benchmark. ASGI testing does not exercise a deployed reverse proxy, network
upload timeout, deployment/container memory budget, or client response cap.

## Reproduction

```bash
.venv/bin/python scripts/verify_long_session_compatibility.py \
  --input real_data/fulldata_afterclean/session_000018.jsonl \
  --output data/evaluation/long_session_compatibility/smoke_report.json
```

Exit status 1 is intentional while either RAW API path remains incompatible;
inspect the separate native-path verdict and component checks in the JSON.
Changing limits or dispatch logic requires a separately scoped production
change, not a hidden adjustment to this smoke.

Focused verification: 63 tests passed across
`test_long_session_compatibility.py`, `test_raw_decode_analyze_api.py`,
`test_jsonl_upload_ingestion.py`, `test_live_research_harness.py`, and
`test_context_auto_labeler.py`. One existing Starlette/httpx deprecation
warning was emitted. Model training in existing unit tests is confined to
their temporary fixtures; the real smoke loads existing models only.
