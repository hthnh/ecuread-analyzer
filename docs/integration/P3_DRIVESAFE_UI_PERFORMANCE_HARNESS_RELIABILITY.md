# P3 — DriveSafe rerun recovery, performance and Harness reliability

24 October 2026, UTC. **Fixes are implemented and verified in isolated staging. Production has not been deployed or reanalyzed.** The rerun action is visible outside collapsed evidence, measured page latency is substantially lower, and the actual failed production session has a confirmed admission-contention explanation and a verified safe recovery path.

Evidence: [machine measurements and complete 59-session inventory](P3_DRIVESAFE_UI_PERFORMANCE_HARNESS_RELIABILITY.json), [review diff against deployed source, including focused tests](P3_DRIVESAFE_REVIEW.patch). References: [P1 integration and rollback](P1_HARNESS_DRIVESAFE_INTEGRATION.md), [P1.5](P1_5_PRODUCTION_CANARY.md), [P2](P2_DIAGNOSTIC_INTERPRETATION_QUALITY.md), [P2.5](P2_5_PRODUCTION_INTERPRETATION_CANARY.md).

## 1. Executive verdict

- **Action visibility:** production had one eligible button in HTML, hidden inside a closed native disclosure. The candidate exposes **Re-run Harness Analysis / Phân tích lại bằng Harness** near the session heading, including after a successful server run.
- **Measured latency:** matched-snapshot medians improve from **11.568 → 3.348 s** for the dashboard, **4.244 → 1.377 s** for session 242, **1.160 → 0.139 s** for Recent Rides, and **5.576 → 2.784 s** for the analysis API. Model predictions and existing evidence fields are unchanged.
- **Actual failure:** session **239**, 1,243 records, failed when Analyzer returned **429 RAW_ANALYSIS_BUSY** during a concurrent request. DriveSafe collapsed that response into **503 / failed**. The candidate preserves the selected result, exposes a retryable busy state and retains the request identity.
- **Recovery:** one copied session 239 was executed in isolated storage. An injected 0.3-second client timeout occurred, Analyzer finished, and the same-key retry recovered its completed run. There was exactly **one isolated model execution**, with **120 windows**. No production inference was launched, including on session 242.
- **Regression:** **116 DriveSafe tests passed in the final candidate container**, plus **26 Analyzer tests**. Browser acceptance covers both locales, desktop/mobile, CSRF, ownership boundaries, Guest rendering, event links, timeline, lazy chart data and recovery states.

Production retains its P2.5 images and data. Cloudflare returns 403 from this environment; external ingress remains an inaccessible measurement path, not evidence of a latency cause. No comparison website was identified, so no comparator benchmark is claimed.

## 2. Runtime baseline and exact candidate

Investigation started from actual running containers, not an older Git checkout. Current source matched the deployed runtime for inspected files. Unrelated working-tree changes were preserved.

| Role | Immutable image |
| --- | --- |
| Production Analyzer, unchanged | `sha256:9d682df1aa2c24e8197f853a75ffee3ca48accc36f1a4005bce2bddd0830d010` |
| Production DriveSafe / rollback, unchanged | `sha256:c19e057b332a5dd821d0f88ecc7670a86459885518583757bc84a0b497bd9125` |
| Verified P3 DriveSafe candidate, not deployed | `sha256:7bfe65816c9ed399833f59117fab67d0a7542fc3ea3777ff04a4c09bb4493dca` |

Candidate tag: **`ecuread-p3-20261024t022127z-candidate:drivesafe`**. It layers nine reviewed runtime files over the exact deployed P2.5 DriveSafe image; no dependency installation or floating pull occurred. It inherits Python 3.13.15, Flask 3.1.3, Gunicorn 23.0.0, SQLAlchemy 2.0.54, psycopg 3.3.6 and requests 2.34.2. Per-file source hashes and the isolated Docker build diff are in the linked evidence.

Post-investigation live memory snapshots were DriveSafe 1.269 GiB and Analyzer 160.6 MiB, without OOM/restarts. These are current resident/container totals, not measured canary peaks or capacity limits.

Production container identities remain Analyzer `db15985c6ad2ce86df24ed5d8cc3f82267c9391009ab92b19733c378ef43fd75` and DriveSafe `48795bbad3c98a955ae74bf978c487e01b07ee2d948711ba6cfc2ae3cd88777f`. The active bundle remains `native-full18-20261011T135838Z`, manifest SHA-256 `e84a8350aae4708a9e429a313e90f8ee1a625209705a6df48141133f1a8bd5ae`. All five active artifact hashes and all **22 model files** match their initial isolated copies. Analyzer code, packages, model versions, health formula and thresholds were not changed.

The P3 snapshot already contained a later manual session-242 result **86**, run `analysis_20261024T020849Z_9324ea85a0c5`, selected before this investigation. Pi result 80, P1.5 result 84 and P2.5 result 85 also remain stored. P3 did not create result 86 or change that selection.

## 3. Rerun button root cause and repair

**Exact deployed root cause:** `/home/hthnh/drisafe/templates/session_detail.html`, deployed snapshot **lines 99–125**: `<details id="model-harness">` lacked `open`, and its rerun button at **line 124** was nested inside it. Native disclosure behavior hid the button until the evidence section was expanded. The pre-change browser found **one button, eligible and enabled, but not visible**; expanding the disclosure made it visible.

The button was not removed, explicitly hidden by a CSS rule, suppressed by prior server success, or lost to a JavaScript initialization failure. Production asset/source hashes matched and browser execution had no page errors. Separate intentional omissions applied to Guest Mode and interrupted captures.

The fix is at current `templates/session_detail.html:20–26`, with the action at **line 23**, outside every disclosure and independent of whether diagnostic evidence exists. `services/analysis_submission.py:162` introduces presentation eligibility; `services/analysis.py` exposes it alongside processing state. The existing route, CSRF check and ownership query remain in place. The 20,000-record visibility limit applies to RAW-backed submission; supported canonical-only fallback keeps its existing contract and is tested separately.

| Session/user condition | Candidate behavior |
| --- | --- |
| Completed eligible Pi-native session | Visible action |
| Prior successful server Harness result | Action remains visible |
| Failed previous attempt with complete input | Action remains available |
| Fresh pending/submitted claim | Visible, disabled; explains processing and refresh |
| Expired 180-second claim | Explicit retry can reclaim through the existing atomic lease |
| Incomplete synchronization/capture, missing records, unsupported format, insufficient reconstruction evidence, malformed HEX/nonfinite timestamp, >20,000 RAW-backed records | Accurate localized explanation; no enabled submit action |
| Complete canonical fallback supported by the existing contract | Action remains eligible; no invented RAW |
| Guest / unauthenticated / other owner | No Guest action; authentication/ownership rejection preserved |

The browser disables a submitted action immediately. `running` stays disabled until refresh; busy admission honors a short retry delay and enables an explicit retry without posting automatically. Network/Analyzer timeouts display **Result unconfirmed. Retry safely.** The existing sessionStorage request ID survives these outcomes and clears only after successful completion. There is no new endpoint, polling queue, page-render inference or automatic analysis trigger.

## 4. Actual failed-session inventory

Read-only production inventory: **59 sessions**, **58 completed analysis states**, **one failed**, **zero pending/submitted**. All stored record counts match metadata. The copied database audit found **no missing timestamps, reversed timestamps, sequence gaps, malformed HEX, invalid-frame flags or checksum failures**. The largest session has 12,625 records, below the 20,000 limit.

| Session | Records | Current production state | Failure stage / error | Retryable | Proposed action |
| --- | ---: | --- | --- | --- | --- |
| **239** | 1,243 | Failed manual attempt; original Pi result 77 remains selected | RAW admission: upstream **429**, DriveSafe **503** at 02:09:12 UTC | **Yes**, same attempt identity after admission is idle | Explicit manual retry after approved rollout; not executed in production here |
| 246 | 3,352 | Interrupted capture, complete sync; Pi result completed | Existing completed-capture eligibility; manual route returns 409 | No while capture remains interrupted | Preserve evidence and termination status; do not force completion |
| 248 | 12,211 | Interrupted capture, complete sync; Pi result completed | Same eligibility boundary | No while interrupted | Preserve uploaded RAW/canonical data |
| 249 | 9,917 | Interrupted capture; historical server result exists | Same current eligibility boundary; historical success does not override capture state | No while interrupted | Preserve historical server result 83 and original Pi result |
| 237 / 238 | 87 each | Successful `limited_data` server results | **Not a failed execution:** four windows; IF scored four, battery/speed each scored three | Not a failure | Keep limited-data/applicability descriptions |
| 242 | 12,625 | Successful selected server result 86 | No current failure | Not needed | Read-only inspection only |
| Other selected legacy results | See complete JSON inventory | Stored older analysis, some without Harness evidence | Missing evidence alone does **not** establish server failure | Not classified as failed | No automatic rerun |

There are **53 selected results without diagnostic evidence**, including legacy ESP/server and Pi producers. They are not counted as 53 Harness failures. Each of the 59 session IDs, record counts, selected/history result IDs, evidence availability and disposition is in the machine inventory.

### Confirmed session-239 failure

Retained DriveSafe access logs show `/api/sessions/239/analysis/re-run` returning **503** at **02:09:12**. Analyzer's corresponding retained log shows **429** while session 242's already-existing manual run was processing. The RAW route's only 429 path is `RAW_ANALYSIS_BUSY`: its nonblocking shared admission lock rejects a competing request before body decoding/model work. No production Analyzer run artifact for session 239 was found.

The isolated reproduction held only the copied Analyzer lock and submitted the copied session through the existing DriveSafe route. Before the fix it produced **503 / failed**, with an embedded upstream `RAW_ANALYSIS_BUSY` detail. After the fix it produced **429 / busy**, `retryable: true`, `Retry-After: 5`, and preserved the previous analysis pointer/status/timestamp. This confirms orchestration/admission contention rather than invalid input or a detector failure.

The client now retains upstream status, structured code, retryability and retry delay. The submission handler logs session/status/code/retryability without RAW or credentials. Historical error reasons were not persisted in the old schema; the diagnosis above uses retained logs, source and reproduction, rather than inventing missing historical messages.

### Bounds, validation and other mechanisms

Actual serialized copied requests: session 239 **140,465 bytes**; 242 **1,448,094**; 246 **380,642**; 248 **1,399,998**; 249 **1,134,396**. Construction was read-only, preserving complete RAW ordering/identity; interrupted sessions were not submitted.

Verified existing limits remain RAW **20,000 records / 8 MiB**, body receipt **10 s**, cooperative compute **45 s**, Analyzer client **60 s**, Nginx upstream **90 s**, Gunicorn **120 s**, stale-claim lease **180 s**. DriveSafe's 2 MiB inbound guard is not an upstream response cap. The previous P2.5 complete response was 12,503,797 bytes and worked; no 413/422/408/504, incompatible decoder/bundle, invalid canonical response, idempotency conflict, stuck claim or worker/OOM failure was identified in the retained production evidence for this inventory. This is not a claim about logs no longer retained.

## 5. Latency by route and network layer

Five serial, bounded samples per route/path used the same owner, English locale, route and cache-control policy. Guest used a signed Guest session; login/static requests were unauthenticated. Each curl process used a fresh connection and negotiated compression. All samples in the table were HTTP 200. A five-sample median and observed maximum are reported; **p95 is not estimated from this small sample**. First observed request values and every individual timing are in JSON; neither OS nor database caches were flushed.

Times below are seconds. Internal means the actual live Gunicorn container; origin means the existing production Nginx host route. Sizes are transferred body bytes, internal / origin; Nginx compresses HTML, so transfer sizes differ while the route/authentication remain the same.

| Production route | Internal median TTFB | Nginx median TTFB | Nginx median total | Body bytes internal / Nginx |
| --- | ---: | ---: | ---: | ---: |
| `/` | 11.714 | 11.597 | 11.599 | 169,734 / 44,863 |
| `/sessions` | 1.262 | 1.112 | 1.114 | 173,635 / 44,983 |
| `/sessions/237` | 0.076 | 0.102 | 0.104 | 242,287 / 52,892 |
| `/sessions/242` | 3.969 | 4.081 | 4.083 | 263,165 / 55,014 |
| `/api/sessions/242/analysis` | 5.593 | 5.373 | 5.375 | 719,083 / 719,083 |
| `/sessions/900101` | 0.032 | 0.037 | 0.039 | 207,824 / 49,969 |
| `/auth/login` | 0.008 | 0.009 | 0.010 | 158,086 / 42,921 |
| `/static/js/sessionDetail.js` | 0.002 | 0.003 | 0.003 | 25,176 / 25,176 |
| `/static/css/dashboard.css` | 0.002 | 0.002 | 0.002 | 32,574 / 32,574 |

Connection and address-resolution medians are recorded per route in JSON and are sub-millisecond on these local paths. TLS is **not applicable to these HTTP service/origin measurements**. Public `https://drivesafe.top/auth/login` returned **403 / Cloudflare**; its DNS/TLS/TTFB cannot be used as a successful DriveSafe comparison. Cloudflare WAF, Tunnel and cache configuration were not changed. The direct/origin agreement locates the measured multi-second bottleneck inside DriveSafe, independently of the unidentified comparison website.

## 6. PostgreSQL and backend profiling

Read-only cProfile against the deployed code found:

| Production path | Profiled wall time | SQL statements | JSON loads | Key cost |
| --- | ---: | ---: | ---: | --- |
| Session 242, first / repeated | 7.150 / 6.655 s | 10 | 47,954 | Full ORM telemetry loading; selected canonical/view construction repeated twice |
| Session-242 analysis API | 7.298 s | 11 | 85,830 | Full telemetry loaded again by `reanalysis_evidence` |
| Dashboard | 15.985 s | 40 | 148,887 | Seven full analysis views and repeated baseline work |
| Recent Rides | 1.421 s | 5 | 35 | Bulk analysis query unnecessarily fetched historical canonical JSON; evidence deep copy |

These cProfile times include profiler overhead and are **not** substituted for uninstrumented HTTP latency. SQL cursor timings include result transfer/driver work; they are not pure PostgreSQL CPU time. A read-only `EXPLAIN (ANALYZE, BUFFERS)` on the copied session-242 telemetry query measured **42.840 ms execution / 4.840 ms planning**, far below the multi-second ORM/JSON request path. No index migration was justified.

The final candidate also ran through a temporary, isolated Gunicorn timing observer with the same two workers/eight threads and read-only PostgreSQL connections. Three serial observations per route measured inclusive spans without cProfile's per-call instrumentation. The observer and container were removed; no debug middleware entered the candidate or production.

| Final path | Median app time | SQL count / cursor time | Telemetry loading | Selected record view | Maintenance / baseline | Evidence normalization | Jinja / API JSON |
| --- | ---: | --- | ---: | ---: | --- | ---: | --- |
| Dashboard | 3.384 s | 47 / 0.525 s | 1.399 s | 1.084 s | 0.427 / 0.283 s | 0.014 s | Jinja 0.059 s |
| Session 242 | 1.502 s | 10 / 0.133 s | 0.391 s | 0.540 s | 0.092 / 0.050 s | 0.004 s | Jinja 0.158 s |
| Analysis API | 2.747 s | 11 / 0.720 s | 1.647 s | 0.636 s | 0.092 / 0.051 s | 0.004 s | JSON 0.011 s |

Spans are nested and **must not be summed**. Session-page statistics cost about **0.141 s**, timeline generation **0.075 s**; the API retains full JSON needed for evidence reporting. The small session median app time was **0.053 s**, including **0.021 s** Jinja. SQL statement count did not uniformly fall: lazy canonical loading trades more small selected-run queries for much less transferred/deserialized history. This is a payload/ORM improvement, not a claim that every query count decreased.

Client-send to application-entry delay, an upper bound covering local transport, Gunicorn admission/queue and routing, measured **1.27–3.61 ms** in this serial isolated check. Worker queue time alone is not separately identifiable. No saturation/concurrency certification follows from these observations.

## 7. Browser and frontend profiling

Chromium **149.0.7827.55**, desktop 1440×1000 and mobile 390×844, exercised the normal production-origin page read-only before changes and the copied candidate in isolation. No browser clicked session 242's analysis action.

Before the fix, charts already created **zero canvases while collapsed**, but `sessionDetail.js` still eagerly fetched `/api/sessions/242/samples` on page load. Its decoded response was **5,009,017 bytes**, with about **4 seconds** observed on the live origin and a slower isolated first observation. The technical evidence remained server-rendered despite its disclosure being collapsed. It was not a duplicate initialization loop; it was an unnecessary initial samples request.

The candidate embeds only the selected run's compact timeline/session boundaries for immediate timeline interaction. The full samples request occurs only when charts open, is deduplicated while pending and can be retried after failure. Opening charts still produces **four canvases**, and timeline/event links still resolve original events. No analysis POST is generated by a page view.

| Browser observation | Before | Final candidate |
| --- | --- | --- |
| Session-242 load samples, seconds | 7.512 / 4.370 / 4.234 | 1.938 / 1.682 / 1.619 |
| Median load, n=3 | 4.370 s | 1.682 s |
| Initial full samples requests | 1 per navigation | **0** |
| Initial chart canvases | 0 | 0; four after opening |
| DOM nodes | 2,324 | 2,325 |
| Long tasks | One 117 ms first observation; zero warm | One 60 ms first observation; zero warm |
| Initial HTML bytes, uncompressed service body | 263,165 | 270,431 |

The browser's first before observation also overlapped baseline response extraction; it is shown explicitly, not treated as controlled cold-cache latency. The five-sample uninstrumented HTTP comparison is the primary backend performance result. Final browser TTFB samples were **1412 / 1422 / 1389 ms**, DOMContentLoaded **1935 / 1680 / 1617 ms**. The remaining difference includes existing external chart-library loading and layout. It does not support blaming JavaScript for the original multi-second server TTFB.

Chart.js / zoom decoded bodies remain **205,889 / 15,203 bytes**; pinned CDN configuration is unchanged. Local JS/CSS bytes appear in the HTTP table and JSON. Browser metrics record script/layout duration, resource timings, long tasks and nodes. No repeated localization observer, duplicate samples fetch or initialization loop was found. Technical details still incur initial HTML/DOM work, but final Jinja/evidence timings are small relative to telemetry loading. HTML grew slightly because of localized state text and the compact timeline bootstrap; no HTML-size reduction is claimed.

## 8. Ranked confirmed bottlenecks and minimal fixes

1. **Repeated full telemetry/ORM/JSON work on ordinary pages.** Request-local read reuse avoids fetching the same input/view twice. Stored-run page reads defer unused candidate/quality JSON; the selected numeric/quality view does not touch those deferred fields. Historical maintenance baselines project their seven required numeric columns. Chart, export and analysis submission retain complete input paths. Read reuse lives in the WSGI request environment, including when tests share an application context; a regression verifies fresh data on the next HTTP request. Reuse is restricted to GET/HEAD; a POST regression confirms bulk reload after commit rather than an expired-row query per record. No cross-request or Redis cache was added.
2. **Unnecessary historical canonical materialization and evidence copying.** `AnalysisResult.canonical_telemetry` is deferred at ORM level, with no column/schema migration. A detail page shares one selected result across statistics and analysis. Presentation copies only the aggregate object it changes instead of deep-copying all diagnostic evidence. Legacy rows and stored evidence remain immutable.
3. **Eager full chart-data fetch while charts are collapsed.** The initial 5 MB request is removed from the normal view path. Timeline data comes from the existing selected run; charts load through the same samples API when requested.

The separate P0 visibility repair moves the existing action outside closed evidence. The narrow reliability repair preserves typed admission/timeout information and the existing RAW idempotency workflow. No new service, detector, database index/migration, queue, model, CDN policy or frontend architecture was introduced.

Changed DriveSafe runtime files: `models.py`; `services/analysis.py`; `services/analysis_submission.py`; `integrations/analyzer_client.py`; `routes/dashboard.py`; `templates/session_detail.html`; `static/js/dashboardApi.js`; `static/js/sessionDetail.js`; `static/locales/messages.json`. Focused tests: `tests/test_p3_rerun_recovery.py`. Analyzer runtime source is unchanged.

## 9. Matched before/after measurements

The same isolated PostgreSQL production snapshot, existing model artifacts, Gunicorn runtime, owner, routes, language and five-request serial procedure were used. Recovery-test mutations were reset only in the disposable fixture before the final matched benchmark. No profiler, browser or recovery inference ran concurrently with the final HTTP batch. Public ingress and static asset caches were not altered.

| Route | Before median total, s | Final median total, s | Reduction | Final observed maximum, s |
| --- | ---: | ---: | ---: | ---: |
| `/` | 11.568 | 3.348 | 71.1% | 3.621 |
| `/sessions` | 1.160 | 0.139 | 88.0% | 0.257 |
| `/sessions/237` | 0.078 | 0.070 | 11.1% | 0.198 |
| `/sessions/242` | 4.244 | 1.377 | 67.6% | 1.439 |
| `/api/sessions/242/analysis` | 5.576 | 2.784 | 50.1% | 2.981 |
| `/sessions/900101` | 0.033 | 0.049 | -46.8% | 0.097 |
| `/auth/login` | 0.010 | 0.010 | -4.8% | 0.014 |
| `/static/js/sessionDetail.js` | 0.002 | 0.002 | -21.1% | 0.003 |
| `/static/css/dashboard.css` | 0.002 | 0.002 | 12.6% | 0.004 |

The small session already loaded quickly, so its small difference is not interpreted as a meaningful optimization. Every existing analysis/chart API field was compared: predictions, health, model versions, diagnostic evidence, maintenance, timeline, statistics and samples match the baseline. Only additive rerun presentation state differs. Explicit full JSON export retains original RAW/quality/candidate fields.

## 10. Harness recovery and cross-cutting regression

One actual copied production session **239** executed against copied active models with isolated PostgreSQL/files:

- Input **1,243 complete records**, **120 windows**, original RAW identity/order preserved.
- Fixed key `p3-isolated-239-recovery`; run **`analysis_20261024T030535Z_57136e17ffbd`**.
- A deliberately shortened **0.3 s DriveSafe→Analyzer timeout** returned 503 / `ANALYZER_TIMEOUT`, `retryable: true`, after **1.484 s** including route work.
- Analyzer completed after the client timed out. Its completed cache/run was checked before retry. The same-key HTTP retry returned **200** in **3.260 s**, and a stored-result retry took **0.825 s**.
- Exactly **one Analyzer run directory/model execution**; original Pi result and all original result rows through ID 86 plus RAW remained unchanged. The copied session gained one server result. Test fixtures were subsequently reset for performance measurements; browser checks reused the same completed cached identity.
- IF executed `ok`: **120 scored / 114 positive**; battery `ok`: **118 / 1**; speed `ok`: **118 / 0**. Two contextual windows were inapplicable; they were not classified as failures.
- Persisted diagnostic output: **eight observations, one symptom, zero possible causes, nine checks**. No finding or cause was manufactured.

Browser recovery-state fixtures returned busy/running/timeout outcomes without executing a model, checked correct messages, preserved request IDs and disabled-submission behavior, then used the **real existing CSRF-protected route** to recover/reuse the completed isolated attempt. The final browser confirmed **EN/VI labels**, mobile visibility, Guest absence, unauthorized API **401**, working event links, collapsible technical details, four charts, timeline clicks and zero page errors. Other-owner view/POST **404** and incomplete/failed/processing/invalid-input behavior are covered in focused regression.

Final candidate image verification:

- **116 DriveSafe container tests passed in 80.940 s**, including the full 88 dashboard cases, P1/P2/localization and twelve P3 cases.
- **26 Analyzer tests passed**: RAW limits, diagnostic interpretation and existing evidence consistency. The live Analyzer image/source was unchanged.
- Existing SQLite fixture connection ResourceWarnings and the Starlette/httpx deprecation were observed; no failed test was ignored.
- One initial disposable Guest timeline bootstrap error was corrected with explicit defaults before final acceptance. No such change reached production.

Normal viewing initiates no inference. Repeated submissions while a request is processing are disabled; failed/unconfirmed requests keep their identity. Raw validation and canonical/model/decoder provenance checks remain enforced. No historical result is replaced by the new recovery path.

## 11. Production preservation and deployment/rollback plan

**Stop point: production deployment requires separate authorization and was not performed.** No production Compose change, restart, analysis POST, model change, Pi5 modification, database mutation or Cloudflare change occurred. Both original container IDs/images remain unchanged, with no OOM/restart. Final database revision is **`b7f2c9a4d610`**, pending/submitted count **0**. Production session 239 remains in its preexisting failed state and session 242 retains preexisting run 86.

Read-only deterministic production SQL hashes match before/after for **all telemetry (365,478,573 serialized bytes)**, **all analysis results (54,403,750 bytes)**, **all ride sessions**, sync batches and RAW artifact rows. The telemetry hash also matches the prior P2.5 preservation hash. Retained production logs contain **zero analysis POST requests during P3** (one unrelated `/xmlrpc.php` probe returned 404), and no new Analyzer run artifact has a timestamp after investigation start. Secrets, full responses, screenshots and the production database copy are retained only under the protected directory:

`/home/hthnh/p3-investigation/ecuread-p3-20261024T022127Z`

Isolated Compose containers, network and the disposable PostgreSQL volume were removed after verification. Temporary owner/Guest authentication files were deleted. The candidate image, protected database snapshot, source hashes, verification scripts and measured evidence remain available for review.

The reviewable candidate override is `drivesafe-candidate.json`; rollback is `drivesafe-rollback.json` in that protected directory, with original environment and direct Gunicorn command preserved. The latter pins the **current deployed P2.5 DriveSafe image**, not a much older P1 image. A protected read-only PostgreSQL dump was used for isolated reproduction; take a **fresh deployment-time backup** after approval.

For a separately authorized maintenance window:

1. Recheck actual source/image identities against the machine manifest, schema `b7f2c9a4d610`, active bundle/artifact hashes, available resources and zero analysis claims/lock holders. Preserve newer unrelated changes and stop on an unexplained compatibility mismatch.
2. Create fresh protected PostgreSQL/Analyzer-data/config backups and immutable rollback references using the P1 section-8 procedure. Do not print environment secrets or run migrations.
3. **Analyzer first: health/bundle compatibility check only.** P3 has no Analyzer code/image update, so leave its verified image and model active. Do not restart it redundantly or build a replacement.
4. Deploy only the reviewed DriveSafe image using the protected override and existing volumes/networks, without build or pull:

```bash
cd /home/hthnh/drisafe
docker compose -f docker-compose.yml -f /home/hthnh/p3-investigation/ecuread-p3-20261024T022127Z/drivesafe-candidate.json up -d --no-build --pull never --no-deps web
```

5. Verify health, login/Guest, dashboard/Recent Rides, read-only session 242, visible EN/VI rerun action, event/timeline/chart data, RAW/history counts and no unexpected jobs. This deployment plan does **not** authorize a production rerun of 239, 242 or any other session. Evaluate public ingress from an accessible authorized client.
6. On a concrete compatibility regression, settle in-flight manual work and perform image-only DriveSafe rollback:

```bash
cd /home/hthnh/drisafe
docker compose -f docker-compose.yml -f /home/hthnh/p3-investigation/ecuread-p3-20261024T022127Z/drivesafe-rollback.json up -d --no-build --pull never --no-deps web
```

Analyzer remains unchanged. Preserve all stored results and RAW; do not downgrade schema or restore the database as routine image rollback. Original Compose/default tags were not rewritten. Candidate packages, source fingerprints and image identity are frozen for review.

## 12. Remaining limitations

- Public Cloudflare access is 403 from this environment. Successful browser/API checks used actual production origin read-only or isolated candidates; public latency and real password submission were not accepted here.
- Dashboard/session viewing still performs necessary complete-session statistics and selected canonical interpretation. Median dashboard latency is improved to about 3.3 s, not instant; the analysis API still loads complete evidence and takes about 2.8 s. This task does not certify concurrent load or fleet capacity.
- Technical DOM and pinned chart-library downloads remain eager. The demonstrated initial full samples request was removed; broader UI/CDN/cache changes were intentionally outside scope.
- Error payloads from much older attempts are not retained in the existing database schema. No additional production failure mechanism beyond the observed admission contention was established; unsupported historical claims are not inferred from missing evidence.
- Interrupted captures stay ineligible under the current manual contract. No metadata was changed to bypass it. The browser's request identity is sessionStorage scoped; cross-device recovery of an unknown lost request ID is not a new durable retry service. Canonical-only legacy retry orchestration remains its existing contract; the fully demonstrated post-timeout cache recovery is the RAW-backed path.

**Ready for deployment review of the pinned P3 DriveSafe candidate. Production remains on P2.5, and no production analysis was launched.**
