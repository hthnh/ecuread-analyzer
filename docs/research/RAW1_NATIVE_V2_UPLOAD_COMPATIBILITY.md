# RAW1 Native-v2 Full Session Upload Compatibility

Date: 2026-10-11. Decision: `native_upload_ready` for the documented direct
analyzer multipart contract and the 18 supplied originals. This is a source-code
compatibility result, not production deployment or capacity certification.

## Route Audit And Root Cause

| Entry point | Input contract | Before RAW1 | After RAW1 |
| --- | --- | --- | --- |
| `POST /api/v1/raw/decode-analyze` | JSON object; explicit `raw_representation`; 1-5,000 records with sequence, timestamp and raw_hex | Existing native-v2 adapter normalizes native24/legacy29; route-local native model lookup could fall back to legacy root | Same decoder and record limit; centralized schema-aware model selection, no legacy fallback for v2 |
| `POST /api/v1/sessions` | Multipart file; optional device/session identity and sampling metadata; 25 MiB file limit | Always called legacy `import_raw_file`, including native JSONL | Validated structure dispatches native24 JSONL to the existing native importer; legacy29 JSONL and legacy RAW text retain v1 |
| `POST /api/v1/import/raw-session` | Same multipart contract | Same legacy-only path | Same shared dispatch and validation as `/sessions` |
| `POST /api/v1/analysis` | Already-canonical v1/v2 JSON; caller supplies decoder/schema provenance | No RAW parser; default artifact directory was the legacy root | Still no RAW parser; centralized schema-aware model selection and compatibility checks |
| `import_native_jsonl_file` | Native24 Table 17 JSONL; existing production decoder | Verified native-v2 decoding | Same decoder; statistics additionally report timestamped frames |

The precise fault was parser/adapter selection, not a 5,000-frame file-parser cap.
Both multipart routes unconditionally called the legacy adapter. Its JSONL parser
accepts 24-byte frames by prepending five FF bytes, then decodes them with the
legacy 29-byte offsets. That compatibility normalization is inappropriate for an
original native capture. For `session_000018` sample zero, it yielded battery
10.9 V and ECT -9 C instead of native 12.5 V and 92 C. All 17,976 ECT/battery
samples differ under these conversion contracts. Existing legacy records and
detector outputs have not been rewritten.

Native decoder identity remains `honda_keihin_71_17`, version `1.0.0`, canonical
schema `canonical-telemetry-v2`; legacy file decoding remains
`honda_keihin_legacy_29`, version `0.1.0`, schema `canonical-telemetry-v1`.
The explicit legacy29 **JSON-array** representation already uses the native
normalizing adapter; it is not the legacy **file** contract. RAW1 preserves that
existing distinction.

## Supported Full-File Contract

Run the updated analyzer with its usual application configuration, then upload
the complete original JSONL file directly to its multipart endpoint:

```bash
curl --fail-with-body http://127.0.0.1:8000/api/v1/sessions \
  -F file=@real_data/fulldata_afterclean/session_000018.jsonl \
  -F raw_representation=native24_table17 \
  -F device_id=raw-corpus-import \
  -F session_id=raw1-session_000018 \
  -F process_with_model=true
```

`/api/v1/import/raw-session` is equivalent. The optional representation field
confirms rather than overrides detected structure. Every JSONL record must agree:
native24 requires exactly 24 bytes and the Table 17 header; legacy29 requires
five FF prefix bytes and 29 bytes. Mixed representations, metadata conflicts,
malformed records, wrong headers, invalid native checksum/decoded-frame quality,
and ambiguous mixed text/JSONL uploads fail with HTTP 422 before source commit.
Legacy JSONL content is parsed as JSONL regardless of a `.txt` filename. Legacy
RAW text, including ordinary non-JSON preambles, retains its existing parser.
No second decoder was implemented.

Identical retries with the same `(device_id, session_id)` and source SHA reuse
the original session and analysis run. Conflicting bytes return 409. An old
idempotency entry decoded under v1 cannot silently satisfy a native-v2 request:
it returns a descriptive 409; preserve that historical entry and use a new client
session identity. Uploads without a stable client identity retain the existing
create-new-session semantics. Multi-worker atomic idempotency is not certified.

The JSON-array route deliberately retains its **5,000-record** validation limit;
requests exceeding it fail, not truncate. Do not split a physical capture to fit
that route. Use the full-file contract above. Canonical `/analysis` is not a RAW
upload replacement and has no dedicated large-body resource certification here.

## Schema And Provenance

Native analysis selects `data/models/honda_keihin_71_17_v2`; a `MODEL_DIR` already
pointing at an explicitly v2-tagged directory is also supported. A missing native
directory never licenses loading the legacy root model. Existing inference checks
telemetry schema, required signal columns, feature schema and feature names.
Incompatibility is visible as `model_unavailable`, null health, an explicitly
skipped detector with reason `model_incompatible`, and zero scored windows.
Such a result is a smoke failure, not a successful inference fallback.

The previously failing feature-schema regression expected a raised exception,
while the pre-existing inference implementation explicitly soft-fails model
incompatibility. The test now verifies the actual strict rejection semantics:
no predictions, zero scores, null health, explicit warning/status/reason. It was
not suppressed or marked expected-failure. Production inference soft-fail behavior,
models, feature definitions, thresholds and decision logic were not changed.

Persisted telemetry/session/raw-artifact metadata retains source SHA, source type,
decoder ID/version, telemetry schema, actual RAW/sample counts, timestamps,
sequences and quality flags. Native sample ordering is not reconstructed into
artificial capture pieces.

The model registry writer now receives the selected artifact directory instead
of recording the legacy root path for native inference. Each persisted analysis
also pins its actual model/scaler paths and SHA-256 in `model_artifact_provenance`,
so a later registry write using a shared historical model-version name cannot
erase which artifact that individual run used.

## Original 17,976-Frame Regression

- Source: `real_data/fulldata_afterclean/session_000018.jsonl`, 1,505,540 bytes.
- SHA-256: `6ef183818d3743c5bae2527928487f7f5991264f2f07528b965bddd0979e4b04`.
- Input/canonical/persisted telemetry: **17,976 / 17,976 / 17,976**, one session.
- Duration: **4,496.001 s**, all source timestamps and sequence positions preserved.
- Windows/features/persisted windows: **1,793 / 1,793 / 1,793**, 50 samples,
  step 10. The final six samples remain in telemetry but do not constitute another
  complete training-contract window. This is not truncation.
- Isolation Forest: **1,793 scored, zero skipped**, status `ok`.
- Contextual Battery shadow: **1,785 scored, eight `skipped_context`**, status `ok`;
  all 1,793 windows accounted for, no incompatible-artifact fallback.
- Evidence Aggregation, RCA v2 and Historical Evidence completed, with persisted
  `diagnostic_evidence` equal to the response. Bounded emitted events are not a
  bound on scored or persisted windows.
- Exact native-import/file-upload parity checks cover **every field of every
  sample**: RPM, TPS raw/voltage, battery, ECT, IAT, timestamp, sequence, reserved
  bytes, checksum/frame validity and quality flags. Direct native analysis also
  matches every IF prediction/score, persisted feature/contextual value, aggregate
  window summary and capability status. Any mismatch fails the script.
- Research-only speed provenance covers all 17,976 frames, original source SHA,
  native byte 19 and record-order alignment. Candidate speed is not added to
  production ML features and is not claimed GPS-calibrated.
- Separate real loopback HTTP measurement: **32.37 s, 355.57 MiB peak server RSS**.
  The corpus parity worker retains extra canonical copies, verifies persistence
  and performs a second native analysis for 018; its time/RSS must not be confused
  with upload-only latency: **57.91 s, 619.18 MiB** in the final 018 worker run.

## All-18 Smoke

The machine-readable source of exact per-file SHA, input/canonical counts,
duration, windows, decoder version, detector statuses/scored/skipped counts,
processing seconds, peak RSS, parity assertions and errors is:

`data/research/raw1_upload/summary.json`

Each `data/research/raw1_upload/session_*.json` additionally contains complete
diagnostic evidence and hashes of the actual persisted analysis/windows/features
from isolated temporary repositories. No production sessions were inserted.

| Original | Frames = canonical | Duration (s) | Windows = IF scored | Contextual scored / skipped |
| --- | ---: | ---: | ---: | ---: |
| 000001 | 117 | 28.999 | 7 | 7 / 0 |
| 000002 | 5,781 | 1,446.503 | 574 | 572 / 2 |
| 000003 | 1,249 | 312.499 | 120 | 120 / 0 |
| 000004 | 3,017 | 755.001 | 297 | 297 / 0 |
| 000005 | 7,851 | 1,963.997 | 781 | 781 / 0 |
| 000006 | 923 | 230.750 | 88 | 86 / 2 |
| 000007 | 6,389 | 1,598.002 | 634 | 634 / 0 |
| 000014 | 1,202 | 300.500 | 116 | 116 / 0 |
| 000015 | 531 | 132.501 | 49 | 49 / 0 |
| 000016 | 17,965 | 4,494.502 | 1,792 | 1,787 / 5 |
| 000017 | 3,492 | 873.500 | 345 | 345 / 0 |
| 000018 | 17,976 | 4,496.001 | 1,793 | 1,785 / 8 |
| 000019 | 2,194 | 548.670 | 215 | 213 / 2 |
| 000020 | 87 | 21.501 | 4 | 3 / 1 |
| 000021 | 3,564 | 891.250 | 352 | 350 / 2 |
| 000022 | 3,021 | 755.001 | 298 | 298 / 0 |
| 000023 | 17,539 | 4,391.172 | 1,749 | 1,665 / 84 |
| 000024 | 7,790 | 1,948.999 | 775 | 772 / 3 |

All 18 passed: **100,688 input/canonical samples; 9,989 generated, persisted and
IF-scored windows; zero IF skips or smoke errors**. Contextual exclusions are
explicit applicability decisions: **9,880 scored and 109 skipped**. The full
sequential corpus verifier took **281.86 s**, including fresh worker startup,
persistence/parity assertions, retries and 018's second reference analysis.
Every original remains unsplit; retries reuse
the same persisted run. Different hashes are not proof of independent captures.
The separate RAW campaign records physical capture independence as unknown.

## Resource And Deployment Caveats

`data/research/raw1_upload/resources.json` records the bounded real HTTP probe:
one full 018 upload, two simultaneous 117/87-frame uploads with separate complete
sessions, concurrent identical retries, and a 26,214,401-byte file rejected with
413 without creating a session. The 25 MiB file limit was not increased. Reads
are bounded to the limit plus one byte; multipart parsing can spool the request
before this handler-level guard, so the edge remains responsible for body limits.

Repository Docker configuration uses one Uvicorn worker, no application request
execution deadline, a 3 s healthcheck HTTP timeout and no Docker memory hard limit.
The local running analyzer has the same single-worker command and no memory
limit. Its bind mount contains data only: this workspace fix is **not yet present
in that image**. No container, proxy, worker configuration or DriveSafe code was
changed or restarted.

Read-only inspection of the current Nginx Proxy Manager configuration found
global 90 s proxy read/send timeouts and a 2000m body ceiling, inherited by the
`drivesafe.top` route to `drisafe-web`. That is not a direct analyzer upload route.
DriveSafe uses two Gunicorn gthread workers with eight threads each, a 120 s
worker timeout, a 60 s analyzer-client timeout and a separate 2 MiB request guard;
its existing integration submits JSON records, not this multipart contract.
Those limits do not constitute validation of a new external full-file workflow.

Synchronous analysis inside the async multipart handler can block the one worker's
event loop. Large concurrent uploads, multiple workers, sustained queuing,
healthcheck behavior under load and captures approaching 25 MiB remain untested.
The byte ceiling is not a demonstrated safe RAM budget. No limit should be raised
from these observations. The historical **36.25 s / 615.82 MiB** smoke and the new
local measurements have different harness overheads; neither is a production
benchmark. Existing model-loading/deprecation warnings remain visible.

## Verification And Reproduction

```bash
.venv/bin/python -m pytest tests --disable-warnings --tb=short
.venv/bin/python scripts/verify_native_upload_corpus.py
.venv/bin/python scripts/verify_native_upload_resources.py
```

Tests: **287 passed, zero failed**. Regression coverage includes native dispatch
on both routes, explicit legacy preservation and text filenames, mixed/ambiguous
input, invalid native frames, strict signal parity, >5,000 samples, no truncation,
idempotent retry, conflicting old decoder identity, byte guards, incompatible
feature-schema rejection, actual selected model/scaler provenance, complete
persistence and the isolated RAW campaign.
Real-model corpus and loopback probes passed separately from pytest. All original
RAW, old campaign sidecars/manifests, existing model artifacts and H2-H9 reference
outputs retained their baseline hashes. No annotation was automatically accepted,
no dataset frozen and no production model retrained or threshold changed.
