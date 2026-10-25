# P3.1 production rollout

Completed **2026-10-24T15:47:16.732816+07:00 (Asia/Saigon)**. **PASS: the four-file P3.1 candidate is deployed and read-only production acceptance passed.** Sessions 246, 248 and 249 now expose an eligible manual Re-run action while retaining their original interrupted termination status. No production Harness submission or inference was executed.

References: [P3 production rollout](P3_PRODUCTION_ROLLOUT.md), [P3.1 implementation and source review](P3_1_INTERRUPTED_CAPTURE_ELIGIBILITY.md), [rollout machine evidence](P3_1_PRODUCTION_ROLLOUT.json).

## Image identities and deployment

| Role | Immutable image |
| --- | --- |
| Deployed P3.1 DriveSafe | `sha256:710177ba4d81d76dbddef52d6a2215dc6eeaff3e8f9f777d6c5418e9e5feda0f` |
| Previous P3 DriveSafe / rollback | `sha256:7bfe65816c9ed399833f59117fab67d0a7542fc3ea3777ff04a4c09bb4493dca` |
| Analyzer, unchanged | `sha256:9d682df1aa2c24e8197f853a75ffee3ca48accc36f1a4005bce2bddd0830d010` |

Candidate tag: `ecuread-p31-20261024t083431z:drivesafe`. New web container: `3fd66ad2d31c8b4b5785520051c22fda077ff355696fc46a98fe3258f2d8f006`. Health became HTTP 200 in **6.30 seconds**, with database and Redis OK.

The image is layered directly over the verified running P3 image. Its configuration is identical to its base and it adds exactly four filesystem layers, one per reviewed file:

- `services/analysis_submission.py`
- `services/analysis.py`
- `templates/session_detail.html`
- `static/locales/messages.json`

Source and live-file hashes matched the reviewed P3.1 manifest before packaging. The deployed bytes match those same hashes. No dependency installation/pull, Git-HEAD rebuild, unrelated source file, migration or Compose-default edit was included. Other P3 runtime files, including rerun JavaScript and performance changes, remain byte-identical. Unrelated working-tree changes were preserved.

Deployment used the protected override and existing production project:

```bash
cd /home/hthnh/drisafe
docker compose -f docker-compose.yml -f /home/hthnh/p3-1-rollout/20261024T083431Z/drivesafe-candidate.json up -d --no-build --pull never --no-deps web
```

Only DriveSafe was recreated. Analyzer, PostgreSQL and Redis retain their original container IDs, configurations and start times. Analyzer was not rebuilt, restarted or reconfigured. DriveSafe environment, mounts and network memberships match the previous container.

## Preflight, backups and candidate regression

Preflight confirmed the P3 live image/container, no writable-container code drift, exact hashes for the four reviewed changes, an idle RAW admission lock and zero pending/submitted claims. Schema remains **`b7f2c9a4d610`**. Active bundle remains **`native-full18-20261011T135838Z`**, manifest **`e84a8350aae4708a9e429a313e90f8ee1a625209705a6df48141133f1a8bd5ae`**. All 22 model-file hashes are unchanged. Available disk/RAM were 12.25 / 4.48 GiB.

Protected backups and runtime snapshots are in `/home/hthnh/p3-1-rollout/20261024T083431Z` (directory 0700, sensitive files 0600). A fresh PostgreSQL custom-format dump was created and its archive listing validated, alongside fresh Compose/environment/configuration snapshots. The previous full Analyzer-data archive remains available at `/home/hthnh/p3-rollout/20261024T074257Z/analyzer-data.tar.gz`. Both previous image references remain available.

The fresh rollback reference is **`ecuread-p31-rollback:20261024t083431z`**, resolving to the previous P3 immutable ID above. The protected image-only rollback override pins that exact ID and preserves the runtime environment/command.

**36 focused tests passed in 29.796 seconds against the packaged candidate**, with no runtime source mounts, network disabled and disposable SQLite. The tests include eight P3.1 cases plus P3 rerun/recovery, P1 integration, P2 evidence and localization regressions. They verify completed/interrupted eligibility, incomplete/active/invalid rejection, canonical fallback, request identity, preserved history, CSRF/ownership/Guest checks, terminal evidence restraint, lazy/read optimizations, busy 429 and timeout recovery. Existing SQLite connection ResourceWarnings were observed; no failing test was ignored. The prior source verification's 124 DriveSafe/26 Analyzer checks remain documented separately; they are not represented as a new full production test run.

## Read-only production acceptance

Browser: Chromium 149.0.7827.55, authenticated owner session, production Nginx origin with the real host header. Routing rejected all methods except GET/HEAD. No Re-run button was clicked and no production POST was used to test an error state.

| Session | Stored records | Capture status retained | Synchronization | Visible, enabled eligible action | Historical result IDs retained |
| --- | ---: | --- | --- | --- | --- |
| 242 | 12,625 | `completed` | `complete` | Yes, EN and VI | 80, 84, 85, 86 |
| 246 | 3,352 | `interrupted` | `complete` | Yes, EN and VI | 78 |
| 248 | 12,211 | `interrupted` | `complete` | Yes, EN and VI | 82 |
| 249 | 9,917 | `interrupted` | `complete` | Yes, EN and VI | 79, 83 |

All four session pages returned HTTP 200 in **both EN and VI**. The action remains outside collapsed sections. Session 249's existing server success does not hide its action, and session 242's completed-session behavior is unchanged. The three interrupted captures still display interrupted status and their original `unclean_runtime_shutdown` reasons. Contextual explanations correctly describe abrupt ignition/power switching as possible normal behavior and state that termination alone does not establish a component failure. No localization gaps were found in those capture cards.

Read-only execution of the deployed eligibility helper confirmed all four sessions eligible and not processing. In-memory copies of their metadata, inside a read-only database transaction, verified that incomplete synchronization and active/capturing states remain ineligible; fresh submitted claims report processing and expired claims are retryable. No production metadata was modified to simulate these states. The candidate's real endpoint tests cover their HTTP 409 behavior and busy/retry paths in disposable storage.

Additional production checks passed:

- Dashboard, Recent Rides and login page: HTTP 200.
- Guest recorded session: HTTP 200, no Re-run action; Guest access to real session 246: HTTP 404; anonymous analysis API: HTTP 401. Candidate endpoint tests also confirm Guest write rejection, CSRF and ownership checks.
- Actual language switching, EN/VI labels and mobile rendering passed.
- Timeline selections and event links work; technical details remain collapsible.
- All eight initial session navigations fetched zero full samples responses and created zero chart canvases. Explicit expansion fetched samples once and produced four charts. Locale navigation with already-open charts legitimately loads chart data for the new page.
- No browser JavaScript errors, unexpected worker exits/timeouts, OOM or container restarts. Exactly two web workers booted normally.
- No analysis POSTs in either service's retained rollout logs, no new Analyzer run artifacts, and no new stored results. Busy/timeout handling was verified in the packaged candidate without submitting production reruns.

## Performance preservation

Five bounded serial requests per route before and after rollout used the same Nginx origin, owner, EN locale, compression and no-cache policy. Candidate tests, backups, fingerprints and browser checks did not run concurrently with these HTTP batches. OS/database caches were not flushed; this is a shared host. Times are total HTTP seconds. Individual samples, TTFB, sizes and observed maxima are in machine evidence; p95 is not estimated from five samples.

| Route | Earlier P3 rollout median | P3 immediately before median | P3.1 after median | P3.1 observed maximum |
| --- | ---: | ---: | ---: | ---: |
| `/` | 3.402 | 3.275 | 3.380 | 3.800 |
| `/sessions` | 0.144 | 0.097 | 0.128 | 0.324 |
| `/sessions/242` | 1.451 | 1.426 | 1.476 | 1.574 |
| `/api/sessions/242/analysis` | 2.720 | 2.675 | 2.681 | 2.918 |

**No meaningful performance regression was observed.** Dashboard, large-session detail and analysis API medians remain within about 4% of the immediate baseline and close to earlier P3 production values. Recent Rides increased by about 31 ms versus the immediate batch while remaining faster than the earlier P3 rollout median. These bounded observations do not certify saturation/concurrent fleet capacity.

A single unauthenticated public login GET returned **HTTP 200**. Authenticated browser and repeated route measurements used the production origin; there is no new public Cloudflare route benchmark or latency attribution, and no Cloudflare configuration changed.

## Data, model and runtime preservation

Before/after deterministic SQL hashes are identical for **every telemetry row**, **every analysis result including canonical telemetry**, **ride sessions including capture metadata**, synchronization batches and RAW artifact rows. Specifically, all 59 sessions and 69 analysis results remain stored; schema and zero pending/submitted state are unchanged. The complete telemetry and analysis serialized streams are 365,478,573 and 54,403,750 bytes respectively.

Session 242 retains its 12,625 records, selected run `analysis_20261024T020849Z_9324ea85a0c5` (result 86), original Pi result 80, P1.5 result 84 and P2.5 result 85. Its complete `analysis` API object matches the pre-rollout response exactly, including Health Score, predictions, evidence, timeline and rerun state. The separate `reanalysis_evidence` object adds only `capture_status` and `termination_reason`; all its earlier fields are unchanged.

Sessions 246/248/249 retain every RAW record, timestamp, sequence, fingerprint, termination reason, metadata value and historical analysis. No capture was rewritten to completed. Models, decoder/inference code, thresholds, Health Score logic, Analyzer configuration, Pi capture behavior and automatic-analysis settings were not changed. No inference, retraining, metadata migration or bulk reanalysis occurred.

## Rollback and final verdict

**No rollback was needed.** On a confirmed deployment regression, settle any operator-started work and restore only the previous P3 DriveSafe image:

```bash
cd /home/hthnh/drisafe
docker compose -f docker-compose.yml -f /home/hthnh/p3-1-rollout/20261024T083431Z/drivesafe-rollback.json up -d --no-build --pull never --no-deps web
```

This override resolves to `sha256:7bfe65816c9ed399833f59117fab67d0a7542fc3ea3777ff04a4c09bb4493dca`. Preserve PostgreSQL, RAW, canonical telemetry and history; do not restore the database or touch Analyzer as routine image rollback. Compose defaults and existing tags were not rewritten. Temporary acceptance credentials were removed after completion; the pinned candidate, rollback reference and protected verification evidence remain available.

**Final verdict: P3.1 production deployment and read-only acceptance passed.** The three interrupted sessions are eligible for a later explicit operator-controlled manual rerun. None was analyzed in this rollout. Public authenticated browser acceptance/password sign-in and concurrent load are outside this bounded check; no additional analysis or architecture work was performed.
