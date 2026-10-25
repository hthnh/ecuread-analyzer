# P1 — Harness / DriveSafe integration

Verification date: 24 October 2026, Asia/Saigon (execution artifacts retain their UTC 2026-10-23 names). This work covers the current uncommitted Analyzer and DriveSafe implementations. No training, detector, feature, anomaly threshold, IF health formula, Sync replacement, database migration, fleet reanalysis, or P1 production rollout was performed. After staging, the user separately requested a live dashboard repair; the template-only hotfix described below was applied under that later instruction.

## 1. Staging environment and deployment isolation

The disposable Compose project is `ecuread-p1-20261023-verified`, described by `/tmp/ecuread-p1-20261023-verified/compose.json`. It builds source snapshots from both working trees, including untracked implementation files. Neither checkout was reset or committed. Analyzer data is a separate writable copy of the active bundle plus the H2/H8 history inputs. PostgreSQL uses a project-specific disposable volume; Redis has no persistence. There are no production database credentials, production mutable mounts, external Docker networks, or production container names in this composition.

The bridge is internal. On this Docker engine the configured loopback publications are not exposed on an internal bridge. HTTP acceptance runs inside the isolated network through its Nginx proxy. Browser verification uses a temporary listener bound only to `127.0.0.1`, relaying to that project's proxy; it closes when verification ends. Resource limits are Analyzer 1 GiB / 1.5 CPUs, DriveSafe 768 MiB / 1 CPU, PostgreSQL 256 MiB, Redis and proxy 128 MiB each. These are staging limits, not modifications to production limits.

Production containers were recreated externally during the interrupted working session. The original audit image identities therefore are not the final baseline. This task detected that change, preserved it, and took a fresh read-only baseline after the user confirmed the other edits were finished. The final machine report compares that baseline before/after isolated acceptance. It must not be read as evidence that external work left production unchanged throughout the entire session.

## 2. Active artifacts and runtime

The active bundle remains `native-full18-20261011T135838Z`, manifest SHA-256 `e84a8350aae4708a9e429a313e90f8ee1a625209705a6df48141133f1a8bd5ae`. IF is `iforest-full18-20261011T135838Z`, battery `contextual-battery-robust-v1-ef5579cf38a0`, and speed `contextual-speed-robust-v1-fce8419c1951`. Pointer, manifest, five model/reference artifacts and copied history inputs are fingerprinted before and after execution. No artifact was retrained or rewritten.

The first container build resolved scikit-learn 1.9.1 and emitted a version warning for artifacts serialized with 1.9.0. `pyproject.toml` now pins `scikit-learn==1.9.0`. The final build and acceptance use that version. This addresses the demonstrated version mismatch; it does not claim general cross-version pickle compatibility. See the official [model persistence guidance](https://scikit-learn.org/stable/model_persistence.html#security-maintainability-limitations).

The final machine report records exact Python/package versions and image IDs. Analyzer uses the repository's Python 3.12 Dockerfile; DriveSafe uses Python 3.13, Gunicorn 23, two gthread workers and eight threads. The proxy uses the production Nginx/OpenResty image with an isolated configuration and the same 90-second connect/send/read timeouts. RAW body acquisition is bounded at 10 seconds, analysis has a cooperative 45-second stage deadline, DriveSafe's Analyzer client has a 60-second timeout, and Gunicorn retains its 120-second worker timeout. The worker timeout is not a hard deadline for an individual gthread request. Public TLS/Cloudflare/tunnel behavior is outside this isolated test.

## 3. Evidence consistency changes

`evidence_aggregation.py` retains all per-window findings and derives the configured detector set from actual results when no explicit policy is supplied. Agreement requires all configured detectors; it works for three or more detectors. `all_applicable_agreement_windows` is the general name, while `both_applicable_agreement_windows` remains a compatibility alias. Overlap counts windows with multiple positive detectors, including windows that also contain a negative detector. Full/partial/none counts are retained explicitly, including zeros. Disagreement participant IDs are collected from disagreement windows.

`diagnostic_evidence.py` now owns the scoped aggregate used by the live Harness. Detector execution, session applicability (scored in at least one window), and coverage across every window are separate concepts. The detector summary and its original positive-anywhere semantics remain in `session_detector_summary`; existing numeric counters and IDs retain that scope. The top-level state describes window presence, with precedence disagreement, multiple, single, no evidence, then insufficient coverage. Full session window coverage requires every window to have full detector coverage. A mixture of full and partial/none windows is partial, not full.

The disagreement `present` flag and window count use the same scope as the top-level state. Participating detector ID unions are not votes or a claim that every window has the same direction. Historical summaries without participant unions explicitly lack that information; identities are not invented.

The 87-sample reference retains 4 windows, 3 full / 1 partial, and 3 disagreement windows. The archived 17,976-sample reference retains 1,793 windows, 1,785 full / 8 partial, and 117 disagreement windows; its previously false `present` flag is interpreted as true from those counts. DriveSafe normalizes these historical counts for presentation using a copy. Original stored JSON, predictions, detector scores and primary diagnosis remain unchanged.

## 4. Actual Pi-session rerun workflow

The manual entry point remains `POST /api/sessions/<id>/analysis/re-run`. Pi eligibility requires a completed capture and complete sync. Incomplete captures return an ineligible response without changing their status. Complete stored RAW is read in sequence order as one logical input, without a display/batch limit. Existing canonical-only historical fallback remains explicit and is separate from the RAW idempotency guarantee.

A manual request carries `request_id`; the browser retains it in session storage until confirmed success. The backend derives the RAW idempotency key from that identity, complete input and model selection. Retries after a lost Analyzer response reuse its completed server run. Retries after a lost browser response find the already stored result by manual request identity. A subsequent deliberate action gets a new request ID and can produce a distinct run. Callers without a manual ID retain the P0 key semantics.

An atomic database status claim prevents concurrent Gunicorn workers from starting duplicate manual work for the same session. Existing states map to Queued (`pending`), Running (`submitted`), Succeeded (`completed`) and Failed. A stale in-flight state can be reclaimed after 180 seconds. Failures only update the matching submitted state and prior selected run, preventing a late failure from overwriting a completed selection. Retried Pi imports preserve the selected server run and in-flight status.

The original Pi `AnalysisResult`, analysis identity, upload fingerprints, RAW and Pi canonical rows are retained. Server output is stored as a new `AnalysisResult`, including canonical telemetry, bundle/detector metadata, deterministic RCA and reanalysis provenance. Duplicate run IDs do not rewrite prior result timestamps. Queries prefer the selected run pointer, then fall back to historical ordering.

Production session 242 was inspected read-only: completed/complete, native24_table17, 12,625 RAW records and timestamps, sequence 0–12,624, decoder honda_keihin_71_17:1.0.0. No reanalysis of that production session was issued by this task.

There is no automatic Pi trigger or historical loop. A future trigger could enqueue only newly finalized, complete sessions after Sync commits, using a stable completion/input/bundle identity and the same claim/idempotency checks. It remains disabled; single-request staging does not establish fleet capacity.

## 5. DriveSafe result presentation

Primary diagnosis, model identity, selected run, analysis origin and request state are shown together. The Harness contains execution/applicability rows, observations, symptoms, possible causes, grounded checks, technical evidence, and coverage/disagreement. Empty causes remain explicitly unsupported even when recommended checks are nonempty. Detailed detector evidence, event metadata and charts remain collapsible.

AI Analyst is a short interpretation of the persisted evidence. Its duplicate detector table and recommended-check list were removed. It does not run an additional model or statistical analysis.

Analyzer adds `anomaly_timeline`, containing all positive primary IF window intervals, and persists it with the result. DriveSafe groups those intervals for the selected run and labels covered samples accordingly; it does not treat IF window coverage as a sample-level fault label. Historical results lacking a timeline display that absence rather than borrowing Pi flags or computing a new local timeline. Charts omit unrelated Pi anomaly scores. Statistics and maintenance inputs use the selected server canonical interpretation when available; the original uploaded record table and exports remain preserved. Maintenance retains its independent vehicle-baseline guidance and is labeled accordingly.

The user's concurrently completed localization work is included in the final snapshot. English/Vietnamese behavior is verified against that shared catalogue and reload-based locale selection; it was not replaced by this task.

## 6. Acceptance and regression results

Final machine evidence: `P1_STAGING_REGRESSION_2026_10_23.json`; browser evidence: `P1_BROWSER_REGRESSION_2026_10_23.json`. Source manifests and container IDs identify exactly what was tested. Screenshots are `P1_SERVER_HARNESS_DESKTOP.png` and `P1_SERVER_HARNESS_MOBILE.png`.

The disposable full input is the first 12,625 records of the native capture `session_000018.jsonl`, treated as a complete logical replay fixture. It is not a copy or reanalysis of production session 242. Windows remain length 50, stride 10: 1,258 windows through sequence 12,619, with the last five samples retained in canonical telemetry. Window boundaries, every detector payload, aggregate, RCA, bundle provenance and completion persistence are verified.

| Scenario | Stored contract and rendered assertions |
| --- | --- |
| A: 12,625-record Pi session | Original Pi run retained; new server run; complete canonical response; RAW fingerprint unchanged; running-state guard; server-origin HTML/API/chart selection |
| B: three detectors | IF and battery positive evidence, speed negative evidence; all three execution rows; no score fusion |
| C: partial coverage | 1,253 full / 5 partial / 0 none windows; session coverage partial; state/flag/count all agree |
| D: genuine skip/unavailability | Separate actual Analyzer request skips IF; battery/speed still execute; stored execution/applicability and HTML do not label skipped IF normal or applicable |
| E: empty causes, grounded checks | No invented cause; actual full-session output renders 5 observations, 1 symptom, 0 causes and 7 checks |
| F: lost responses | Analyzer response deliberately discarded before DriveSafe storage; retries reuse one Analyzer run; repeated manual ID reuses one database result; browser retry preserves its key |
| G: historical missing evidence | Imported Pi result shows evidence missing, identifies Pi origin and does not infer detector execution |

87-sample and archived 17,976-sample fixture tests verify presentation without modifying stored JSON. The Analyzer focused suite passes 99 tests. The DriveSafe suite, including localization and P1 tests, passes 113 tests (76.156 seconds after the dashboard fix): 212 focused tests total.

The final fresh 12,625-record HTTP run passed in **36.184 seconds**, run `analysis_20261023T171514Z_ad7426eab4a5`. Container lifetime peaks were **438.72 MiB Analyzer** and **744.42 MiB DriveSafe**. These include startup, file cache and the verifier process, and are not incremental model RSS. Analyzer runtime is Python 3.12.13 / scikit-learn 1.9.0; DriveSafe is Python 3.13.15 / Gunicorn 23.0.0. All P0 detector execution/version/scored/anomaly counts match. All 318 protected Analyzer production files, four production database table fingerprints and production container identities matched through P1 browser verification, before the later explicitly requested dashboard repair.

Browser Chromium 149.0.7827.55 passed desktop/mobile, four chart canvases, timeline selection, retry behavior, locale switching, and zero page errors. A follow-up dashboard regression exposed an unguarded float formatter for IF interval events with `max_anomaly_score=None`. The dashboard now displays `--`, preserving the unavailable score. The regression checks authenticated dashboard rendering in both languages. A template-only image based on the existing production image was deployed for that separately requested repair; authenticated checks through the production reverse proxy returned HTTP 200 in EN and VI, and database/Analyzer evidence fingerprints stayed unchanged. No P1 Analyzer image was deployed.

The original full-session machine snapshot is preserved. Its `dashboard_followup` section identifies the subsequent one-template application patch and two updated test files. The final P1 web candidate is the layered **`ecuread-p1-20261023-verified-web:dashboard-fixed`**, image `sha256:80bf7c69fbc33e9a80b3449df00fe64face8a0f9b1cca9e7d62cffd047f3005b`. Its browser checks include the dashboard and pass. The full Harness/inference run predates this presentation-only guard; detector code and results did not change. The independently repaired live web image is `sha256:bd0ff56123d3fe99203c23ca7bb2c9cdab7cde67c48528394b714070ad21f7e9`.

Reproduction commands (run each project's tests in its own environment):

```bash
cd /home/hthnh/ecuread-analyzer
.venv/bin/python -m pytest tests/test_p1_evidence_consistency.py tests/test_evidence_aggregation.py tests/test_live_research_harness.py tests/test_diagnostic_integration.py tests/test_raw_request_limits.py tests/test_raw_decode_analyze_api.py tests/test_native_upload_compatibility.py tests/test_long_session_compatibility.py tests/test_features.py tests/test_contextual_speed_bundle.py -q
cd /home/hthnh/drisafe
.venv/bin/python -W ignore::ResourceWarning -m unittest tests.test_app tests.test_diagnostic_evidence_contract tests.test_contextual_speed tests.test_p1_harness_integration tests.test_localization -q
```

For another isolated verification, choose a **new** `/tmp/ecuread-p1-<unique>` directory and use Analyzer's `scripts/verify_p1_staging.py --work-dir <directory> --phase prepare`, then `verify`, `browser`, and `cleanup`. Browser verification requires the existing Playwright Node package on `NODE_PATH`. Start with empty disposable database and Analyzer storage so a cached response cannot substitute for actual inference. Never pass production Compose files or storage to these commands.

## 7. Remaining limitations

The analysis deadline is cooperative, and the 180-second claim recovery is not a persistent job queue. The RAW endpoint admits one active request per shared Analyzer data directory; concurrent attempts may receive 429 and retry. Completed RAW response caches contain full responses and need an operational retention policy. Canonical-only fallback has no equivalent RAW retry guarantee. UI statement lists and representative research events retain their existing presentation bounds; full persisted JSON and window evidence remain available.

This is one full-size request plus focused acceptance, not a throughput or fleet-load certification. External ingress timeouts were not exercised. Historical outputs are interpreted without rewriting or rescoring them. New runtime builds should use the verified frozen images; rebuilding floating dependency ranges can resolve different packages. DriveSafe peaked close to its 768 MiB staging cap; the verifier shared that container. This does not establish a safe production memory cap or concurrent-request capacity. The scikit-learn pin is tied to the current active artifacts and must be reviewed alongside any future bundle replacement.

## 8. Exact production deployment and rollback plan (not executed)

Execute only after separately authorizing production deployment. Preserve the current working trees; do not reset or build from an older Git HEAD. Use the candidate tags from final staging and verify their image IDs against the machine report. At the P1 baseline the running images were Analyzer `sha256:94e35f2701f74616fa38ff26492e573416720032a168f2218c854df4551ee14e` and DriveSafe `sha256:8c35d8cd789c6efad3068ff490a9051f170a89c71f68efbae5847ed06160145a`; the later dashboard repair changed the live web image to `sha256:bd0ff56123d3fe99203c23ca7bb2c9cdab7cde67c48528394b714070ad21f7e9`. Use that repaired image for web rollback (commands below), recheck both images before execution, and stop if they changed unexpectedly.

1. Choose a quiet maintenance interval, finish any in-flight manual requests, and make protected backups. Check the live Alembic revision is `b7f2c9a4d610`; P1 adds no migration. A different revision requires review before rollout. Snapshot live image IDs, compose files, protected environment configuration, database and Analyzer data. Do not print environment credentials.

```bash
umask 077
mkdir -p /home/hthnh/p1-rollout-backup
docker tag sha256:94e35f2701f74616fa38ff26492e573416720032a168f2218c854df4551ee14e ecuread-p1-rollback:analyzer
docker tag sha256:bd0ff56123d3fe99203c23ca7bb2c9cdab7cde67c48528394b714070ad21f7e9 ecuread-p1-rollback:web
docker exec drisafe-db-1 sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /home/hthnh/p1-rollout-backup/drisafe.dump
tar -C /home/hthnh/ecuread-analyzer -czf /home/hthnh/p1-rollout-backup/analyzer-data.tar.gz data
cp /home/hthnh/ecuread-analyzer/docker-compose.yml /home/hthnh/p1-rollout-backup/analyzer-compose.yml
cp /home/hthnh/drisafe/docker-compose.yml /home/hthnh/p1-rollout-backup/drivesafe-compose.yml
cp /home/hthnh/drisafe/.env /home/hthnh/p1-rollout-backup/drivesafe.env
```

2. Create `/home/hthnh/p1-rollout-backup/analyzer-candidate.yml` with the exact reviewed Analyzer tag, leaving its existing production volume and networks inherited:

```yaml
services:
  ecu-analyzer:
    image: ecuread-p1-20261023-verified-analyzer:candidate
```

Create `/home/hthnh/p1-rollout-backup/web-candidate.yml`:

```yaml
services:
  web:
    image: ecuread-p1-20261023-verified-web:dashboard-fixed
    environment:
      ML_API_TIMEOUT_SECONDS: '60'
```

3. Deploy Analyzer first, then DriveSafe, with no build, pull, database recreation or bulk reanalysis:

```bash
cd /home/hthnh/ecuread-analyzer
docker compose -f docker-compose.yml -f /home/hthnh/p1-rollout-backup/analyzer-candidate.yml up -d --no-build --pull never --no-deps ecu-analyzer
cd /home/hthnh/drisafe
docker compose -f docker-compose.yml -f /home/hthnh/p1-rollout-backup/web-candidate.yml up -d --no-build --pull never --no-deps web
```

Check exact running image IDs, Analyzer health, active artifact hashes, DriveSafe login and read-only historical session rendering through public ingress. Confirm schema revision is unchanged, timeout configuration matches, and no unexpected server analyses were started. Any production canary rerun is a separate explicit manual action on a chosen session; it is not part of this staging task and must preserve its original run and RAW.

4. For rollback, stop starting manual reruns and let existing requests settle. Create override files with `services.web.image: ecuread-p1-rollback:web` and `services.ecu-analyzer.image: ecuread-p1-rollback:analyzer`. Run the same `up -d --no-build --pull never --no-deps` commands with the rollback overrides, **DriveSafe first, then Analyzer**. Preserve new stored results and RAW; do not downgrade the database or restore backups as a routine image rollback. Check old image IDs, health and historical access. Restore a backup only for an independently confirmed data recovery need, with a separately reviewed plan.

## 9. Readiness verdict

**P1 integration passes isolated staging and is ready for coordinated deployment review using the pinned Analyzer candidate and dashboard-fixed web candidate.** No P1 rollout or bulk reanalysis was performed. The separately requested live dashboard repair is applied and verified in both languages. The 212 focused tests, full-session HTTP acceptance, browser checks and production evidence comparisons pass. Capacity remains established only for the measured manual workload; automatic fleet processing stays disabled. Disposable staging containers and database volumes are removed; candidate images, manifests, JSON reports and screenshots are retained for review.
