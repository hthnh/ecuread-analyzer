# P3.2 — Reliable long-running Harness analysis

> Follow-up: the source RAW cap was raised to 50,000 on 25 October 2026. The measurements and candidate images below retain their original 20k verification context. See [50k limit change and tests](P3_2_RAW_50K_LIMIT.md); replacement candidates are required before deploying the new limit.

Completed **2026-10-24T17:01:20.165748+07:00 (Asia/Saigon)**. **Implemented and verified in isolated staging. No production deployment or production analysis was performed.** Production remains on P3.1, including its existing failed session-248 attempt; this report does not claim that live failure has been rerun or repaired in production.

The five copied sessions completed with full inputs, active IF + Battery + Speed, persisted diagnostic evidence and preserved historical results. The manual workflow now returns promptly, continues in an existing-worker executor, and recovers its durable identity across reloads/restarts. [Machine evidence](P3_2_LONG_RUNNING_HARNESS_RELIABILITY.json) and the [source-only review patch](P3_2_LONG_RUNNING_HARNESS_REVIEW.patch) accompany this report.

## Root cause and actual timeout chain

Retained production logs show session 248's manual request at **24 October 2026, 15:54:11 Asia/Saigon** failing upstream with HTTP **504 / `RAW_ANALYSIS_TIMEOUT`**. DriveSafe returned HTTP 503 at 15:54:13. The stored input has 12,211 complete records and is a synchronized interrupted capture; the timeout is not evidence of invalid capture data.

Analyzer `app/api/raw.py` set `deadline = monotonic() + 45` through `Settings.raw_analysis_timeout_seconds`. Existing cooperative checks cover decoding, validation, feature extraction, primary inference and diagnostic evidence, including a check before persistence. Expiration could discard an otherwise useful computation before a completed analysis was stored. DriveSafe also held the browser request open until the Analyzer response was validated and committed.

| Layer | Inspected production P3.1 | P3.2 isolated candidate / rollout target |
| --- | --- | --- |
| RAW compute budget | 45 s | `RAW_ANALYSIS_TIMEOUT_SECONDS`, default **300 s**; finite/positive validation |
| DriveSafe → Analyzer | 60 s, direct internal HTTP | `ML_API_TIMEOUT_SECONDS`, **360 s** |
| Nginx Proxy Manager | **90 s** inherited from `/etc/nginx/nginx.conf`; the unrelated monitor server's 1 h setting is not the DriveSafe route | Reviewed Nginx examples and staging proxy use **420 s** read/send |
| Gunicorn | gthread, 2 workers × 8 threads, `--timeout 120`, graceful shutdown 30 | same worker/thread counts; configurable heartbeat timeout **420 s**, graceful shutdown 30 |
| DB claim lease | 180 s | configurable **420 s**, greater than the analysis client's timeout; durable-owner recovery under the global executor lock |
| Health checks | used the analysis-client timeout | at most **3 s**, independently of the longer analysis budget |
| Body receipt / RAW bounds | 10 s / 20,000 records / 8 MiB | unchanged |

Cloudflare [documents a default proxy read timeout of 125 seconds](https://developers.cloudflare.com/fundamentals/reference/connection-limits/); this zone's negotiated override was not inspected. Raising origin limits alone would therefore leave a fragile browser connection. In P3.2, neither Cloudflare nor Nginx carries the long computation: the browser sends a small start request and polls metadata; the executor calls Analyzer directly on the internal network. The first copied 248 start returned HTTP 202 in **2.31 s**. No Cloudflare security/Tunnel configuration was changed.

Gunicorn's [gthread timeout is a worker-heartbeat limit, not a per-request deadline](https://docs.gunicorn.org/en/stable/design.html). The longer setting is nevertheless coordinated with the client. A graceful application restart can still interrupt an executor; durable state and same-key recovery handle that case rather than depending on the old process staying alive.

## Implementation

### Bounded executor using the existing web service

`drisafe/services/manual_analysis.py` persists a small atomic JSON attempt per session in `HARNESS_STATE_DIR`. It stores identity, state and result references, **not another RAW/canonical body**. A shared filesystem queue lock serializes admission; a second, nonblocking executor lock permits **one active outgoing analysis across all Gunicorn workers**. Default capacity is **two accepted attempts total**: one executing and one waiting. Excess work receives a retryable busy response without altering its previous analysis.

Each existing Gunicorn worker starts a lightweight daemon executor. It scans only explicitly accepted manual attempts; it never enumerates historical sessions for analysis. On startup it can recover an orphaned running attempt from the persistent directory. Writes use a private temporary file, fsync and atomic replacement. Production configuration requires a persistent state-directory setting; a shared bind mount is included in the reviewed Compose change. No Celery, Redis queue, new production service, database table or schema migration was introduced.

The existing CSRF/ownership-protected manual POST enqueues work and returns 202. A new **read-only status GET** under the same session provides queued/running/completed/retryable/input-failure state and the same request ID. Status reads do not scan telemetry, call inference or submit retries. The normal page and chart paths keep the P3 optimizations.

### Existing validation, submission and persistence remain authoritative

The executor calls the existing submission service, preserving P3.1 completed/interrupted eligibility, complete synchronization, structural validation, decoder provenance, previous selected results and original Pi metadata. Analyzer retains its single RAW admission lock; canonical analysis now shares that admission guard as well.

The RAW limit remains 20,000 records / 8 MiB. Existing maximum session size is 12,625 records, with complete serialized RAW requests around 0.14–1.45 MB. No existing tested RAW input requires a limit increase. Canonical requests use the existing service upload-size bound of **25 MiB**, rather than incorrectly applying the RAW-specific 8 MiB bound. The 10-second receipt guard remains separate from compute time. No valid samples are skipped or truncated.

Canonical-only fallback retains its input contract and gains an HTTP idempotency header with a durable Analyzer reservation/cache. RAW keeps its existing request-key cache and reserved run ID. A completed Analyzer response can be recovered even if DriveSafe missed success or its last status write. A finished DriveSafe result is reused by request ID before another call is made.

Typed transient HTTP/network/timeouts, including truncated-success JSON, are retryable interruptions. Genuine structural failures remain separate. Busy errors restore previous selected-result state; the executor waits/retries the same accepted identity. Unexpected process errors produce a safe retryable status, with technical exceptions confined to server logs.

### Browser recovery and localization

`sessionDetail.js` retains the request ID, polls GET status and never automatically posts an independent analysis. Server-side discovery also works in a fresh browser context without sessionStorage. Completion refreshes the selected analysis once if the displayed run is older. Queued/running disable repeated submission; retryable interruption permits an explicit same-attempt retry. EN/VI messages distinguish these states and never use raw Python exceptions or backend codes as the primary message. Guest has no action and cannot inspect real-session status.

## Recovery behavior

| Situation | Verified behavior |
| --- | --- |
| Leave page, disconnect or reload | executor continues; a fresh context recovers status and identity through GET |
| Analyzer busy | accepted attempts wait within the bounded queue; excess work receives retryable busy; previous result remains selected |
| Temporary client/upstream timeout | previous result preserved; durable attempt marked retryable; explicit retry retains identity |
| DriveSafe restart during inference | filesystem executor lock releases; new worker reclaims only its durable attempt, tolerates Analyzer busy, then recovers the completed cached response |
| Lost success/status write | completed database row or Analyzer reservation/cache is reused; no completed inference is executed again |
| Invalid RAW, active capture or incomplete sync | rejected before queueing/claiming; no inference |
| Same request ID after success | returns the original completed run, not another execution |

No exactly-once CPU claim is made for forcibly killing **Analyzer itself before durable completion**: an explicitly resumed partial attempt may need recomputation under its reserved run ID. The demonstrated restart kept Analyzer alive, recovered its completed work and executed it once. Loss of the state volume or Analyzer data/cache is a storage-recovery event, not a supported ordinary image rollback.

## Real-session copy results

Fresh production data was restored into isolated PostgreSQL; active model artifacts and required historical context were copied into isolated Analyzer storage. The network is internal and has no production mutable mounts. Same host, package bases, two gthread workers/eight threads and no explicit CPU/memory quotas match the inspected production runtime. Sampled memory is container usage, not peak RSS or a hard capacity certification.

A staging-only wrapper counted starts/completions of `AnalysisService.analyze` without changing detector inputs, algorithms, thresholds or outputs. It is not in the candidate image. **Five physical executions completed for five logical requests**; busy retries, browser status checks and completed-result retries added none.

| Copied session | Full records | Windows | Analyzer service span, s | Acceptance→stored completion, s | Health Score | Observations / symptoms / causes / checks |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 239 | 1,243 | 120 | 2.11 | 4.33 | 17.44 | 8 / 1 / 0 / 9 |
| 246 | 3,352 | 331 | 6.10 | 54.46 | 97.00 | 6 / 1 / 0 / 7 |
| 248 | 12,211 | 1,217 | 25.02 | 44.56 | 85.99 | 16 / 2 / 0 / 9 |
| 249 | 9,917 | 987 | 16.84 | 29.64 | 94.56 | 14 / 0 / 0 / 7 |
| 242 | 12,625 | 1,258 | 21.51 | 38.36 | 86.07 | 16 / 1 / 0 / 10 |

The Analyzer span excludes RAW decoding and response transfer. End-to-end time includes queueing, persistence, status sampling and, for 248/246, restart/recovery and waiting. All **39,348 records** and **3,913 windows** are represented. All three detector executions have status `ok`; contextual applicability still governs scoring.

| Session | IF scored / positive | Battery scored / positive | Speed scored / positive |
| --- | ---: | ---: | ---: |
| 239 | 120 / 114 | 118 / 1 | 118 / 0 |
| 246 | 331 / 1 | 331 / 1 | 330 / 0 |
| 248 | 1217 / 84 | 1217 / 6 | 976 / 0 |
| 249 | 987 / 12 | 987 / 0 | 855 / 0 |
| 242 | 1258 / 69 | 1256 / 1 | 1213 / 1 |

Unscored contextual windows are not misclassified as detector failures. All results retain IF timelines, model/bundle provenance, RCA and diagnostic evidence. Unsupported possible causes remain empty. Original results 83/86 and their new copied 249/242 results match in Health Score, window counts and detector scored/positive counts, supporting unchanged model semantics.

- Session 239: `analysis_20261024T092715Z_220789abad2a`; copied DriveSafe result 89.
- Session 246: `analysis_20261024T092705Z_8d4b5c8cd04a`; copied DriveSafe result 88.
- Session 248: `analysis_20261024T092627Z_4158370f72e9`; copied DriveSafe result 87.
- Session 249: `analysis_20261024T092725Z_afaf6f3e5ecb`; copied DriveSafe result 90.
- Session 242: `analysis_20261024T092757Z_dd58fdac4c39`; copied DriveSafe result 91.

### Session 248 and the old cutoff

The copied 248 completed with **12,211 records, 1,217 windows, 16 observations, two symptoms, zero causes and nine checks**, with request ID `p32-stage-248`. DriveSafe was restarted during its actual Analyzer execution; Analyzer stayed alive. A second accepted capture waited, and a third received 429. The same request identity recovered the stored output and remained usable after browser reload/new context.

Its measured end-to-end time was **44.56 s**, so this run is **not** presented as a physical execution beyond 45 seconds. A deterministic deadline-clock regression separately advances elapsed compute time to **60 s**: the old 45-second budget returns 504, while the configured 300-second budget completes/persists successfully. This proves removal of the lower cutoff without manufacturing findings or repeatedly rerunning production telemetry to force a target duration.

Maximum sampled memory across **50 samples**: Analyzer **470.2 MiB**, DriveSafe **781.4 MiB**. One active compute slot limits overlapping large bodies/results. The former large analysis snapshot in the start response is eliminated; no broad change to evidence serialization or model execution was justified by these observations.

## Regression and browser acceptance

- **135 DriveSafe tests passed on the final image**, including P3/P3.1 behavior and the new worker, queue bound, retry identity, orphan claim, partial-success recovery, input, ownership/Guest/CSRF and transport tests.
- **37 Analyzer tests passed in the repository virtual environment**, covering RAW bounds/admission, configurable and simulated elapsed deadlines, canonical idempotency/restart, evidence consistency and P2 interpretation. The five actual Harness executions used the frozen Analyzer candidate container. Existing SQLite fixture ResourceWarnings and Starlette/httpx deprecation warnings were observed; no failed regression was ignored.
- Actual reload and fresh-context recovery observed a running 248 and then completion with zero browser POSTs.
- EN/VI production-copy pages, mobile, Guest/anonymous boundaries, event links, timelines, collapsible technical details and lazy charts passed without JavaScript errors. Deterministic browser status fixtures separately verify queued/running/retryable/invalid primary messages in both languages, with zero inference POSTs.
- Final client cached-response recovery retrieved all 12,211 records under the original run in **0.96 s**, without increasing the five-execution count. Core worker/Analyzer files used in the real runs are unchanged; final transport hardening additionally covers canonical idempotency headers, transient response classification and a bounded health probe.

### Matched page performance

Five serial samples per route compared P3.1 and P3.2 on the **same copied database after the five runs**, same proxy, owner, EN locale, worker/thread counts and cache policy. No inference, test suite or browser ran during the HTTP batches; caches were not flushed. Median and observed maximum are reported, not p95 from five samples.

| Route | P3.1 median, s | P3.2 median, s | P3.2 observed max, s |
| --- | ---: | ---: | ---: |
| `/` | 4.442 | 4.643 | 4.845 |
| `/sessions` | 0.167 | 0.214 | 0.397 |
| `/sessions/242` | 1.529 | 1.557 | 1.590 |
| `/api/sessions/242/analysis` | 2.767 | 2.828 | 2.988 |

Main-route medians remain within about 5%; Recent Rides differs by about 47 ms. P3 ORM deferral, read reuse, baseline projection, evidence presentation and lazy chart paths remain intact. This copied database now contains five additional large server results, so these timings are not a direct comparison against the earlier production dataset's 3.4-second dashboard. This is not a concurrency/load certification.

## Production preservation and candidate identities

No deployment, restart, analysis POST, model change or schema operation occurred in production. Its original container IDs/start times/images remain unchanged. Production still has 59 sessions, 69 analysis results and no pending/submitted claim; session 248 retains its prior failed state. Production RAW, historical result rows, synchronization artifacts and capture metadata match the protected copied baseline. In the copy, every original telemetry/result/artifact hash is unchanged; only the five new server results and intended analysis selection/status were added.

| Candidate | Immutable image |
| --- | --- |
| Analyzer | `sha256:effa2cb42a812f55f857db203c12bc6f3507c204398e76e438a4ab6b81fd567e` |
| DriveSafe | `sha256:eb8676f4e1f6827e566e2a82027d8bf4c5509f3b4d07e0eb8bb446260aac727a` |
| Live Analyzer / rollback | `sha256:9d682df1aa2c24e8197f853a75ffee3ca48accc36f1a4005bce2bddd0830d010` |
| Live P3.1 DriveSafe / rollback | `sha256:710177ba4d81d76dbddef52d6a2215dc6eeaff3e8f9f777d6c5418e9e5feda0f` |

Final source hashes and package bases are frozen in the machine manifest. Protected data/configuration/logs remain under `/home/hthnh/p3-2-investigation/20261024T090314Z`; verification artifacts are under `/tmp/ecuread-p3-2-20261024T090314Z`. Disposable containers, network and PostgreSQL volume were removed; temporary browser credentials were deleted. No dependency pull or model training occurred.

## Separately authorized deployment plan

1. Recheck current images/source hashes, schema `b7f2c9a4d610`, active bundle/artifacts, resources and zero in-flight requests. Preserve newer changes and create fresh protected database/config/Analyzer-data backups and immutable rollback references. Do not restore old database snapshots as routine image rollback.
2. Create a persistent shared DriveSafe state directory owned by the candidate's actual app UID/GID, mode 0700. Bind it to `/app/instance/manual-analysis` for every web worker/container. Do not place it in transient container storage. Keep it and Analyzer's cache/data backed up.
3. Use the reviewed runtime values: Analyzer `RAW_ANALYSIS_TIMEOUT_SECONDS=300`; DriveSafe `ML_API_TIMEOUT_SECONDS=360`, `HARNESS_ASYNC_ENABLED=true`, `HARNESS_STATE_DIR=/app/instance/manual-analysis`, `HARNESS_MAX_PENDING=2`, `HARNESS_CLAIM_LEASE_SECONDS=420`, worker timeout 420. If increasing compute capacity later, coordinate **all** limits; do not leave a shorter client, lease or proxy timeout. Keep the Analyzer URL direct/internal.
4. Apply/review the 420-second read/send directives for the existing DriveSafe NPM host, preserving its existing TLS/security/SSE configuration; validate Nginx before an authorized reload. Do not alter Cloudflare security. The asynchronous browser path still returns promptly.
5. Deploy the immutable Analyzer candidate first, verify health/bundle, then the DriveSafe candidate with the persistent mount and direct Gunicorn command. Use `--no-build --pull never --no-deps`; no migrations or floating package/model changes. The reviewed Docker startup no longer automatically runs `flask db upgrade`, matching the existing explicit production Gunicorn override.
6. Perform read-only health/login/Guest/EN/VI/session/status/performance/data checks. Starting the worker may resume only previously accepted persisted manual attempts; it must not scan/reanalyze the fleet. This plan does not itself authorize any production rerun.
7. On a confirmed compatibility regression, stop new manual acceptance, settle active work where possible, then image-only rollback DriveSafe and Analyzer in reverse order to the exact live IDs above. Preserve the durable state directory, Analyzer reservations/cache, RAW, database and completed results. An older web image cannot consume new queued files; retain them for deliberate recovery after restoring a compatible executor. Do not discard unconfirmed work or routinely restore the database.

## Remaining limitations

The execution budget remains a configurable cooperative guard, not an unconditional hard-real-time kill; a long stage may overrun a checkpoint. Long-term disk/cache retention and shared-host saturation still need operational capacity management. The executor is bounded for a single host/shared local filesystem, not a distributed queue. Durability depends on the persistent state directory and Analyzer/database storage; full storage loss or forced Analyzer death before completion can require explicit recovery/recomputation under the same reserved identity.

**Ready for deployment review. Production remains unchanged, and no production session was reanalyzed.**
