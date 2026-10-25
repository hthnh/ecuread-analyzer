# P1.5 — Final production canary

**PASS: real production session 242 completed one server Model Harness analysis, persisted its diagnostic evidence, and displayed the selected server result through the normal DriveSafe session page in English and Vietnamese.** No other session was processed.

Date: 24 October 2026, Asia/Saigon. The manual request started at `2026-10-23T23:22:02.603709Z` (06:22:02 local) and returned HTTP 200 after **51.156 seconds**. This final result supersedes the earlier stopped preflight: the user explained the intervening Compose rebuild and explicitly authorized the focused runtime check and production canary.

Machine evidence: [P1.5 production acceptance JSON](P1_5_PRODUCTION_CANARY_2026_10_24.json). Reference procedure and frozen source manifests: [P1 integration report](P1_HARNESS_DRIVESAFE_INTEGRATION.md) and [P1 staging evidence](P1_STAGING_REGRESSION_2026_10_23.json).

## Production implementation and deployment status

**Both running applications already contain the complete verified P1 implementation, so redundant deployment was skipped.** Their existing containers and newer production contents were preserved. No image was rebuilt or pulled, no container was recreated, and no configuration, model or schema was changed.

| Component | Running immutable image ID |
| --- | --- |
| Analyzer | `sha256:94e35f2701f74616fa38ff26492e573416720032a168f2218c854df4551ee14e` |
| DriveSafe | `sha256:2c18966e5cefb3147632df4897bce2340b7e1b232144c2c0cca3c893ddf4dc8d` |

| Component | Container ID, unchanged through acceptance |
| --- | --- |
| Analyzer | `caf3f3604a8806615a6a8d8297c19cccf00473975ea7e820b3870dec550f4bea` |
| DriveSafe | `72728df914024e4c309b6ceebbe9b5e09d586f28c738bdde8a776bc46fba17ec` |

Compatibility was established from running files and configuration, independently of image labels or build success:

- Analyzer: all 163 packaged files from the 164-file P1 source manifest match byte for byte. The sole absent file is the build-only `Dockerfile`, which is not copied into the runtime image. This includes the RAW endpoint, Harness, aggregation, RCA and diagnostic-evidence implementation.
- DriveSafe: all 248 files in the P1 source snapshot, incorporating the recorded dashboard follow-up, match byte for byte. This includes the evidence consumer, manual rerun workflow, retry identity, dashboard formatter guard and shared VI/EN localization.
- Running RAW API constants and request schema confirm **20,000 records / 8,388,608 bytes**. Window size remains 50, step 10. Training API remains disabled.
- Analyzer runtime is Python 3.12.13, scikit-learn 1.9.0, NumPy 2.5.4 and pandas 2.3.3. DriveSafe uses Python 3.13.15, Flask 3.1.3, Gunicorn 23.0.0 and SQLAlchemy 2.0.54. The Analyzer client timeout is 60 seconds.
- Active bundle pointer, manifest and all five model/reference artifacts match the P1 SHA-256 fingerprints. Database revision is `b7f2c9a4d610` before and after; no migration was necessary.
- The Analyzer RAW lock was idle, and there were zero `pending`/`submitted` sessions before starting. Host inspection showed 13 GiB disk available and approximately 4.3 GiB available RAM; neither application has an explicit Docker memory cap.

The frozen candidates remain available and were not deployed because their implementation was already present:

| Candidate | Image ID |
| --- | --- |
| `ecuread-p1-20261023-verified-analyzer:candidate` | `sha256:422b92cca47091a8c6787f578d729f082da199d26318106ac14d45f52c86601a` |
| `ecuread-p1-20261023-verified-web:dashboard-fixed` | `sha256:80bf7c69fbc33e9a80b3449df00fe64face8a0f9b1cca9e7d62cffd047f3005b` |

## Protected backup and rollback targets

Before the canary, protected backups and configuration snapshots were created under:

`/home/hthnh/p1-rollout-backup/p1-5-final-20261023T231909Z/`

The directory is private. It contains a PostgreSQL custom dump (**19,050,385 bytes**), Analyzer data archive (**61,636,091 bytes**), Compose files, protected environment/container configuration, preservation fingerprints and verification artifacts. The dump's catalogue was successfully read with `pg_restore --list`; no database restore was performed.

Rollback image tags point to the actual inspected running images, preserving the user's Compose rebuild:

- `ecuread-p1-5-rollback:analyzer` → `94e35f2701f7…`
- `ecuread-p1-5-rollback:web` → `2c18966e5cef…`

No rollback was needed. If a later image rollout requires rollback, retain the reviewed image-only procedure: settle manual requests, restore DriveSafe first then Analyzer with `--no-build --pull never --no-deps`, and preserve results and RAW. Routine rollback must not restore this dump or downgrade the schema.

## Single real-session execution

| Item | Observed result |
| --- | --- |
| DriveSafe session | **242** |
| Session UUID | `80d11ec8-7b8f-5731-a7c3-828b60eb480e` |
| Capture source / status | `pi_native`, `kline`, capture completed, sync complete |
| Original Pi result ID | **80** |
| Original Pi run | `87f16d88-91af-476e-af77-32c079637b36` |
| Original Pi model | `iforest-baseline-20260920T091709Z` |
| Manual workflow | `POST /api/sessions/242/analysis/re-run` |
| Fixed manual request ID | `p1-5-prod-242-20261023T231909Z` |
| New server result ID | **84** |
| New server run | **`analysis_20261023T232221Z_2c818f9e015c`** |
| Final session state | `completed`; selected analysis is the new server run |
| Input / persisted server canonical samples | **12,625 / 12,625** |
| RAW and timestamp counts | **12,625 each**, sequences 0–12,624 |
| Analysis windows | **1,258**, length 50, stride 10 |
| Last window | Sequences 12,570–12,619; remaining five samples retained in canonical telemetry |
| Provenance | `stored_raw`, reconstructible RAW, active server model, no missing evidence/signals |
| Input SHA-256 recorded by rerun | `d66674cd86951f9cd61e106713b378abfb5db8d6b55bf57460fac28a9d462819` |

The request used the existing authenticated DriveSafe route, normal CSRF validation, atomic claim and Analyzer HTTP client. No direct Analyzer request substituted for the workflow. The authorized session owner was represented by a temporary signed application session; no password reset or account modification was used. Before the rerun, the normal API selected the original Pi-local result; afterwards it selected Server Harness.

One subsequent POST reused **the same manual request ID** to verify recovery semantics. It returned HTTP 200 in **8.374 seconds** with the same stored server run. The database retained exactly two session results: the original Pi result and one new server result. Logs show **one Analyzer decode/analyze POST**, and two DriveSafe manual POSTs (initial action plus stored-result reuse). Exactly one new Analyzer run directory was created. There was no independent second analysis, transport timeout or recovery rebuild.

## Active Harness and actual diagnostic output

Bundle: `native-full18-20261011T135838Z`; manifest SHA-256 `e84a8350aae4708a9e429a313e90f8ee1a625209705a6df48141133f1a8bd5ae`.

| Detector | Version | Execution / session applicability | Scored windows | Positive windows | Not applicable windows |
| --- | --- | --- | ---: | ---: | ---: |
| Isolation Forest | `iforest-full18-20261011T135838Z` | `ok` / applicable | 1,258 | 69 | 0 |
| Contextual Battery | `contextual-battery-robust-v1-ef5579cf38a0` | `ok` / applicable | 1,256 | 1 | 2 |
| Contextual Speed | `contextual-speed-robust-v1-fce8419c1951` | `ok` / applicable | 1,213 | 1 | 45 |

Execution and applicability are distinct: all three detectors executed successfully, while battery had two unmodeled-context windows and speed had 45 windows reporting `missing_speed_or_invalid_time`. These windows are represented as not applicable, rather than normal findings. No detector execution failed.

Coverage is **1,213 full / 45 partial / 0 none** windows. Independent read-only checks of all persisted window payloads reproduce those counts, all detector scored/positive counts and **68 disagreement windows**. The top-level state is `detector_disagreement`, its presence flag is true, and session window coverage is partial. All three detectors have at least one positive finding somewhere in the session; that session summary does not remove disagreements within individual windows. No score fusion or consensus diagnosis was applied.

The persisted diagnostic evidence contains **6 observations, 1 symptom, 0 possible causes and 9 recommended checks**. The normal API exposes the same six observations, one symptom and nine checks. Actual observation statements are:

1. Aggregate detector evidence state preserved.
2. Isolation Forest reported a pattern outside its learned reference distribution.
3. RPM/TPS feature families were involved in the detector observation.
4. A constant or low-variance signal feature was observed in the IF unusual-feature list.
5. Contextual battery-voltage deviation was observed.
6. Active detector disagreement is preserved without consensus diagnosis.

The supported symptom is **“Contextual supply-voltage deviation.”** One underlying battery window includes a 10.7 V minimum against a 14.1 V reference median; this is detector evidence, not a confirmed component failure. Possible causes legitimately remain empty.

The nine persisted check descriptions cover IF feature review, reproducing the RPM/TPS operating state, cross-session repeatability, RAW/timestamp/checksum integrity, known-stimulus signal response, voltage comparison within similar contexts, external supply measurements, review of each detector's scope, and the boundary between no anomaly evidence and verified health. Their full text, deterministic priorities and check IDs are recorded in the machine evidence. They were generated by the existing RCA v2 pipeline; no findings or causes were manufactured.

The primary IF timeline is `available` and contains **69 actual positive-window intervals** for the selected server run. Persisted window boundaries and timeline item count agree. The browser renders an interactive timeline, and clicking a segment displays its IF window evidence. It does not borrow the old Pi result's anomaly flags.

## Browser, API and operational acceptance

Production acceptance used the existing Nginx origin with `Host: drivesafe.top`, the existing Gunicorn workers, live Analyzer and production database. Chromium 149.0.7827.55 rendered the production session and its actual assets. Browser requests for the HTTPS application URL were routed to that origin for verification; this is not a public Cloudflare/TLS acceptance claim.

| Check | Result |
| --- | --- |
| Analyzer final health | HTTP 200, `status=ok`, model loaded |
| DriveSafe final health | HTTP 200; database and Redis both `ok` |
| Authenticated session API / detail page | HTTP 200; new run selected, Server Harness origin |
| Detector execution/applicability table | Three rows; actual coverage and disagreement displayed |
| Observations / symptom / checks / empty causes | Persisted evidence consumed by normal detail page |
| Timeline and charts | Timeline interaction passed; four chart canvases rendered |
| VI → EN switching | Correct HTML locale and selected run after reload; zero localization-gap markers |
| Dashboard hotfix | Authenticated dashboard HTTP 200 in VI and EN |
| Login page / Guest Mode | Login page HTTP 200; Guest Mode entry and guest dashboard HTTP 200 |
| Desktop / mobile | Rendered successfully; protected screenshots retained |
| Browser errors / failed resource requests | **0 / 0** |
| Analyzer / DriveSafe error logs during acceptance | No `ERROR`, `CRITICAL`, exception or traceback entries |
| Final in-flight work | **0** pending/submitted sessions |

Peak observable memory during the initial analysis request:

| Container | Sampled cgroup usage peak, 200 ms sampling | Cgroup lifetime peak after request |
| --- | ---: | ---: |
| Analyzer | **592.69 MiB** | **619.68 MiB** |
| DriveSafe | **828.84 MiB** | **829.09 MiB** |

Sampling ran on the host for 256 observations. Values include all container processes and accounted cache; they are not incremental model RSS. Lifetime peaks include earlier container activity. This establishes one measured manual workload, not concurrent or automatic fleet capacity.

Public `https://drivesafe.top/auth/login` returned **Cloudflare HTTP 403 from this execution environment**. Origin login, Guest Mode, authenticated dashboard and session acceptance all passed. Public ingress behavior for the user's browser remains unverified by this test; no Cloudflare or proxy configuration was changed.

## Historical preservation and side effects

Before/after SHA-256 fingerprints match for:

- **Every stored telemetry row**, including historical RAW and original canonical columns: 365,478,573 bytes of ordered row serialization.
- The **entire original Pi result row 80**, including its original summary and identity.
- All analysis results and ride-session rows belonging to other sessions.
- All sync-batch and RAW-artifact rows.

The original Pi run remains readable by stored result ID 80 and its run ID. The session detail page now selects the new server run; switching the browser back to the historical Pi result was not part of the existing UI acceptance. Both original and server results remain stored.

Of 318 pre-existing Analyzer files, **316 are byte-identical and none are missing**. The only changes are normal registry timestamp rewrites: decoder registry `updated_at` and active-model registry `created_at`. Their non-timestamp content is unchanged. All model artifacts, pre-existing telemetry, prior analysis payloads and existing session files remain unchanged. The new run adds its analysis/window/feature artifacts and the session's new server canonical telemetry; it does not replace historical Pi data.

No rollback, model retraining, new detector, LLM, database migration, Pi5 change, historical bulk analysis or automatic trigger was performed. Automatic analysis was not enabled. Unrelated working-tree changes were preserved. Temporary authentication material was kept outside the repositories in the protected verification directory and removed after acceptance.

## Final verdict

**The single production canary succeeds.** The real 12,625-record Pi5 session executed the active three-detector server Harness, aggregation, RCA v2 and diagnostic-evidence pipeline. Exactly one server result was added and selected; DriveSafe displays its evidence and real IF timeline in Vietnamese and English. The original Pi result and historical RAW/canonical evidence are preserved.

Remaining limits are public Cloudflare ingress verification, the existing lack of a validated browser path back to the old Pi result, and capacity beyond this one manual request. No application error was reproduced during origin/browser acceptance. Stop here: no additional session analysis is authorized by this canary task.
