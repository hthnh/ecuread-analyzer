# P2.5 — Production diagnostic interpretation canary

24 October 2026, UTC. **P2 is deployed and the single authorized production canary passed.** Session **242** now selects P2 run **`analysis_20261024T014120Z_f53946d6f290`**, database result **85**. All **12,625 RAW records** were included and **1,258 windows** generated. The normal DriveSafe session page displays measured, event-linked diagnostic interpretation in English and Vietnamese. The original Pi and P1.5 results remain unchanged.

Public Cloudflare ingress returns **403 from this test environment**. Browser and API acceptance exercised the actual production Nginx origin, Gunicorn, Analyzer and PostgreSQL. External ingress acceptance remains unverified; this limitation did not require an image rollback.

Machine evidence: [P2.5 measurements and preservation checks](P2_5_PRODUCTION_INTERPRETATION_CANARY.json). Implementation reference: [P2 diagnostic interpretation](P2_DIAGNOSTIC_INTERPRETATION_QUALITY.md). Deployment/rollback reference: [P1 integration plan, section 8](P1_HARNESS_DRIVESAFE_INTEGRATION.md#8-exact-production-deployment-and-rollback-plan-not-executed). Previous production baseline: [P1.5 canary](P1_5_PRODUCTION_CANARY.md).

## Deployment identities and source

| Service | Deployed immutable image ID | Candidate tag |
| --- | --- | --- |
| Analyzer | `sha256:9d682df1aa2c24e8197f853a75ffee3ca48accc36f1a4005bce2bddd0830d010` | `ecuread-p2-5-20261024t011304z:analyzer` |
| DriveSafe | `sha256:c19e057b332a5dd821d0f88ecc7670a86459885518583757bc84a0b497bd9125` | `ecuread-p2-5-20261024t011304z:drivesafe` |

The production Analyzer container is `db15985c6ad2ce86df24ed5d8cc3f82267c9391009ab92b19733c378ef43fd75`; DriveSafe is `48795bbad3c98a955ae74bf978c487e01b07ee2d948711ba6cfc2ae3cd88777f`. Final inspection at **01:47:19 UTC** confirmed those images, zero restarts, no OOM kill, healthy services and no in-flight analysis.

Candidates were built from the exact existing production image layers plus the nine reviewed P2 runtime files. Their SHA-256 fingerprints match the P2 review and are recorded in the machine report. Analyzer changes cover interpretation, aggregation, RCA and Harness propagation. DriveSafe changes cover the existing evidence consumer, presentation catalogue, session template and JavaScript. No floating dependencies were resolved or installed. Current source was checked again after rollout and still matches those fingerprints.

Actual production files were compared before overlaying P2. The user-rebuilt DriveSafe baseline, dashboard hotfix, P1.5 manual workflow and localization were retained. The unrelated dirty working trees were preserved without reset, staging or commit.

| Runtime | Preserved versions |
| --- | --- |
| Analyzer | Python 3.12.13; FastAPI 0.161.1; Uvicorn 0.54.0; NumPy 2.5.4; pandas 2.3.3; scikit-learn 1.9.0; joblib 1.6.0; Pydantic 2.14.1 |
| DriveSafe | Python 3.13.15; Flask 3.1.3; Gunicorn 23.0.0; SQLAlchemy 2.0.54; psycopg 3.3.6; requests 2.34.2 |

## Readiness, backups and deployment

Before changing containers, the production analysis lock was available, no session was pending/submitted, session 242 had 12,625 records and exactly its two previous results, and the database revision was **`b7f2c9a4d610`**. The active bundle and required artifacts were verified against the preserved production runtime. Resources were sufficient for the measured single-request workload; final available disk was **13,447,434,240 bytes** and host available memory **4,191,116 KiB**.

Focused candidate regression passed **26 Analyzer tests** and **22 DriveSafe tests**, including P2 interpretation, existing evidence semantics and localization. A disposable container stack used production runtime packages, Nginx timeouts, Gunicorn and isolated PostgreSQL. Its complete 12,625-record fixture produced 1,258 windows in **31.794 s**, with all detector-window payloads identical to its frozen P1 baseline. Its actual upstream response was **12,172,617 bytes**. Disposable containers and PostgreSQL volume were removed after acceptance. This fixture was separate from production session 242.

Protected backup directory:

`/home/hthnh/p2-5-rollout-backup/ecuread-p2-5-20261024t011304z`

The directory is mode 0700; protected database/configuration files are mode 0600. Backups completed at **01:36:14 UTC**:

- PostgreSQL custom dump: **19,698,607 bytes**, verified using `pg_restore --list`.
- Analyzer data archive: **64,116,028 bytes**.
- Original Compose files, protected environment and container configuration, source/image identities, historical SQL hashes and existing Analyzer file hashes.

Exact rollback references are retained:

| Service | Rollback tag | Previous immutable image ID |
| --- | --- | --- |
| Analyzer | `ecuread-p2-5-rollback:analyzer` | `sha256:94e35f2701f74616fa38ff26492e573416720032a168f2218c854df4551ee14e` |
| DriveSafe | `ecuread-p2-5-rollback:drivesafe` | `sha256:2c18966e5cefb3147632df4897bce2340b7e1b232144c2c0cca3c893ddf4dc8d` |

Analyzer was deployed first and checked healthy; DriveSafe followed. Both were healthy by **01:38:40 UTC**. Compose used exact candidate overrides with `up -d --no-build --pull never --no-deps`, preserving existing volumes, networks and environment. DriveSafe starts Gunicorn directly; no `flask db upgrade` or database migration was executed. PostgreSQL and Redis containers were not replaced. No compatibility failure or rollback occurred.

These reviewed overrides remain part of the deployment configuration; no default Compose file or floating `latest` image alias was rewritten. To reproduce the deployment, use the protected `analyzer-candidate.json` and `drivesafe-candidate.json` overrides with their respective repository Compose files. A bare Compose rebuild is not this verified deployment.

If rollback is subsequently required, stop new reruns, wait for in-flight work to settle and use `drivesafe-rollback.json` first, then `analyzer-rollback.json`, with the same `--no-build --pull never --no-deps` flags. The protected `production.py rollback` implements that image-only procedure. Preserve all newly stored results and RAW; do not restore the database backup as a routine image rollback.

## Single production execution and payload compatibility

Only session **242**, UUID **`80d11ec8-7b8f-5731-a7c3-828b60eb480e`**, was reanalyzed. At **01:41:11 UTC**, the existing authenticated, CSRF-protected DriveSafe manual route received:

`POST /api/sessions/242/analysis/re-run`

Fixed request identity: **`p2-5-prod-242-20261024T011515Z`**. Input provenance is stored RAW (`kline`), decoder `honda_keihin_71_17:1.0.0`, with **12,625 RAW, timestamp and speed records**. Input SHA-256 is `d09fef1c55be107503673ac95c5b5df1181d00930cd7c06d5f3736083803e5f7`. The request produced one logical analysis and one new result. HTTP returned **200 / completed in 41.190 seconds** without a transport timeout or recovery rerun.

| Actual serialized HTTP body | Bytes |
| --- | ---: |
| Browser/manual rerun request JSON | 48 |
| DriveSafe → Analyzer complete RAW request JSON | 1,448,094 |
| Analyzer → DriveSafe response, including canonical samples | 12,503,797 |
| DriveSafe manual rerun response | 1,427,775 |

Sizes count serialized bodies, excluding HTTP headers. The exact upstream response size was measured by retrieving the already completed Analyzer idempotency-cache response using the identical request body/key. The cache and run identity were asserted before retrieval, and the analysis directory set stayed unchanged. Its HTTP `Content-Length` was **12,503,797**, with no content encoding. This retrieval did not execute the models again.

Runtime limits were verified: RAW **20,000 records / 8,388,608 bytes**, body read **10 s**, cooperative analysis **45 s**, DriveSafe Analyzer client **60 s**, Nginx connect/send/read **90 s**, Gunicorn **120 s**. DriveSafe's **2,097,152-byte inbound request guard** does not cap its upstream response. Production Nginx has `client_max_body_size 2000m`; no explicit response-size cap was found. The measured complete request and response passed the actual runtime paths.

A second manual-route POST used the **same identity** solely to verify stored-result reuse. It returned the same run and left the session result count at **three**. Thus two manual HTTP requests and two upstream HTTP requests are visible in logs, but there was **exactly one new production inference run**. Browser acceptance made **zero analysis POSTs**. No other production session was analyzed.

## Models, detectors and diagnostic output

The active bundle remains **`native-full18-20261011T135838Z`**, manifest SHA-256 **`e84a8350aae4708a9e429a313e90f8ee1a625209705a6df48141133f1a8bd5ae`**. All five model/reference artifact hashes and the active model selection match P1.5. Artifact identities are included in the machine report.

| Detector | Execution | Scored / positive / not applicable windows | Model version |
| --- | --- | --- | --- |
| Isolation Forest | `ok` | 1,258 / 69 / 0 | `iforest-full18-20261011T135838Z` |
| Contextual Battery | `ok` | 1,256 / 1 / 2 | `contextual-battery-robust-v1-ef5579cf38a0` |
| Contextual Speed | `ok` | 1,213 / 1 / 45 | `contextual-speed-robust-v1-fce8419c1951` |

All three detectors are applicable somewhere in the session; per-window inapplicability remains explicit. Coverage is **1,213 full / 45 partial / 0 none**. There are **68 disagreement windows**, **1,149 full-coverage agreement windows** and **one overlapping positive window**. All three session-level findings are positive, which does not imply they agreed in every window. No score fusion or voting was introduced.

Health Score **86.07**, status **`monitor`**, IF anomaly count **69**, anomaly ratio, bundle and all **1,258 complete detector-window payloads match P1.5 exactly**. No unexpected detector-output difference required investigation. The persisted IF anomaly timeline retains **69 original positive-window intervals**; the browser exposes 10 timeline controls and opens the original window details.

| Interpretation | P1.5 | Production P2 |
| --- | ---: | ---: |
| Observations | 6 | 16: 12 primary, 4 technical |
| Symptoms | 1 | 1 |
| Possible causes | 0 | 0 |
| Deduplicated checks | 9 | 10 |

P2 persisted `evidence-grounded-interpretation-v1`, **14 finding regions** and **11 reviewed representative finding/coverage events**. There are 37 coverage-gap regions before bounded representative selection. These are grouped evidence regions and interpretation counts, not independent vehicle failures. The primary UI shows up to six observations with a shown/total count; complete evidence remains stored.

Actual findings now answer what happened, under which operating conditions, with which measurements and when:

- **Voltage:** window 0, sequences **0–49**, **0–12.250333516 s**. Minimum supply voltage **10.7 V**, observed contextual median **14.1 V**, difference **−3.4 V**, idle/closed-throttle context. Robust deviation **5.0962222222**, threshold **4.5**. This is a statistical contextual reference, not an OEM specification or a component-failure diagnosis.
- **Speed:** window **183**, sequences **1,830–1,879**, **474.023205982–486.274453254 s**. Median recorded speed **46 km/h**, engine speed **4,942.5 rpm**, TPS **0.5078125 V / raw 0**, speed-change rate **−5.4987156063 km/h/s**, dispersion **1.1014576079 km/h/s**, moving/mid-load context. Speed-rate robust deviation **4.9457776342**, threshold **4.5**. Missing contextual numeric reference medians and independent calibration remain explicit.
- **IF:** signal-family involvement includes measured witness values, for example first-window engine temperature **33 → 34 °C**, delta **+1 °C**, median **33 °C**. The description does not infer overheating or a physical cause from feature involvement.

Each finding links to the original event, run, representative window and `analysis_windows.jsonl` reference, with supporting/limiting detectors and coverage. Checks explain the evidence they seek: safe stationary independent voltage/connection verification, comparison with existing recordings under similar RPM/TPS conditions, and corroboration of recorded speed/RPM/TPS and timestamps. The page has one deduplicated recommendation list. Unsupported causes remain empty because no independent physical/operator evidence opens the existing cause gates. No replacement, transmission-failure, wheel-slip or ECU-write claim was added.

## Browser and API acceptance

Chromium **149.0.7827.55** exercised the normal production session page, authenticated dashboard, timeline, charts, locale switching and Guest Mode. Browser HTTPS URLs were routed to the existing production Nginx HTTP origin while preserving the normal host and secure session cookies. The public Cloudflare path was checked separately and returned **403**.

| Acceptance | Result |
| --- | --- |
| `/sessions/242`, selected server analysis | HTTP 200, result 85 / new P2 run |
| Session analysis API, EN and VI | HTTP 200, selected new run and persisted evidence |
| Dashboard EN and VI | HTTP 200; existing dashboard hotfix preserved |
| Login page / Guest Mode / Guest dashboard | HTTP 200 |
| Numeric voltage/speed findings and event links | Present; event links open original event details |
| Technical details / recommendations | Collapsible; one list, no duplicate analyst/check tables |
| Locale switching | Both locales reload correctly; zero localization-gap markers |
| Charts / IF timeline | Four chart canvases; timeline event selection works |
| Desktop / mobile | Rendered and screenshots retained privately |
| Browser errors / failed requests | Zero / zero |

Localized persisted-page content includes EN **“Window minimum supply voltage was 10.7 V versus the observed contextual median 14.1 V (difference -3.4 V).”** and VI **“Điện áp nguồn nhỏ nhất cửa sổ là 10.7 V so với trung vị tham chiếu theo bối cảnh 14.1 V (chênh lệch -3.4 V).”** Both contain the real 46 km/h speed context and localized checks. Repeated generic aggregate/algorithm descriptions do not dominate the primary observations.

Authenticated acceptance used a short-lived signed session for the existing session owner plus normal CSRF, without changing a password or account. Login-page and Guest authentication paths were exercised; an interactive password submission was not tested. Temporary owner credentials were removed after acceptance. Screenshots and full telemetry-bearing responses remain in protected local storage rather than this repository.

## Resources, errors and historical preservation

The canary sampled container cgroup memory every **200 ms** for **206 samples**. These are container working memory totals, not individual Python heap measurements.

| Container | Pre-request current memory | Sampled canary peak | Cgroup lifetime peak after request |
| --- | ---: | ---: | ---: |
| Analyzer | 126.91 MiB | **407.75 MiB** | 429.75 MiB |
| DriveSafe | 620.25 MiB | **746.78 MiB** | 756.78 MiB |

Cgroup lifetime peaks include earlier health/smoke activity; the DriveSafe lifetime peak was already present before the request. Sampling can miss short spikes. No OOM kill, worker restart, error/critical message or traceback appeared in the canary log interval. No rollback was used. Large canonical responses and DriveSafe memory remain relevant operational limits; this single request does not certify concurrent or fleet capacity.

Historical evidence was compared using deterministic SQL serialization hashes before and after:

- **All telemetry rows:** 365,478,573 serialized bytes unchanged, including all 12,625 records for session 242.
- **Every preexisting analysis result through ID 84:** 27,308,768 serialized bytes unchanged, including full Pi/P1.5 content, timestamps and canonical evidence.
- Other sessions, session 242 immutable input fields, sync batches and RAW artifact rows: unchanged.
- Existing model files, all previous Analyzer analysis artifacts and canonical `samples.jsonl` files: byte-identical; no existing file missing.

Of **324 existing protected Analyzer files**, four metadata files changed only their normal `created_at`/`updated_at` fields: session telemetry metadata, session entity, model-version registry and decoder-version registry. Their sample/model contents did not change. Exactly **three new analysis files** were created for the one P2 run: `analysis_run.json`, `analysis_windows.jsonl` and `window_features.jsonl`.

Final session results are:

| Result | Origin | Run ID | Preservation |
| --- | --- | --- | --- |
| 80 | Original Pi | `87f16d88-91af-476e-af77-32c079637b36` | Original row and evidence unchanged |
| 84 | P1.5 server | `analysis_20261023T232221Z_2c818f9e015c` | Original row, 12,625 canonical records and files unchanged |
| 85 | P2 server | `analysis_20261024T014120Z_f53946d6f290` | New persisted result, 12,625 canonical records; selected by normal session page |

The Pi result's canonical JSON was originally absent and remains absent; preservation does not fabricate a Pi canonical payload. Historical results remain stored/queryable. The normal page selects the current server run; a new historical-run browser selector was outside this rollout.

Database revision remains **`b7f2c9a4d610`**. No migration, model change/retraining, new detector, LLM, Pi5 modification, bulk reanalysis or automatic fleet trigger occurred. Automatic session analysis remains disabled. Final pending/submitted count is zero and the Analyzer analysis lock is free.

## Verdict and remaining limits

**Production P2 rollout and the one-session diagnostic interpretation canary pass.** The deployed normal session page answers the requested behavior, signals/context, measured support, event timing, detector support/limits and next-check questions in both locales, while preserving P1.5 predictions and all historical evidence.

External Cloudflare access could not be accepted from this environment because it returned 403. Cause gates still require independent evidence; speed reference medians/calibration remain unavailable; bounded representative events and six primary visible observations are intentional. Payload/memory measurements establish this single manual workload only. No further production analysis was launched after this canary.
