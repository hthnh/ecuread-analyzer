# P3 production rollout

Completed 2026-10-24T07:52:41.034157+00:00. **PASS: the verified P3 DriveSafe image is deployed and read-only production acceptance passed.** No production session was reanalyzed. P3.1 interrupted-capture work is separate and is not included in this deployed image.

## Immutable deployment and compatibility

- DriveSafe: `sha256:7bfe65816c9ed399833f59117fab67d0a7542fc3ea3777ff04a4c09bb4493dca`; tag `ecuread-p3-20261024t022127z-candidate:drivesafe`.
- New web container: `5d2bc49e7715e8be1b2f0cda583d6c5f510ddc8673c9587b0f996b25b9e923dc`. Health returned HTTP 200 with PostgreSQL/Redis OK in 10.12 seconds.
- Rollback image: `sha256:c19e057b332a5dd821d0f88ecc7670a86459885518583757bc84a0b497bd9125`, retained as `ecuread-p3-rollback:20261024t074257z`.
- Analyzer remains `sha256:9d682df1aa2c24e8197f853a75ffee3ca48accc36f1a4005bce2bddd0830d010` in its original container `db15985c6ad2ce86df24ed5d8cc3f82267c9391009ab92b19733c378ef43fd75`. It was not rebuilt, restarted or reconfigured.
- Schema remains `b7f2c9a4d610`; bundle `native-full18-20261011T135838Z`, manifest `e84a8350aae4708a9e429a313e90f8ee1a625209705a6df48141133f1a8bd5ae`. All 22 model-file hashes match the verified P3 baseline.

Preflight verified exact candidate/tag identities, unchanged live image/source files, no writable-container source drift, zero pending/submitted analysis claims, an idle RAW admission lock and compatible resolved Compose configuration. Available resources were 12.42 GiB disk and 4.51 GiB RAM. Newer unrelated working-tree changes were preserved. No image build, dependency pull, migration or default Compose/tag rewrite occurred.

Fresh protected PostgreSQL custom-format and full Analyzer-data archives, Compose/environment files, resolved runtime configuration and container inspections are in `/home/hthnh/p3-rollout/20261024T074257Z` (directory 0700, sensitive files 0600). Both archive listings were validated. Their hashes/sizes are recorded in [machine evidence](P3_PRODUCTION_ROLLOUT.json). The web environment, mounts and network memberships exactly match the previous container; PostgreSQL and Redis retain their original containers and volumes.

## Read-only browser/API acceptance

Chromium 149.0.7827.55, production Nginx origin with `Host: drivesafe.top`, exercised authenticated pages using a temporary signed owner session. No password login POST or Harness rerun was sent. Browser routing rejected every method except GET/HEAD.

- Dashboard, Recent Rides, session 242 and login page: HTTP 200; anonymous analysis API: HTTP 401.
- Re-run Harness action is visible, enabled and outside every closed disclosure for session 242's existing successful server result. EN label is `Re-run Harness Analysis`; VI label is `Phân tích lại bằng Harness`.
- EN→VI switching, mobile viewport, Guest session and read-only Guest behavior passed. Guest has no rerun action.
- Timeline selection and event links work; technical details remain collapsible. No browser JavaScript errors.
- Three initial session-242 navigations made **zero full samples requests** and created zero chart canvases. Expanding charts fetched samples once and produced four charts. A language-switch navigation with the charts still open legitimately fetched its own chart data.
- The action was never clicked. Both services recorded zero analysis POSTs during the rollout; no unexpected worker exits/timeouts, errors, OOM or container restarts. Exactly two web workers booted normally.

The complete P3 regression result remains 116 DriveSafe and 26 Analyzer passing tests from the verified immutable candidate; production acceptance is HTTP/browser verification, not execution of a mutating test suite against production.

## Production-origin response times

Five bounded serial requests per route before and after deployment, same Nginx path, authenticated owner, EN locale, compression and no-cache policy. Seconds below are total HTTP response time; TTFB, body bytes and individual samples are in machine evidence. Local HTTP has no TLS phase. OS/database caches were not flushed, the host is shared, and fresh archive readability verification overlapped part of the before batch. The before maxima show transient contention; medians and observed maxima are reported, not p95 or a concurrency guarantee.

| Route | Before median | After median | P3 isolated staging median | Median reduction | After observed max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/` | 11.687 | 3.402 | 3.348 | 70.9% | 3.538 |
| `/sessions` | 1.074 | 0.144 | 0.139 | 86.6% | 0.269 |
| `/sessions/242` | 4.085 | 1.451 | 1.377 | 64.5% | 1.593 |
| `/api/sessions/242/analysis` | 5.237 | 2.720 | 2.784 | 48.1% | 2.748 |

Production results are consistent with the isolated P3 improvements. Public `https://drivesafe.top/auth/login` returned **HTTP 200** from this environment during this rollout. This differs from the earlier P3 investigation's 403. It establishes current public login-page reachability; one request is not a representative latency comparison. No WAF/Tunnel/security settings were changed.

## Preservation and rollback

Before/after deterministic SQL hashes are identical for all telemetry (365,478,573 serialized bytes), analysis results (54,403,750 bytes), ride sessions, synchronization batches and RAW artifact rows. Production retains 59 sessions and 69 results. Session 242 retains 12,625 records and selected run `analysis_20261024T020849Z_9324ea85a0c5` (result 86), with original Pi 80, P1.5 84 and P2.5 85 also accessible. Every existing session-242 analysis API field matches the baseline after removing additive P3 rerun state. RAW/canonical data, timestamps, sequence numbers and models were not rewritten. Session 239 remains in its preexisting failed state; no recovery analysis was authorized here.

No rollback was needed. On a confirmed deployment regression, use the pinned image-only rollback, preserving stored data and schema:

```bash
cd /home/hthnh/drisafe
docker compose -f docker-compose.yml -f /home/hthnh/p3-rollout/20261024T074257Z/drivesafe-rollback.json up -d --no-build --pull never --no-deps web
```

Do not rebuild/restart Analyzer or restore PostgreSQL as a routine image rollback. The protected candidate override is `/home/hthnh/p3-rollout/20261024T074257Z/drivesafe-candidate.json`; the deploy used this reviewed override with `--no-build --pull never --no-deps web`.

## Remaining limitations

Authenticated public-path browser acceptance and a password-based sign-in were not exercised; the public login GET was reachable. The dashboard still takes about 3.4 seconds and the analysis API about 2.7 seconds; this is a bounded serial check, not fleet-capacity testing. P3 retains the completed-only manual capture rule; P3.1 will correct interrupted-capture eligibility as a separately reviewed change. No production inference, automatic analysis, new model or unrelated refactor was introduced.
