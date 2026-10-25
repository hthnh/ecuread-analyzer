# EcuRead Analyzer: current harness and diagnostic pipeline state

Audit date: **2026-10-23, Asia/Saigon (UTC+07)**. Machine provenance timestamps below retain their recorded UTC representation. Scope: Analyzer, the available DriveSafe checkout, running containers, existing artifacts and stored production records.

This was a read-only implementation/status audit. The only intended workspace addition is this requested report. No application code, configuration, models, datasets, database records or deployment were changed. No production reanalysis, training, batch inference or capacity campaign was run.

Evidence labels used throughout:

- **Observed:** inspected source, live configuration/schema, stored records, or a read-only reproduction using stored summaries.
- **Archived validation:** an existing test/report result, not a test rerun during this audit.
- **Inference:** a consequence of the inspected code; explicitly identified where the production input/request itself was unavailable.
- **Unknown:** unavailable producer/runtime information; not replaced with an implementation assumption.

Analyzer paths are relative to `/home/hthnh/ecuread-analyzer`. `DriveSafe/...` means `/home/hthnh/drisafe/...`. Container-qualified paths identify runtime files rather than checkout files.

## 1. Executive technical verdict

**Much of the desired pipeline already exists and runs in the deployed server Analyzer. End-to-end parity is broken primarily by an active RAW JSON request cap, differing analysis producers, and consumer contract defects. A new harness, database, LLM service or training campaign is not the next requirement.**

The important findings are:

1. **The 5,000-record limit is an active request-schema limit on `POST /api/v1/raw/decode-analyze`.** `MAX_RAW_RECORDS = 5000` and the Pydantic list `max_length` reject a larger request with HTTP 422 before decoding or persistence (`app/api/raw.py:17,38,47`). It is not a Model Harness or detector limit. The running OpenAPI schema still advertises `maxItems: 5000`. DriveSafe reconstructs an entire stored RAW session into this endpoint, so its rerun path encounters the limit (`DriveSafe/services/analysis_submission.py:110,152,170,497`).
2. **The server really executes multiple detectors.** Isolation Forest is the primary status/health producer; contextual battery and, for the active native bundle, contextual speed execute as supporting research evidence. Aggregation, RCA v2 and interpretation are connected (`app/services/analysis_service.py:140,171`; `app/services/research_harness.py:79-119`). Stored runs prove three-detector execution. This is more than unused infrastructure.
3. **Contextual ECT is a Guest Mode fixture, not an implemented registered detector.** ECT is an input to Isolation Forest and DriveSafe maintenance rules. The only `contextual_ect` implementation reference found in the audited application trees is the fabricated Guest detector (`DriveSafe/services/demo_data.py:393-410`).
4. **A real large production session has successfully crossed Cloud ingestion but has no stored harness evidence.** DriveSafe session **242**, `80d11ec8-7b8f-5731-a7c3-828b60eb480e`, is tagged `pi_native` / `kline`, contains **12,625** stored samples, and has a completed imported analysis with **1,258** windows. Its result names `iforest-baseline-20260920T091709Z`, whereas the live server advertises `iforest-full18-20261011T135838Z`. Its normalized summary contains neither `diagnostic_evidence` nor detector summaries. This establishes different effective analysis provenance, not a server-wide inability to analyze long sessions.
5. **DriveSafe currently hides actual structured RCA observations and symptoms.** Analyzer emits objects with a `statement` field (`app/ml/root_cause_analysis_v2.py:336-399`); the consumer's text-key list omits `statement` (`DriveSafe/services/analysis.py:1020-1048`). Feeding an existing current-harness summary into the deployed consumer produced **0 displayed observations and 0 symptoms from 6 stored observations and 1 symptom**. Checks still rendered because their `description` key is supported.
6. **Guest Mode's “executed 1 / applicable 0 / coverage full / single detector evidence” is a fixture/consumer inconsistency.** Guest supplies top-level `applicable: true`; the consumer counts the nested `applicability.applicable` shape. Coverage and evidence state are fixture literals. The exact contradictory counts were reproduced without invoking a detector.
7. **RCA is deterministic and already computes the needed interpretation categories.** Live causal hypotheses remain empty because additional causal evidence is gated and the live adapter does not supply it. Empty causes are legitimate; Guest's fabricated cooling diagnosis is not a production acceptance oracle. AI Analyst is a rendering-time summary of the same evidence, not another model/LLM (`DriveSafe/templates/session_detail.html:323-371`).

The next implementation should make the existing RAW rerun path support bounded complete sessions above 5,000 records. Follow with the small consumer normalization repair and explicit propagation/provenance of Pi-generated diagnostic evidence. Detailed acceptance criteria appear in sections 9-10.

## 2. Actual repository, deployment and verification state

### 2.1 Repositories and runtime parity

| Component | Observed checkout | Runtime evidence | Conclusion |
| --- | --- | --- | --- |
| Analyzer | `/home/hthnh/ecuread-analyzer`; branch `main`; HEAD `bbbb2846bead3115023fc15ab6484b0d70acde04`; commit timestamp `2026-10-06T15:13:26Z`; origin `https://github.com/hthnh/ecuread-analyzer.git` | Container `ecu-analyzer`, image `sha256:416d793955fbc4d1719c2dbd9fef8eb6d015d085b82ced0f0928e08f9126feba`; created `2026-10-11T15:12:25Z`, started `2026-10-18T05:57:24Z` | **118/118** checkout `app/**/*.py` files matched container `/app` bytes by SHA256. Current relevant runtime code matches the dirty working tree, not Git HEAD alone. |
| DriveSafe | `/home/hthnh/drisafe`; branch `master`; HEAD `7e951f98843fb601de1ebc1c542dff40188b0626`; commit timestamp `2026-10-07T06:55:55Z` | Container `drisafe-web`, image `sha256:1aff4bdc22950abff8105692ec387471b05ceda42b4076ab61f7ecd0faf21b0a`; created `2026-10-12T13:33:06Z`, started `2026-10-18T05:57:24Z` | **23/23** checked files matched: all Python files under `services`, `integrations`, `routes`, plus `app.py`, `config.py`, `models.py`, session template and session JS. This is targeted parity, not an assertion about every file in the image. |
| Cloud storage | PostgreSQL container `drisafe-db-1` | Direct SELECTs executed inside explicit `BEGIN READ ONLY` transactions | Production session/results visible. No database modifications. |
| Pi analysis producer | No corresponding producer checkout/runtime was identified in the available project checkouts | Cloud stores imported Pi summaries and source metadata; web logs identify `ecuread-pi/0.1` sync requests | Producer source, active artifacts, harness invocation and outbound request bodies were not inspected. Do not infer their parity with the server Analyzer. |

No applicable `AGENTS.md` was found in the audited Analyzer/DriveSafe trees or checked ancestor locations. Both repositories have substantial pre-existing changes. Analyzer changes include active-bundle selection, speed detector, native file dispatch, inference and RCA edits; several are untracked. DriveSafe has uncommitted consumer, sync, rerun, model and template changes. Analyzer also has many pre-existing tracked data deletions. None were repaired or reverted.

Specific HEAD/runtime differences include:

- `app/ml/active_bundle.py`, `app/ml/contextual_speed.py`, `app/ml/model_selection.py` and `app/ingestion/raw_dispatch.py` exist in the working tree/runtime but are untracked locally.
- RAW handling no longer forces the old `_v2_model_dir()` helper; active bundle selection occurs in `AnalysisService` (`app/api/raw.py:67`; `app/services/analysis_service.py:137-146`).
- Inference compatibility failures now yield explicit skipped/unavailable evidence rather than the previous unhandled compatibility exception (`app/ml/inference.py:183-205`).
- Current research orchestration registers speed, includes all detector results in returned summaries, and preserves detector evidence per window (`app/services/research_harness.py:88-95,153-169`). Older stored runs can therefore have a one-entry top-level detector list while their nested evidence contains additional research output. They are not proof that today's runtime registers only one detector.

Container inspection shows Analyzer mounts checkout `data` at `/app/data`; source is copied into the image, not bind-mounted. DriveSafe has no host bind mounts. Thus matching code and model data were verified separately. No VCS revision label proving how either image was built was established.

### 2.2 Enabled configuration and artifacts

Analyzer live values match `docker-compose.yml:1-25`: `DATA_DIR=/app/data`, `MODEL_DIR=/app/data/models`, `MAX_UPLOAD_MB=25`, window size **50**, stride **10**, training API **false**. The minimum status evidence defaults to **10 windows** (`app/config.py:8,66-81`). Neither inspected application container has a Docker memory limit, CPU quota or PID limit set. This does not establish unlimited host resources or capacity.

`GET /health` returned `model_loaded: true`. Read-only `GET /api/v1/model` identified the active IF directory and bundle below; this is stronger evidence than inspecting an unused directory.

| Artifact | Current active version / location | Evidence |
| --- | --- | --- |
| Native bundle | `native-full18-20261011T135838Z` | `data/models/active_bundle.json:2`; `data/models/bundles/native-full18-20261011T135838Z/manifest.json:2-24` |
| IF | `iforest-full18-20261011T135838Z`; bundle `iforest/isolation_forest.joblib`, `robust_scaler.joblib`, `model_metadata.json` | Metadata and live model endpoint; 81 feature names; 9,989 training windows. |
| Contextual battery | `contextual-battery-robust-v1-ef5579cf38a0`; `battery_reference.json` | Three supported contexts; z threshold 4.5; minimum reference support 50 windows and 3 captures. |
| Contextual speed | `contextual-speed-robust-v1-fce8419c1951`; `speed_reference.json` | Ten supported contexts; speed bands 24/39/54 km/h; z threshold 4.5; reference support 50 windows and 3 captures. |
| Other IF artifacts | Root legacy model; `honda_keihin_71_17_v2` baseline; human-normal candidate; older v0.2 directory; pre-full 18 bundle | Present on disk, not all active. `app/ml/model_selection.py:15-34` selects by schema, with an active bundle preferred for v2. |

The active manifest SHA256 is `e84a8350aae4708a9e429a313e90f8ee1a625209705a6df48141133f1a8bd5ae`. This audit independently verified that digest and all five declared artifact hashes. `app/ml/active_bundle.py:33-86` validates hashes, supported schema, feature order and window geometry and refuses invalid active pointers without silently using a legacy model. An invalid active bundle can fail the request before the optional research-error fallback (`app/services/analysis_service.py:137,171`).

Existing model-refresh artifacts describe 18 source files, 100,688 canonical samples and 9,989 windows (`data/model_refresh/corpus_smoke.json`). This is reference-corpus support, not verified fault-detection accuracy or independent healthy ground truth. Speed explicitly retains operator-corroborated, non-GPS calibration provenance (`app/ecu/profiles/honda_keihin_71_17.py:173-178`).

### 2.3 Implemented versus tested versus exercised

| Capability | Source | Existing tests / artifacts | Enabled now | Stored production evidence |
| --- | --- | --- | --- | --- |
| IF inference, windows and health | Yes | `tests/test_model_harness.py`, `tests/test_features.py`, `tests/test_limited_data_gate.py` | Yes | Both imported Pi summaries and server runs; producer details differ. |
| Contextual battery | Yes | `tests/test_contextual_detector.py`; five live research harness cases | Yes when reference/features/context available | Server reruns contain scored battery windows. |
| Contextual speed | Yes | `tests/test_contextual_speed_bundle.py` | Yes for active native bundle | Stored October 12 and October 22 server runs contain scored speed windows. |
| Aggregation / RCA / persisted interpretation | Yes | `tests/test_evidence_aggregation.py`, `tests/test_root_cause_analysis_v2.py`, `tests/test_diagnostic_integration.py` | Yes, supplemental | Exists in several database results; absent from selected long Pi imports. |
| More than 5,000 RAW JSON records | Explicitly rejected | `tests/test_native_upload_compatibility.py:71-82` asserts 422/no session | Cap enabled | Rejection also appears in bounded server logs; request body/session not attributable from access log alone. |
| Long native file / canonical analysis | Yes | Existing 17,976-record probes; 5,001-sample upload test | Separate routes available | Long Cloud sessions prove Cloud batch storage, not that they used these server endpoints. |
| Guest-like text rendering | Partly | DriveSafe tests include statement-shaped fixtures but do not assert their observation/symptom text in the inspected visibility matrix | Consumer enabled | Read-only stored-summary reproduction exposes dropped text. |

Archived `data/model_refresh/analyzer_tests.xml` records **410 tests, zero failures/errors/skips**, timestamp `2026-10-11T14:15:19Z`. Relevant recorded case counts: harness 3; live research 5; contextual battery 4; speed bundle 21; aggregation 5; RCA v2 9; diagnostic integration 7; native upload 21; long-session compatibility 17. `data/model_refresh/completion.json` additionally reports 83 DriveSafe tests and 16 browser combinations. The browser artifact explicitly used disposable SQLite/refreshed capture previews (`data/model_refresh/drivesafe_browser.json`); it is not proof of production page parity. These suites were **not rerun** during this audit.

## 3. Exact 5,000-record limitation and full-path limit inventory

### 3.1 Three different ingress paths

```text
RAW JSON POST /api/v1/raw/decode-analyze
  -> records schema max 5,000 -> 422 if exceeded; nothing analyzed/persisted
  -> raw_records_to_canonical_session -> AnalysisService

Native RAW JSONL file POST /api/v1/sessions or /api/v1/import/raw-session
  -> file byte guard -> representation dispatch -> native canonical v2
  -> SessionProcessor -> AnalysisService

Canonical POST /api/v1/analysis
  -> CanonicalTelemetrySession (no samples count maximum)
  -> ML input validation -> AnalysisService

AnalysisService -> full-session windows/features -> IF
  -> battery + optional speed -> aggregate -> RCA v2/history -> persisted summary/windows
```

There is no shared 5,000-sample execution guard beneath these entry points. For an over-limit RAW JSON request, there is no partial analysis or final successful analysis output: Pydantic returns a validation error. The identical count can enter through a different supported contract, subject to its byte and schema rules.

### 3.2 Limits, scope and exceed behavior

“No application cap found” below means none in the inspected path, not that arbitrarily large inputs are safe.

| Stage / reference | Exact value or configuration | Scope and purpose | When exceeded / data behavior |
| --- | --- | --- | --- |
| RAW JSON list, `app/api/raw.py:17,38` | `MAX_RAW_RECORDS = 5000`; minimum 1 | Per request schema guard. No measured capacity justification or detector requirement is documented beside the constant. | HTTP **422**, `body.records`, `type=too_long`; no decode, windows, analysis or persistence; no truncation. |
| RAW row, `app/api/raw.py:24-38` | `sequence >= 0`; timestamp optional `>=0`; `raw_hex` 1-256 characters; session/device/vehicle/firmware strings max 128; note max 512; representation enum | Per record/request contract integrity | Invalid schema rejected with 422. These are not session-length caps. Native/legacy representation subsequently requires exact 24/29 bytes. |
| Analyzer JSON HTTP body, `app/main.py:28-47`; `app/api/analysis.py:13-21` | No explicit JSON byte guard found; Uvicorn command has no request-body maximum or analysis deadline | Per request. Body/model objects are materialized, not incremental inference | No controlled application oversize response demonstrated for these JSON routes. External memory/network limits remain possible. The file-upload guard does not protect RAW JSON. |
| File upload, `app/api/uploads.py:76-80`; `app/config.py:66,87-89` | `MAX_UPLOAD_MB=25` = **26,214,400 bytes**; reads at most limit + 1 in handler | Per uploaded file, resource guard | Empty =>400; above limit =>**413** before staging/analysis. Rejected whole; no intended record truncation. Multipart parsing precedes this handler; this is not a full HTTP-envelope cap. |
| Storage staging guard, `app/services/storage.py:54-56,74-76` | Same max upload bytes | Per file, defensive storage check | `ValueError`; upload handler maps value errors to 422 if reached there. No incremental analysis. |
| File parser/dispatch, `app/ingestion/raw_dispatch.py:16-65`; `app/ingestion/honda_0x17/adapter.py:376-486` | No record-count maximum; native frame 24 bytes; legacy normalized frame 29 bytes | Per file/record, decode correctness | Format mismatch/mixed representation/malformed JSON rejected. Native importer iterates records but accumulates samples/rows. Dispatch also reads/decodes complete file contents; this is not bounded streaming. |
| RAW import validity, `app/services/session_processor.py:67-84` | No frame-count maximum; native import requires every frame valid/checksum-valid | Per import integrity guard | Invalid native frames or malformed JSONL cause whole-import value error/422, with up to 3 examples shown. Example count does not cap ingestion. |
| Canonical schema, `app/domain/telemetry.py:128-155` | `samples: list[TelemetrySample]`, no `max_length`; supported v1/v2 schema | Per session; schema validation | Bad schema =>422. No count rejection/truncation. Speed must be finite and 0-255 (`:113`); sampling interval/rate must be positive (`:88-90`). |
| ML gate, `app/ml/validation.py:37-79` | Minimum **50** samples and 50 eligible samples at live geometry; unique monotonic sequences; complete finite required core signals; nondecreasing present timestamps | Per analysis correctness/minimum evidence | Validation failure =>422. RAW JSON enables an explicit insufficient-data fallback; canonical endpoint does not (`app/api/raw.py:74`; `app/api/analysis.py:18`). No upper count limit. |
| Window generation, `app/ml/windowing.py:24-72`; `app/config.py:67-74` | Size **50**, stride **10**; checksum-error and invalid-decoded tolerances **0.0** | Per session/window. Full eligible windows only | Bad-quality windows omitted; last incomplete tail not padded or scored. Canonical samples retained. No window-count maximum. |
| Features, `app/ml/features.py:110-161` | Native core **81** features; all eligible windows processed | Per analysis, model feature contract | No batch/window cap. Accumulates a complete feature DataFrame; numeric cleanup is not record truncation. |
| Harness, `app/ml/harness.py:183-235` | Sequential detector loop; no sample/window maximum, timeout or cancellation budget | Per analysis/detector isolation | Disabled/missing-feature detector skipped; detector exceptions captured as failed. No count clipping, implicit pagination or incremental execution. |
| IF/battery/speed, `app/ml/iforest_detector.py:56-77`; `app/ml/contextual_detector.py:172-218`; `app/ml/contextual_speed.py:21-49,106-144` | IF scores whole matrix; battery/speed visit all eligible feature windows. Speed requires at least 5 timestamps, strictly increasing time and complete finite speed in 0-255 per candidate window | Per detector/window applicability | Unsupported contexts/missing speed yield no applicable score, not truncated session. Missing core model features skip detector. No 5,000-record detector limit. |
| Session status, `app/config.py:8,78-80`; `app/ml/scoring.py:78-85` | Minimum **10 scored windows**; attention ratio >=0.15 or health<50; monitor ratio >=0.02 or health<80 | Per IF session result; evidence-strength/status rules | Below window minimum =>`limited_data`; raw scores preserved. Not an input cap. |
| Analysis persistence, `app/services/analysis_service.py:230-282`; `app/repositories/file_repository.py:94-106,198-227,280-333` | No session/window count cap or output byte cap found | Per session/run; whole files written with temporary-file replacement | All samples and windows serialized; disk/resource errors can fail persistence. File replacement is atomic individually, not a demonstrated transaction across all session artifacts. Read pagination loads the whole stored JSONL before slicing. |
| RAW response serialization, `app/api/raw.py:42-44,79-82` | Full canonical session **and** analysis summary | Per response | No output pagination/byte cap; response memory and network time grow with session. Canonical analysis endpoint returns summary only. |
| Evidence summary, `app/services/research_harness.py:107,315-321`; `app/ml/evidence_aggregation.py:407-416` | Representative selection max **5 per event type**; exposed `events.items` sliced to **15** | Per analysis, bounded explanatory summary | Events omitted from summary intentionally; samples/windows are not omitted. RCA consumes the representative selection, not necessarily every event; 15-item serialization occurs after representative selection. |
| Read API pages, `app/api/sessions.py:24,39,69`; `app/api/analysis.py:36` | Default **100**, maximum **5,000**, offset >=0 | Per GET page; response bounding | Requesting `limit>5000` =>422. Use offsets for later records. This is pagination, not storage/analysis truncation. Session listing separately max 200 (`app/api/sessions.py:12`). |
| DriveSafe ingress bytes, `DriveSafe/config.py:47`; `DriveSafe/app.py:231-235` | Live `MAX_CONTENT_LENGTH=2097152` (**2 MiB**) | Per inbound web request, including Pi sync | **413**, `REQUEST_TOO_LARGE` for API. Does not govern DriveSafe's outbound request to Analyzer. |
| Cloud batches, `DriveSafe/config.py:48`; `DriveSafe/services/pi_sync.py:381-385`; `DriveSafe/services/telemetry.py:283-288` | Live `MAX_LOG_RECORDS=1000` | Per Pi sync / legacy logs-upload batch; bounded ingestion | Above 1,000 =>validation error/**422**; no batch truncation. Accepted batches accumulate into one session with deduplication. Actual 12,625-sample session used 22 batches. |
| RAW frame representation in Cloud, `DriveSafe/config.py:60`; `DriveSafe/services/telemetry.py:95-99`; `DriveSafe/services/pi_sync.py:23-24` | General RAW frame max 16,384 bytes; Pi contract native24 specifically | Per record integrity/size | Invalid record rejected; unrelated to total session length. |
| DriveSafe → Analyzer, `DriveSafe/config.py:71`; `DriveSafe/integrations/analyzer_client.py:62-80` | Source default timeout **2.5 s**; live env **60 s** | Per outbound request; requests timeout, not an Analyzer compute deadline | Timeout raises `AnalyzerClientError`; rerun marked failed (`DriveSafe/services/analysis_submission.py:507-513`). Server processing may already have persisted work; no cancellation contract was found. |
| DriveSafe worker | Live Gunicorn command: `--timeout 120 --graceful-timeout 30 --keep-alive 5`, 2 gthread workers / 8 threads | Worker liveness/lifecycle, not a per-analysis sample cap | Potential worker failure on timeout conditions; no evidence that these values certify long analyses or define a hard gthread request execution deadline. |
| Local reverse proxy, container `nginx-proxy-manager:/etc/nginx/nginx.conf:33-39` | connect/send/read timeouts **90 s**; `client_max_body_size 2000m` =2,097,152,000 bytes | Per proxied request/inactivity policy, broad proxy defaults | Oversize =>413; upstream timeout can fail response. DriveSafe's2 MiB app limit is tighter. Host config `/data/nginx/proxy_host/5.conf:12-20,53,58` targets `drisafe-web`, no tighter host override found in inspected includes. This proxy does not sit in the direct Docker DriveSafe→Analyzer URL. |
| Cloudflare/public edge | No effective account/plan limit established | External transport | Exact edge upload/time ceilings unknown; not inferred from local Nginx. |
| DriveSafe local screening, `DriveSafe/services/analysis.py:33,241-245` | `MAX_ANALYSIS_RECORDS=100000` | Per UI local analysis read, performance bound | Reads first 100,000 by sequence; later telemetry remains stored but is omitted from that local calculation. Not relevant to the selected 12,625-sample session. |
| DriveSafe maintenance baseline, `DriveSafe/services/analysis.py:559-575` | Up to **20** clean prior sessions from 60 candidates; first **5,000** records each | Per prior-session baseline read, UI compute bound | Bounded subset; later prior-session samples omitted from baseline. This is a second, active 5,000 value, not a harness/input cap. |
| Charts/details, `DriveSafe/routes/dashboard.py:555-557`; `DriveSafe/services/analysis.py:248-255,1020,1383`; `DriveSafe/templates/session_detail.html:484-485` | Chart default 1,200/max 5,000 points; statement sections max 6 items; detector rows max 12; recent-record table max 30 | Per UI presentation | Downsamples/bounds displayed content; preserves stored records. These limits do not explain missing `statement` text. |
| Memory/concurrency | No configured Docker memory/CPU cap in either application; no Analyzer admission control found in inspected path | Process/server resource exposure | Full in-memory ingestion, feature extraction, detector copies, aggregation and serialization can exhaust capacity. No production stress test performed. |

**Classification:** **A** is proven (active request cap). It has a plausible **D** guard role, but a validated safety rationale for exactly 5,000 was not found. **B** and **C** are false for the inspected harness/detectors. **E** is false: it is still live. GET page limits and maintenance-baseline limits are separate active uses of 5,000.

### 3.3 Existing long-session evidence and its limits

- `tests/test_native_upload_compatibility.py:56-68` checks 5,001 samples accepted as a file, all persisted,496 windows, final sequence/timestamp retained. `:71-82` separately checks the JSON list rejects 5,001 and creates no session.
- `data/evaluation/long_session_compatibility/smoke_report.json` records 17,976 native samples and 1,793 windows; native pipeline PASS, but overall compatibility FAIL because RAW JSON returned 422 and the then-file path decoded with the wrong v1 contract. It predates the file-dispatch correction. Its overall FAIL must not be quoted as today's universal upload behavior.
- Later `data/research/raw1_upload/resources.json` records a corrected 17,976-frame file upload, 17,976 canonical samples, 1,793 windows, **32.365 s**, **355.57 MiB** peak RSS, PASS; a 25 MiB+1 input was rejected 413. Its declared scope is one loopback worker, one long upload and tiny concurrency/retry probes. It is not production or public-edge capacity certification. The earlier native probe recorded about 26.894 s analysis and 615.82 MiB RSS under a different scope; these are observations, not safe upper bounds.
- `data/model_refresh/corpus_smoke.json` subsequently records all 18 files processed with the refreshed bundle. No equivalent archived proof establishes the selected production Pi producer executed today's harness.

Removing the list bound alone would expose an uncapped JSON body, full canonical response and synchronous memory-heavy path. Splitting one ride into independent 5,000-record analyses also changes semantics: with 50/10 geometry, four windows crossing each aligned split are lost, and evidence/status/RCA/history become separate sessions. Preserve a single session/window identity and existing geometry in the fix.

## 4. Detector inventory and execution matrix

There is no separate global plug-in registry. `ModelHarness.register()` appends detector objects; orchestration sites define actual registrations (`app/ml/harness.py:174-185`). Primary inference registers IF; research orchestration adds battery and optional speed. Evaluation code additionally registers the temporal candidates (`app/evaluation/temporal_candidate_revision.py:212-223`).

| Detector | Implemented | Enabled in server analysis | Required signals/features | Observed execution status | Output | Current limitations |
| --- | --- | --- | --- | --- | --- | --- |
| `isolation_forest` | Yes | Yes when `process_with_model=true` and compatible artifacts exist | Native RPM, TPS voltage/raw, battery, IAT, ECT; 13 statistics per signal +3 relationships =81 features, in metadata order | Current stored runs `ok`; large Pi result reports model scored 1,258 windows but has no detector execution object | IF sample/decision scores, ±1 prediction, anomaly flag, internal window/session health, unusual features | Multivariate statistical deviation; no fault probabilities; health/overall status remain IF-only. Robust feature ranking is not causal explanation. |
| `contextual_battery_voltage` | Yes | Yes when reference/features available; active bundle supplies reference | `rpm_median`, `tps_raw_median`, `tps_voltage_median`; battery median/mean/min | Stored 87-sample current run: 3/4 windows scored, 3 positive; Oct 22 run: 134 scored, 0 positive | Max absolute robust z, 4.5 threshold, context/reference/observed delta, applicability, anomaly flag | Three modeled RPM/TPS contexts only; no ECT/speed causality. Reference distribution is not confirmed health. Legacy v1 feature names can make it skip. |
| `contextual_speed` | Yes | Yes when active bundle contains speed reference | Complete speed/time in each core window; speed median/rate/dispersion/stationary fraction plus RPM/TPS medians | Oct 12 stored 87-sample run: 3/4 scored, 0 positive; Oct 22: 134 scored, 0 positive. Oct 11 stored run explicitly skipped speed | Detector-local z, threshold, reason, supported-context/reference counts, raw motion features | Unsupported contexts/missing or nonincreasing time skip windows; no GPS calibration, fixed CVT ratio, road-limit or component-fault claims. No bundle speed artifact =>no speed registration/row in this path. |
| `temporal_battery_shift` | Yes | **No live registration**; evaluation only; explicitly excluded by default aggregation contract | Window identity; RPM/TPS medians; battery median | No live execution established | EWMA shift/persistence evidence, local applicability and events | Candidate, not a missing enable switch to turn on casually; resets at context/gap changes, needs warm-up. |
| `temporal_rpm_stability` | Yes | **No live registration**; evaluation only; explicitly excluded by default aggregation contract | Window identity; RPM median/std/range/mean-absolute-diff; TPS raw/voltage medians | No live execution established | Robust temporal dispersion/persistence evidence | Closed-throttle-running applicability; needs history/persistence; candidate exclusion remains deliberate. |
| `contextual_ect` | **No detector implementation found** | No | Guest implies ECT/RPM/TPS, but defines no production feature/reference contract | Guest hard-coded `status=ok` only | Fixture literals: score 0.84, threshold 0.75, elevated-ECT observation/cause/check | Missing actual detector and validation; existing IF ECT features and cooling maintenance rules do not establish a contextual ECT detector. |
| `baseline_screen` | Guest fixture only | No Analyzer registration | None defined by fixture | Guest literal negative | Fixture no-evidence state | UI demonstration, not an additional production detector. |

### 4.1 Actual decision and failure behavior

- **IF:** scaler transforms the full feature matrix, then `score_samples`, `decision_function`, `predict`; anomaly is prediction−1 / decision score<0 (`app/ml/iforest_detector.py:56-81`; `app/integration/diagnostic_evidence.py:179-186`). Health maps training score quantiles and aggregates median/lower-decile/ratio terms (`app/ml/scoring.py:18-47`). It is explicitly an internal score, not failure probability. No windows/missing artifact/schema mismatch produce explicit skipped/unavailable results (`app/ml/inference.py:132-205`). Harness errors are isolated.
- **Battery:** RPM<500 =>engine-off/transition; modeled idle uses RPM<1,800/TPSraw<=15/TPSvoltage<=0.85; low load<4,500/45/1.65; mid load<7,500/115/3.50 (`app/ml/contextual_detector.py:41-58`). Compare each battery feature to capture-balanced reference median using its robust scale; maximum z>=4.5 flags (`:172-218,251-282`). Unsupported windows are `skipped_context`; zero supported windows makes the detector skipped/`no_applicable_context`. Missing/bad reference is explicitly skipped/failed (`app/services/research_harness.py:195-225`).
- **Speed:** uses the same eligible core window boundaries, five elapsed-time median blocks and derived rates, then stationary/transition/moving + speed-band + RPM/TPS context (`app/ml/contextual_speed.py:21-59`). Temporal evidence is max(rate-z,dispersion-z); moving relationship evidence is min(RPM-z,max(TPS-voltage-z,TPS-raw-z)); max of those>=4.5 flags (`:121-128`). Zero supported windows =>`no_supported_speed_context`. It is statistical operating-pattern evidence.
- **Temporal candidates:** battery defaults alpha 0.3, 8 warm-up windows, 4 persistence windows, 0.4 V shift, maximum start-frame step 15 (`app/ml/temporal_detector.py:30-37`). RPM defaults engine-running>=500, closed TPSraw<=5/TPSvoltage<=0.65, 8 warm-up/4 persistence, z3, minimum dispersion increase 250 RPM, scale floor 100 RPM (`app/ml/rpm_stability_detector.py:31-42`). They are implemented/tested research candidates, not active production findings (`app/ml/evidence_aggregation.py:19-22,58-64`).

All successful detector results use `DetectorResult` with their own score direction, scored/anomaly counts, metadata and evidence fields; skipped/failed results are not “normal” (`app/ml/harness.py:28-134,188-234`). Aggregate applicability is evaluated per window from `status=ok` and a non-null anomaly flag (`app/ml/evidence_aggregation.py:153-167`). Scores are preserved separately; there is no universal score fusion/voting.

The research call currently occurs even when `process_with_model=false`: only primary IF is bypassed (`app/services/analysis_service.py:140-185`). Therefore that flag is **not a global no-inference guarantee**. No production request using that flag was sent in this audit.

### 4.2 Execution, applicability, coverage and agreement semantics

At window level, coverage full means all configured detectors are applicable; partial means some; none means zero. One positive with no negative gives single-detector evidence; positive plus negative gives disagreement; more than one positive without a negative gives multiple-detector evidence (`app/ml/evidence_aggregation.py:281-298`). Full coverage and single-detector evidence can be legitimate for a registry containing one detector. Zero applicable with full coverage cannot be justified by those window semantics.

There are additional session-summary inconsistencies:

1. `aggregate_from_detectors()` counts a detector applicable when it scored any window, while the session coverage label becomes **full if any window has full coverage** (`app/integration/diagnostic_evidence.py:124-145,198-225`; `app/services/research_harness.py:648-656`). In the 87-sample Oct 12 run, the summary says full although the window distribution is 3 full /1 partial. This is an optimistic session label, not all-window coverage.
2. Session evidence state is overridden from window state priority, but `detector_disagreement.present` remains the earlier any-positive-per-detector session result (`app/services/research_harness.py:283-292,638-645`). The archived 17,976-sample report actually has `evidence_state=detector_disagreement`,117 disagreement windows, but `present=false` and an empty session negative list because both detectors were positive somewhere. These fields have incompatible scopes.
3. `both_applicable_agreement_windows` now operates with a three-detector policy; its old name is misleading. Its implementation counts full-coverage states `no_evidence` or `multiple_detector_evidence`, rather than reporting explicit pairwise agreement (`app/ml/evidence_aggregation.py:379-400`).

These are presentation/contract work, not reasons to change model predictions.

## 5. Diagnostic Evidence contract and transformation status

### 5.1 Connected data flow

`AnalysisService` validates canonical telemetry, builds all eligible features and IF output, calls the research harness, adopts its windows/detector summaries/evidence, and writes the resulting run (`app/services/analysis_service.py:127-188,251-282`). Research orchestration then:

1. Reuses IF result and executes contextual detectors.
2. Normalizes per-window findings with detector versions, local scores/thresholds, applicability, errors and evidence.
3. Aggregates flags, coverage and relationships; groups finding/coverage regions.
4. Selects representative events and constructs RCA v2 cases from their actual detector findings and telemetry (`app/services/research_harness.py:419-489`).
5. Builds observations, symptoms, optional causes and checks; deduplicates sections; adds available historical context/check priority.
6. Returns `diagnostic_evidence` with schema `analyzer-diagnostic-evidence-v1`, analysis version `h9-drive-safe-diagnostic-evidence-v1`, live version `h9c-live-research-harness-v1`, versions/provenance and production boundary (`app/services/research_harness.py:262-355`).

The fallback `build_diagnostic_evidence()` deliberately marks interpretation/history unavailable. Its old text saying RCA is not computed on the production-compatible path describes that fallback, not successful current research execution (`app/integration/diagnostic_evidence.py:59-120`; `app/services/analysis_service.py:189-205`).

### 5.2 Classification of requested output

| Output | Actual production-capable implementation | Classification | Contract / limitation |
| --- | --- | --- | --- |
| Observations | IF, battery, speed, RPM/TPS involvement, constant-feature, aggregate/disagreement facts | **Dynamic telemetry/detector evidence + deterministic rule-selected text templates** | RCA objects use `statement`, `evidence`, `claim_level`, provenance. Current DriveSafe text adapter drops `statement`. |
| Symptoms | Contextual supply-voltage deviation when supported by positive battery evidence | **Deterministic evidence-grounded interpretation** | Limited symptom catalog; no demonstrated contextual ECT symptom generator. Current consumer also drops these statement objects. |
| Possible causes | Electrical supply-path, unexplained operating-state and acquisition-integrity candidates gated on extra causal evidence | **Implemented deterministic gated hypotheses; disconnected from additional live evidence** | No generic observation-to-fault mapping or calibrated cause ranking. Guest cooling/battery causes are fabricated strings. |
| Recommended checks | Catalog descriptions triggered by IF feature review, voltage context, external measurements, RPM/TPS repeatability, integrity and coverage/disagreement | **Rule-selected preconfigured templates grounded in actual findings** | Live checks can exist with zero causes; correct behavior. Minimal knowledge base, not open-ended diagnosis. |
| Confidence/support | Claim levels; required/limiting evidence; detector-local scores; reference capture/window support | **Dynamic support + deterministic qualification** | No calibrated diagnostic confidence percentage; RCA policy forbids invented percentages (`app/ml/root_cause_analysis_v2.py:30-48`). |
| Technical evidence | Scores/directions/thresholds, versions, feature deltas, window boundaries, detector outcomes/errors | **Dynamically derived** | Summary and per-window contracts exist; summary is intentionally bounded. |
| Coverage/disagreement | Per-window and session aggregates | **Dynamically derived, with summary semantic defects** | Use window counts/distributions when assessing complete coverage; see4.2. |
| Signal provenance | Decoder/version/schema; speed byte19/calibration; signal definitions | **Persisted decoder contract + telemetry metadata** | IAT/ECT remain `provisional_high_confidence` in v2 definitions (`app/domain/telemetry.py:53-59`); no inference from a UI label to calibrated truth. |
| History/check priority | Existing H8 observation index + H2 evaluation manifest, same-vehicle/context/provenance comparisons, cutoff metadata | **Deterministic comparison against precomputed research artifacts** | Files must exist; it is not automatically an exhaustive continuously updated production history service. No history can create a cause. |
| AI Analyst narrative | Summary sentences about detector outcomes, aggregate and causes | **Rendering-time deterministic template** | Reuses evidence, currently narrates at most first 2 detectors; speed can be absent from narrative even when executed. |
| Maintenance recommendations | DriveSafe per-vehicle baseline/rule findings, including existing cooling/battery guidance; optional speed finding | **Separate dynamic deterministic UI computation** | Not identical to Analyzer RCA; Guest uses fixtures. |
| Anomaly timeline | DriveSafe embedded-record analysis when present; otherwise local statistical scoring/grouping | **Dynamic DriveSafe computation** | Not a guaranteed projection of Analyzer persisted window events. The full production timeline was not recomputed during this audit. |

### 5.3 Persisted data and actual contract defects

**Persistence is implemented.** Analyzer stores summary/evidence in `data/analyses/<run>/analysis_run.json`; samples in `data/telemetry/<session>/samples.jsonl`; detector window payloads in `analysis_windows.jsonl`; feature/evidence data in `window_features.jsonl` (`app/repositories/file_repository.py:198-227,280-333`). File-upload results additionally expose top-level `diagnostic_evidence` and `model_bundle` (`app/services/session_processor.py:198-242`). The RAW endpoint instead returns `{canonical_session, analysis}`; canonical analysis returns the summary directly. Consumers must respect these different envelopes.

DriveSafe persists normalized summaries in `AnalysisResult.result_summary`; server reruns can additionally store `canonical_telemetry` (`DriveSafe/models.py:424-448`; `DriveSafe/services/analysis_submission.py:21-53`). Pi sync accepts an optional JSON-object diagnostic evidence block and preserves it (`DriveSafe/services/pi_sync.py:122-132,313,357-358,600-618`). It does **not** preserve arbitrary extra top-level Pi analysis fields: `detectors` and `model_bundle`, for example, are not copied by the whitelist at `:330-358`. Nested versions/bundle in `diagnostic_evidence` are therefore particularly important for this integration.

Observed missing transformations/contracts:

- **RCA text:** Analyzer `statement` objects versus DriveSafe `_statement_items` accepted keys. Read-only current-summary reproduction confirms loss. DriveSafe visibility tests supply statement objects at `DriveSafe/tests/test_app.py:2005-2006` but assert state/narrative rather than these strings at `:2051-2064`; historical test success does not close this gap.
- **Guest detector shape:** top-level `applicable`, `local_score`, `threshold` versus nested canonical applicability/local-score/threshold objects. This explains incorrect applicability counts and incomplete technical score display in Guest. Guest schema literal `diagnostic-evidence-v1` also differs from actual Analyzer evidence schema.
- **Cause evidence:** live `_rca_case()` never adds `additional_causal_evidence`; all current causal gates depend on that field. This is an input gap, not missing RCA infrastructure.
- **History:** research index/manifest is used, not guaranteed live database history. A stored `history.status=available` must be read together with comparable-session counts and cutoffs.
- **Event identity:** session interpretation deduplicates by rule-like observation/symptom/check IDs, retaining the first item of each ID (`app/services/research_harness.py:380-392,755-768`). Multiple events of the same type do not retain separate statement provenance in that flattened list; representative events remain a different bounded view.
- **Availability:** missing evidence is mapped to `legacy_session` and claims analysis happened “before the research harness was enabled.” That explanation is not established for recent Pi imports; they were imported after the server had harness support (`DriveSafe/services/analysis.py:1415-1428`). A generic missing-producer-evidence state would be more accurate.

**Answer on structural parity:** the server pipeline can already emit nearly all structural categories, but deployed DriveSafe does not yet display them faithfully; long Pi sessions lack that block entirely; contextual ECT and populated causal hypotheses cannot be claimed from Guest screenshots.

## 6. RCA and AI Analyst status

| Question | Evidence-based answer |
| --- | --- |
| Does RCA consume structured detector evidence? | **Yes.** Representative aggregate findings/telemetry become cases, then `RootCauseAnalyzerV2.analyze_case()` evaluates them (`app/services/research_harness.py:396-489`; `app/ml/root_cause_analysis_v2.py:147-165`). |
| Does it map observations to possible causes? | **Only through specific gated deterministic rules.** It emits observations/symptoms directly from detector evidence and may emit cause candidates when external/physical/operator/integrity evidence exists. It is not an unrestricted causal inference model. |
| Does it rank/qualify causes? | **Qualifies**, using claim levels, supporting/limiting evidence, maximum claim level and verification steps (`app/ml/root_cause_analysis_v2.py:376-399`). No live numeric probability/ranked causal distribution. Older RCA v1 exposes hypothesis statuses, but v2 is the live engine; v1 remains reproducibility/evaluation code (`app/ml/root_cause_analysis.py:39-62`). |
| Are checks grounded? | **Yes**, selected by detector/evidence rules with provenance and a minimal predefined check catalog (`app/ml/root_cause_analysis_v2.py:52-122,174-191,252-288,402-405`). History adds priority; it does not confirm causes. |
| Is an LLM involved? | **No in the audited diagnostic path.** RCA declares `llm_used=false`; implementation is local Python rules. No LLM client call was found in the audited Analyzer/DriveSafe diagnostic components. This statement is not about an uninspected remote Pi implementation. |
| Where is interpretation computed? | RCA sections in **Analyzer**. AI Analyst narrative and maintenance/local-screening views in **DriveSafe**. Imported Pi results originate outside this inspected server process. |
| Persisted or page render? | Analyzer RCA interpretation persisted with run; Cloud retains evidence when supplied. DriveSafe analyst narrative is regenerated from that stored block during view construction; separate maintenance/local scores are computed on read. |
| Redundancy? | **Yes.** AI Analyst includes the same detector rows and checks already shown under diagnostic evidence. Template expressly says it uses the same stored evidence and no second computation (`DriveSafe/templates/session_detail.html:328,338-371`). Maintenance is a different computation and may overlap conceptually without shared event identity. |

Current causal gates are exact: electrical requires `external_voltage_measurement` or `physical_electrical_inspection` (`app/ml/root_cause_analysis_v2.py:436-451`); operating-state requires `operator_context_unexplained` (`:454-467`); acquisition requires one of invalid timestamps/repeated RAW/checksum-header failures/implausible saturation/missing stimulus response (`:470-490`). `additional_causal_evidence()` reads explicit case data (`:573-574`), which live `_rca_case` does not populate. Thus **the current live adapter cannot activate those cause gates**, even though the rule engine can do so in appropriately supplied cases/tests. Do not confuse missing additional evidence with proof of no possible physical cause.

The live constructor is `RootCauseAnalyzerV2()` with no source registry/grounding matrix arguments (`app/services/research_harness.py:404`); bibliographic grounding IDs are consequently not guaranteed to be populated. Local rule/event provenance still exists. Existing deterministic interpretation is sufficient for observations, a limited symptom set and useful checks. A separate AI Analyst computation or LLM dependency would not repair absent Pi evidence, a request rejection or a dropped text key.

## 7. Real production session end-to-end trace

### 7.1 Selected stored session

Selected **DriveSafe `ride_sessions.id=242`**, session ID **`80d11ec8-7b8f-5731-a7c3-828b60eb480e`**, declared `source_type=pi_native`, `source_kind=kline`, RAW `native24_table17`. It is the available12,625-sample non-replay-tagged capture requested by the audit. “K-line” is persisted source provenance; hardware capture authenticity was not independently revalidated here.

| Stage | Observed stored state / exact visibility |
| --- | --- |
| Input count | `ride_sessions.record_count=12625`; actual `telemetry_records` count=**12625**; sequences0-12624. |
| Canonical data | All 12,625 rows have timestamp_ms, RPM, TPSvoltage, TPSraw, battery voltage, IAT, ECT, raw_hex/raw_frame and candidate `vehicle_speed_kmh`; all 12,625 frame/checksum/decoder validity flags true. Decoder `honda_keihin_71_17:1.0.0`, schema `canonical-telemetry-v2`. Presence/flags are observed; no fresh RAW decoding was run. |
| Cloud transfer | **22 accepted sync batches**, per-batch received count132-599, inserted total12,625, duplicate total0; full sequence span. This is incremental transport/persistence into one session, not incremental server Model Harness analysis. |
| Analysis ID | `87f16d88-91af-476e-af77-32c079637b36`; database analysis result **id 80**; completed. |
| Analysis time | Stored `2026-10-21T15:18:58.157473Z` = **2026-10-21 22: 18: 58, Asia/Saigon**. |
| Model/version | `iforest-baseline-20260920T091709Z`; feature schema `ecu-window-features-v1`; model_loaded true. No stored bundle hash, detector versions or artifact hash for this result. |
| Windows | **1,258** recorded; anomaly windows 86; ratio0.068362; health85.48; overall `monitor`; evidence count1,258; minimum10; sufficient=true. This equals `floor((12625-50)/10)+1`; model's actual remote window implementation was not inspected. Under server geometry last full window would be12570-12619 and tail5; that boundary is an inference, not a stored window trace. |
| Unusual features | `ect_c_delta`, `ect_c_slope`, `ect_c_first`, `battery_voltage_median`, `tps_voltage_min`. ECT involvement is IF feature evidence, not execution of `contextual_ect`. |
| Harness execution | **Not recorded / cannot verify.** No top-level detector execution list or nested diagnostic evidence in normalized stored summary. IF model scoring is reported; do not turn that into “one detector executed” as an observed harness counter. |
| Executed/skipped/unavailable detectors | **Unknown for producer.** No recorded battery/speed/ECT status. Missing evidence does not mean detector-negative, disabled, or skipped. |
| Diagnostic evidence/RCA | **Absent** from stored result. No observations/symptoms/causes/checks/aggregate coverage or disagreement persisted for this run. |
| Persisted interpretation | No RCA/analyst block. Only IF summary fields and note: internal normalized health is not failure probability. |
| Canonical analysis blob | `AnalysisResult.canonical_telemetry` null; individual telemetry rows exist. Cloud Pi importer does not require/store that blob (`DriveSafe/services/pi_sync.py:600-618`). |
| Server Analyzer copy | No matching run directory/session entity was found under the currently available Analyzer data. The Cloud result is imported Pi evidence, not a traceable local server run. |
| Consumer evidence view | Direct read-only execution of deployed `diagnostic_evidence_view(stored_summary, analysis_status='completed', has_analysis_result=True)` =>availability `legacy_session`, no evidence state, no technical detector rows, observations/symptoms/causes/checks empty; analyst repeats the missing-harness message. |

The stored normalized Cloud analysis contains exactly these groups: IDs/model/schema/decoder/profile; six `signal_columns`; window/anomaly counts and ratio; health/overall status; unusual features; model_loaded; warnings=[]; note; analyzed_at; evidence_window_count/minimum/evidence_sufficient. **It contains no `diagnostic_evidence`, `detectors`, `model_bundle`, or persisted interpretation.**

The current Pi importer preserves optional valid diagnostic evidence but whitelists other fields. Therefore the narrow inference is: **the ingested Pi analysis supplied no usable diagnostic evidence block (absent/null, or otherwise not preserved by the producing version).** Current code would warn on an invalid non-object; the stored warnings are empty. The original outbound body and remote producer code were unavailable, so the exact remote omission point and whether top-level detectors were sent then dropped cannot be proven. No assertion that the remote Pi ran or did not run the new harness is justified.

Two other actual `kline` sessions corroborate the producer pattern:

| DriveSafe session | Samples | Stored windows | Model | Evidence block |
| --- | ---: | ---: | --- | --- |
| 248 / `e136fe0d-4f43-57bd-ba17-79cca06cbea9` | 12,211 | 1,217 | `iforest-baseline-20260920T091709Z` | Absent |
| 249 / `46a37421-aa27-558e-ad5a-8b87df1dcb3d` | 9,917 | 987 | Same baseline | Absent |

### 7.2 Fields actually passed to DriveSafe presentation

The session route calls `session_records`, `analysis_snapshot` and the existing template (`DriveSafe/routes/dashboard.py:446-479`). Source mapping and the stored-summary reproduction establish:

- Primary result/status, health, model/decoder/schema and counts are available from the stored result.
- Harness and AI Analyst panels receive the unavailable/`legacy_session` view above. The template displays the missing-evidence message and a rerun button; causes have an explicit empty presentation. It does not dynamically invent the Guest cooling/voltage narrative.
- Observation/symptom subsections are conditional on text items (`DriveSafe/templates/session_detail.html:133-163`). They have no items for this session.
- Maintenance uses `maintenance_findings` at view time, independently of the absent RCA block (`DriveSafe/services/analysis.py:1937-1943`). It can display rule-based guidance; its exact current per-signal findings were not recalculated in this audit.
- Timeline/anomalous-sample metrics are built from embedded record evidence or local screening (`DriveSafe/services/analysis.py:404-442,1913-1927`); they are not stored IF windows retrieved from the Analyzer. Exact current DOM/timeline output was not browser-verified or recomputed. Do not equate its event/sample counts with the 86 anomalous IF windows.

**First visible divergence from the desired pipeline:** at the producer-to-Cloud analysis contract, the stored result already lacks structured detector/diagnostic evidence, despite complete canonical signals. Transport/storage count is intact. Remote feature/model/harness behavior before that boundary remains unverified. A subsequent rerun of reconstructed RAW through the present server route would encounter the 12,625>5,000 schema rejection; this is a source/schema inference, not a rerun performed during the audit.

The consumer's “before harness enabled” explanation is misleading for this run: the recorded analysis is October21, while the same server/data contain three-detector evidence on October12. Absence of evidence identifies a producer/contract/provenance problem, not that date-based explanation.

### 7.3 Independent proof the server pipeline is exercised

Existing server run `analysis_20261012T133148Z_9807d0063a69` for session `7ff24ebc-f990-52db-ae53-2d183e408f72` is also persisted in Cloud (resultid74, session237). It has87 samples /4 windows: IF4 scored/4 positive; battery3 scored/3 positive; speed3 scored/0 positive; 6 observations, 1 symptom, 0 causes, 8 checks. Aggregate is disagreement; 3 windows have full coverage and 1 partial, although the session label says full. The sibling Oct 12 run `analysis_20261012T141946Z_e49a483543ba` has the same interpretation counts; deployed consumer reproduction rendered0 observations/0 symptoms and 8 checks from it.

Existing Oct 22 run `analysis_20261022T120810Z_1fb59abab17d` for `calib-real-run-session-000007-part-02` is persisted in Cloud session210: 1,389 samples,134 windows, IF/battery/speed all `ok`, all 134 negative, 1 aggregate observation, 0 symptoms/causes, 1 boundary check. This proves live server execution/persistence without inferring anything about remote long-session harness execution or treating these other captures as fault-validation data.

Database-wide read-only snapshot: **59 stored sessions; 65 analysis-result rows**. Pi-native results10, 3 with diagnostic evidence; ESP-RAW results55, 2 with evidence. These are historical result-row counts, not distinct-current-session coverage percentages. They demonstrate sparse adoption of the new evidence contract, not missing storage capability.

Logs inspected for `2026-10-21T00:00Z` through `2026-10-23T00:00Z` show 63 Pi sync POST entries and two server RAW analysis requests, one 422 and one 200. Access logs do not include enough body/session context to attribute the 422 to the selected session or prove its rejection reason; the live schema and existing over-limit test establish the cap independently.

## 8. Guest Mode versus production gap

### 8.1 Reproduced Guest counter inconsistency

`DriveSafe/services/demo_data.py:393-433` selects static evidence by `result_hint`. Attention Guest includes a fabricated `contextual_ect` positive and cooling cause/check; monitor Guest includes a fabricated battery positive and cause/check; normal Guest contains `baseline_screen` negative. Their full-coverage/evidence-state labels are literals.

Deployed consumer read-only reproduction for attention and monitor Guest returned:

| Value | Reproduced display | Reason |
| --- | --- | --- |
| Executed detectors | 1 | `status=ok` is accepted by `_detector_rows` (`DriveSafe/services/analysis.py:1347`). |
| Applicable detectors | 0 | Fixture has top-level `applicable:true`; `_detector_applicability` reads nested `applicability` and falls back to text `True`, not label `Applicable` (`:1256-1268`). Summary counts rows whose label equals `Applicable` when no aggregate count supplied (`:1472-1474`). |
| Coverage | full | Fixture literal, not recomputed consistency check. |
| Evidence state | Single detector evidence | Fixture literal. |

Thus these four Guest values together are **not correct coherent domain semantics**. Correct nested Analyzer payloads did produce3 executed /3 applicable in the same consumer. Execution and applicability remain distinct useful concepts, but that distinction does not excuse this reproduced mismatch.

### 8.2 Capability comparison

“Ready” means fit for this requested integrated behavior, not demonstrated clinical/mechanical diagnostic accuracy.

| Desired capability | Existing implementation | Production-ready? | Missing work | Priority |
| --- | --- | --- | --- | --- |
| Requests over 5,000 RAW records | File/canonical routes support larger sessions; RAW JSON rejects | **No** for existing DriveSafe RAW rerun | Bounded full-session RAW contract; limits/deadline/resource acceptance; preserve window identity | P0 |
| Multi-detector execution | IF+battery+speed in current server orchestration | **Yes server execution; partial fleet integration** | Carry the same evidence/provenance from Pi or existing server rerun; explicit unsupported status | P0 |
| Contextual anomaly evidence | Battery and speed real; ECT Guest-only | **Partial** | Use existing supported detectors; contextual ECT requires separate justified scope/reference validation | P2 for ECT |
| Detector aggregation | Real per-window flags, coverage, disagreement/events | **Partial** | Align session/window scopes and `present` flag; coverage counts/label; three-detector names | P1 |
| Diagnostic observations | Actual structured RCA statements | **No end-to-end** | Read `statement` in consumer; stop fixture-only text assumptions | P0 |
| Symptom interpretation | Limited battery symptom rules | **Partial** | Render current statements; clearly state supported symptom scope | P0 rendering /P2 expansion |
| Possible causes | Gated deterministic candidates; zero live extra causal evidence | **Partial, intentional empty results** | Expose existing evidence limits; add grounded causal inputs only if available/required; never copy Guest causes | P2 |
| Recommended checks | Analyzer rule catalog + priorities; DriveSafe renderer works | **Partial** | Ensure producer sends block, preserve provenance, reduce duplicate presentation | P1 |
| RCA | v2 live; v1 reproducibility; no LLM | **Implemented with bounded domain scope** | Input/consumer fixes; retain claim gates and empty-cause semantics | P1 |
| Evidence persistence | Analyzer run/windows, Cloud JSON, optional canonical rerun blob | **Yes capability; sparse actual adoption** | Version/propagate producer evidence; preserve run identity; record missing-field reason | P0 |
| DriveSafe integration | Existing RAW/canonical clients, sync whitelist, evidence/technical panels | **Partial** | RAW cap, statement mismatch, Pi metadata whitelist/provenance, misleading legacy classification | P0 |
| AI Analyst | Reuses stored evidence and checks | **Adequate deterministic computation** | Fix upstream block/text; show all relevant executed detector scopes; remove repeated check/table content if desired | P1/P2 |
| Maintenance/timeline | Existing independent DriveSafe screening/baselines | **Implemented but different evidence source** | Make source/event identity explicit when compared with Analyzer windows; avoid matching their counts by assumption | P1 |
| Guest/production parity | Guest supplies strings and fabricated detectors/causes | **No** | Canonical fixture schema and real stored-output consumer regression; do not require fabricated ECT/causes | P0/P1 |

## 9. Ranked minimum viable completion plan

No task below was implemented. Effort is relative: S=localized change, M=several connected code/contracts/tests, L=broader evidence/domain work. Tasks 1-4 repair existing paths. Tasks 5-6 address traceability and any explicitly retained domain gap without requiring a new service.

| Order / objective | Required changes | Dependencies | Acceptance criteria | Effort | Primary technical risk |
| --- | --- | --- | --- | --- | --- |
| **1 — P0: support bounded complete RAW sessions above 5,000** | Replace the fixed 5,000-only request contract with a documented finite session envelope appropriate to at least 12,625 and existing 17,976-frame captures; include JSON byte/admission protection and response/deadline behavior. Prefer reuse of current decoder/AnalysisService rather than a new pipeline. Update DriveSafe client validation/error handling for the chosen supported contract. Choose any batching only after preserving session/window boundaries. | Existing decoder, active bundle, API schemas and client; no training dependency. | Offline/disposable coverage for 5,000/5,001/9,917/12,625/17,976 plus over-limit rejection; exact input/canonical/persisted counts; unchanged 50/10 window geometry and crossing-boundary windows; no duplicate analysis on normal retry path; typed oversize/timeout failure; full detector/evidence contract preserved. Bounded single-request time/memory measurement must establish fit against 60 s client and 90 s proxy; this is targeted acceptance, not an unrestricted stress campaign. | M | Unbounded JSON parsing/response memory; deadline mismatch; duplicate/partial persistence after client timeout. |
| **2 — P0: repair DriveSafe evidence normalization and truthful availability** | Accept actual RCA `statement` objects alongside legacy strings; preserve their support/provenance. Normalize Guest top-level applicability to canonical nested shape or update fixtures to real contract. Do not describe every missing evidence block as “before enabled.” | Can proceed independently of 1; existing producer/consumer data contract. | Use existing stored current-harness JSON in a consumer regression: 6 observations/1 symptom displayed, 8 checks retained, 0 causes handled honestly. Guest one-applicable fixture displays 1 executed/1 applicable/full; genuine zero-applicable means no coverage. Recent Pi result without evidence gets an accurate missing-evidence reason. Assertions must verify actual text, not just state badges. | S | Backward-compatible mixed shapes; accidentally treating missing/skipped as normal. |
| **3 — P0/P1: close producer-to-Cloud evidence/provenance gap** | Inspect the actual Pi producer next. Preserve existing canonical diagnostic evidence, detector versions and bundle provenance in upload/normalization; avoid dropping supported top-level metadata silently. For existing records, the already-implemented server RAW rerun can create a new evidence-bearing run after 1; do not require bulk backfill or silently overwrite old analysis identity. | Pi source/runtime visibility for producer changes; 1 for long RAW rerun.2 for visible statements. | Disposable end-to-end contract round-trip from an actual produced summary: detector executions/applicability/skips, aggregate, RCA/checks, model/bundle versions unchanged in Cloud and UI. Existing old result remains addressable; missing evidence is explicit. A separately authorized single stored-session rerun can later demonstrate server-to-Cloud parity; no batch migration prerequisite. | M, uncertain until producer inspected | Remote producer may only run IF; same version strings can mask different artifacts; fingerprint/idempotency conflicts. |
| **4 — P1: align aggregate coverage/disagreement scopes** | Define session versus window count semantics; derive `present` and selected state from compatible scope; expose full/partial/none window fractions; label session “full” only with explicit agreed meaning. Rename/generalize two-detector fields for current three-detector registry. Preserve raw local scores. | Existing aggregation schema; coordinate consumer aliases with 2. | Stored 87-sample example clearly shows 3 full/1 partial, not an unexplained full label; archived 117-disagreement example has consistent state/present/count/ID scopes; all-negative 134-window example remains coherent. IF health/status/predictions unchanged. | S-M | Changing a documented summary label or disagreement scope can surprise historical consumers. |
| **5 — P1: finish evidence-to-UI traceability and reduce repeated sections** | Tie displayed events/signal findings to persisted run/detector/event IDs where available; label local screening and maintenance-baseline provenance. Reuse technical evidence panels; summarize checks once. Let analyst include speed or explicitly summarize all active scopes. Keep representative-event bounds visible. | 2-4; current persistence/interfaces already exist. | A reviewer can distinguish IF windows, harness events and local sample-screening timeline; no contradictory counts presented as the same metric; analyst/checks refer to same stored evidence; long canonical samples remain intact beyond chart caps. | S-M | Different units/time bases and bounded representative events; merging unlike evidence accidentally. |
| **6 — P2: explicitly decide thermal and causal scope** | Confirm whether a real contextual ECT detector is mandatory beyond IF ECT features/current cooling guidance. If yes, define supported operating conditions, thermal reference/evidence rules and validation before activation. If supported physical/operator/integrity evidence already exists, pass it into existing RCA gates with provenance; otherwise retain empty causes and grounded checks. | Product scope decision; demonstrated evidence inputs; 1-5 are sufficient for existing-detector structural parity. | No Guest-only detector advertised as executed; unsupported thermal diagnosis remains explicit. Any nonempty cause references observed additional evidence and stays within maximum claim level. No LLM/model retraining requirement unless a specific validated gap demonstrates need. | M-L if new thermal evidence; S-M for available gated inputs | Confusing normal warm-up/load with a fault, unsupported causal claims, or expansion based only on a screenshot. |

Repository/deployment traceability is a release dependency across these tasks: preserve the current dirty work, then package the intended changes in reviewable commits and record code/image/bundle provenance. This audit does not authorize discarding data deletions, committing unrelated work or deploying. Matching runtime code today does not make the current HEAD a reproducible release.

Do **not** start by adding another AI Analyst computation, retraining IF, enabling rejected temporal candidates, introducing a new database/history service, or populating possible causes from demo text. Those do not address the proven blockers.

## 10. Recommended next implementation task

**Implement a bounded large-session RAW decode/analyze contract for the existing DriveSafe rerun path, keeping one complete session and the current decoder/features/harness.** This is the critical active limit and blocks use of the already-working server evidence pipeline for the requested real captures.

Use the current RAW endpoint/client as the change boundary (`app/api/raw.py:17-82`; `DriveSafe/services/analysis_submission.py:152-193,497-516`; `DriveSafe/integrations/analyzer_client.py:57-80`). Start with the explicit session size and byte/deadline budget rather than deleting the guard. Reuse the existing upload/canonical long-session evidence to choose the smallest implementation; no new service is justified. The acceptance set is task 1 above. Immediately pair the resulting feature with task 2's consumer regression so that a successful evidence-bearing result actually displays its observations/symptoms.

The required outcome is **complete counts, real executed/applicable/unavailable detector evidence, bounded resource use, persisted deterministic interpretation and faithful rendering**. It is not agreement with fabricated Guest diagnoses or a guarantee of nonempty possible causes.

## Audit evidence and reproduction notes

Read-only operations performed: Git metadata/diffs; source/tests/configuration reads; Docker inspect and file-hash comparison; artifact hash verification; GET health/model/OpenAPI; bounded existing log reads; PostgreSQL read-only SELECTs; pure consumer view construction from existing stored summaries and Guest fixtures. No production analysis POST was issued. No test suite, browser campaign or verifier script was executed during this audit.

The two key consumer reproductions used the deployed `services.analysis.diagnostic_evidence_view` function with `analysis_status='completed'`, `has_analysis_result=True`:

| Input | Stored/raw input | Actual view outcome |
| --- | --- | --- |
| Large production result | Result id 80, run `87f16d88-91af-476e-af77-32c079637b36`, no evidence field | `legacy_session`; 0 observations/symptoms/checks; analyst repeats absence message. |
| Current server harness | `data/analyses/analysis_20261012T141946Z_e49a483543ba/analysis_run.json`: 6 observations, 1 symptom, 8 checks | Available; 3 executed/3 applicable; 0 displayed observations/0 symptoms/8 checks. |
| Guest attention/monitor | `_diagnostic_evidence(SimpleNamespace(result_hint=...))` | Available; 1 executed/0 applicable/full; static string observations render. |

Representative database queries used, all inside `BEGIN READ ONLY ... COMMIT`:

```sql
SELECT s.id, s.session_id, s.source_type, s.source_kind, s.record_count,
       s.analysis_status, s.analysis_run_id, a.model_version, a.result_summary
FROM ride_sessions s
JOIN analysis_results a ON a.analysis_run_id = s.analysis_run_id
WHERE s.id IN (242, 248, 249);

SELECT count(*), min(seq), max(seq), count(timestamp_ms), count(rpm),
       count(tps_voltage), count(tps_raw), count(battery_voltage),
       count(iat_c), count(ect_c), count(raw_hex),
       count(*) FILTER (
         WHERE candidate_signals->>'vehicle_speed_kmh' IS NOT NULL
       ),
       count(*) FILTER (WHERE frame_valid AND checksum_valid AND decoder_valid)
FROM telemetry_records WHERE ride_session_id = 242;

SELECT count(*), min(received_count), max(received_count),
       sum(inserted_count), sum(duplicate_count), min(first_seq), max(last_seq)
FROM sync_batches WHERE ride_session_id = 242;
```

Visibility boundaries: Cloud database and relevant deployed code/config were accessible. The external Pi producer runtime/artifacts and original upload request bodies were not. Public edge plan/effective time limits were not established. Full logged-in browser DOM/screenshots and live production timeline/maintenance recomputation were intentionally outside this read-only stored-output trace. Archived tests/previews establish their stated historical scope only.
