# P3.1 — Interrupted captures are valid analysis sessions

Completed 2026-10-24T07:58:19.793977+00:00. **Implemented and verified; not deployed.** Production remains on the separately authorized P3 image `sha256:7bfe65816c9ed399833f59117fab67d0a7542fc3ea3777ff04a4c09bb4493dca`. P3 rollout results are in [P3_PRODUCTION_ROLLOUT.md](P3_PRODUCTION_ROLLOUT.md). No production analysis was executed for P3 or P3.1.

## Root cause

The deployed P3 `services/analysis_submission.py` had two completed-only gates:

- Line 168 in `manual_rerun_state`: `capture_status != "completed"` marked a synchronized interrupted capture ineligible and hid its button.
- Lines 550–552 in `submit_session_to_analyzer`: the same completed-only Pi-native gate returned HTTP 409 even if the frontend action were made visible.

These gates confused capture termination with input integrity. The operator confirms that abrupt ignition/power-off is normal rider behavior. An interrupted session is terminal; its stored records must be judged by synchronization and reconstruction/validation checks rather than by the way recording ended.

The endpoint already used the existing submission service. P3.1 does not add a second endpoint or change Pi capture/sync behavior.

## Corrected contract and changes

`services/analysis_submission.py:17–23` defines a shared terminal-state rule: capture is **completed or interrupted**, and synchronization is **complete**. `manual_rerun_state` uses that rule while retaining its structural and supported-input checks. The backend now calls the same eligibility function for manual/forced submissions **before claiming a run or contacting Analyzer**. Thus a manually posted invalid input is rejected consistently with its disabled/absent UI action, rather than relying on a frontend-only gate.

| Capture / stored input | Manual behavior |
| --- | --- |
| Completed, sync complete, supported reconstructible input | Eligible |
| Interrupted, sync complete, supported reconstructible input | Eligible |
| Interrupted, sync partial/syncing/failed | Ineligible, HTTP 409 |
| Active/capturing, failed, unknown or other nonterminal capture state | Ineligible, HTTP 409 |
| Missing records, count mismatch, malformed RAW or unavailable canonical fallback | Ineligible, HTTP 409 before Analyzer or a status claim |
| Prior successful server result | Remains eligible; history does not hide the action |
| Failed previous manual attempt | Remains eligible if input and capture/sync conditions pass |
| Fresh pending/submitted claim | Processing state is separate; button disabled and existing atomic claim prevents duplicate work |
| Same stored successful request ID | Existing result is reused without another run |

The RAW cap remains 20,000 records / 8 MiB. Canonical-only fallback keeps its existing contract. Complete input and decoder provenance remain required; Analyzer still performs its existing schema/decoder checks. No RAW schema, detector, threshold, model, Health Score, analysis timeout or inference implementation changed.

Two older regression expectations now correctly expect **409 / ineligible**, rather than generic 503, for incomplete canonical or unsupported local-screening input. No validation was weakened to make interrupted data pass. Automatic submission behavior was not enabled or expanded.

## Termination semantics and diagnostic context

- `capture_status` and `termination_reason` are never rewritten by this change. An interrupted capture remains interrupted after a successful rerun.
- `reanalysis_evidence` exposes both fields. New server-result provenance inherits these original values; old analysis JSON is not backfilled or rewritten.
- `services/analysis.py:210–220` keeps capture termination separate from processing/analysis status. Even an interrupted capture without a recognized reason remains labeled interrupted.
- The existing capture card adds a localized EN/VI explanation: ignition or power switching can end recording abruptly; saved data can still be analyzed after synchronization/input checks; terminal events remain visible; termination alone does not establish a component fault.
- The manual ineligibility message now says an **ended capture (completed or interrupted)** is required, rather than insisting on completed capture.

Analyzer's `RawDecodeAnalyzeRequest` currently carries RAW records and sampling context, but no capture-status/termination-reason field. P3.1 therefore retains termination context at the **existing DriveSafe presentation/provenance boundary**. It does not add a RAW field that Analyzer would ignore or change the deterministic RAW request identity. Canonical request/model inputs are likewise unchanged.

Terminal RAW is neither removed nor automatically repaired. IF/contextual inference remains unchanged, and the selected IF timeline retains end-of-session sequence/timestamp references. Existing event-linked observations and recommended checks remain available. No termination-based detector, causal rule, component-failure claim or blanket anomaly suppression was added; possible causes remain empty where evidence does not support them.

## Actual affected sessions: read-only inspection and copied-input validation

Production SQL/app reads used a read-only transaction. Full stored inputs for these three sessions were copied into protected storage and inspected offline. The updated shared eligibility function ran in a disposable container with **network disabled**. Existing Analyzer Pydantic schema and decoder ran locally on the copies **without loading/running models or posting an analysis**.

| Session | Complete records | Original capture / sync | Corrected eligibility | Historical result IDs retained | Last sequence / timestamp retained |
| --- | ---: | --- | --- | --- | --- |
| 246 | 3,352 | `interrupted` / `complete` | Eligible | 78 | 3351 / 837749.580846 ms |
| 248 | 12,211 | `interrupted` / `complete` | Eligible | 82 | 12210 / 3113226.776997 ms |
| 249 | 9,917 | `interrupted` / `complete` | Eligible | 79, 83 | 9916 / 2576343.155591 ms |

All **25,480** records meet the existing RAW schema and decode to the same number of canonical samples. Every sequence and timestamp matches the copied request in order; all records have valid frame/checksum flags under the existing decoder, and all three terminal samples are retained. Request sizes are 380,642 / 1,399,998 / 1,134,396 bytes respectively. All are below current record/body limits.

Session 249's prior successful server result 83 and Pi result 79 remain available. Sessions 246 and 248 retain Pi results 78 and 82. These outcomes establish that the sessions **would be eligible under P3.1**, not that a new server analysis has run. In currently deployed P3 they still show the prior completed-only restriction until a separate P3.1 rollout is authorized.

## Verification

**124 DriveSafe cases passed in 85.529 seconds**, including the full 88 dashboard cases, existing P1/P2/localization/P3 regressions and **eight new P3.1 cases**. Tests ran against the immutable P3 image with the four changed runtime files mounted read-only, disposable SQLite and network disabled. No production database or Analyzer endpoint was used by the suite. **26 existing Analyzer tests passed** for RAW limits, P2 diagnostic interpretation and evidence consistency.

Coverage includes:

1. Completed/interrupted capture eligibility, incomplete synchronization, active and failed captures, and matching page/API/direct-service outcomes.
2. Invalid RAW, missing records and unreconstructible canonical data: no claim, no Analyzer call and HTTP 409.
3. Canonical-only interrupted input still follows the existing supported fallback.
4. An interrupted upload with original Pi result, a simulated response timeout, same-ID retry and a stored-result retry: deterministic RAW request identity, one new stored server result and no duplicate storage. Original RAW, timestamps, sequence numbers, record fingerprints, capture metadata and historical Pi JSON are preserved. Reuploading the original batch still succeeds.
5. A prior server success leaves the action visible; a prior failed attempt is still retryable.
6. CSRF missing-token rejection, successful same-origin CSRF-protected submission in disposable storage, other-owner 404 and Guest 403.
7. EN/VI text, status explanations and action visibility outside disclosures, with no analysis from page rendering or localization gaps.
8. Synthetic terminal IF timeline evidence remains visible at its original sequence/time; copied P2 observations/checks remain available, stored predictions/model/Health Score payloads remain unchanged, and no cause is manufactured. This is a presentation fixture, not a new production diagnostic finding.
9. Existing P3 ORM/query, lazy charts, request-local read reuse, busy handling and retry tests continue to pass.

Existing SQLite fixture connection ResourceWarnings and the Starlette/httpx deprecation were observed; no failing regression was ignored. The first full run exposed two outdated 503 assertions for newly explicit ineligibility; those expectations were corrected and the complete final suite passed.

## Files and deployment compatibility

Runtime changes in `/home/hthnh/drisafe`:

- `services/analysis_submission.py`: shared terminal-state rule, matching backend validation and additive termination provenance.
- `services/analysis.py`: interrupted-state presentation and context.
- `templates/session_detail.html`: one localized contextual paragraph in the existing capture card.
- `static/locales/messages.json`: EN/VI explanation and corrected ineligibility text.

Tests: `tests/test_p3_1_interrupted_capture.py` (new), `tests/test_p3_rerun_recovery.py` (active capture replaces interrupted as the ineligible fixture), and two expected outcomes in `tests/test_app.py`.

Analyzer/EcuRead decoder and inference source, models, schema, Pi upload/capture behavior, frontend rerun JavaScript and P3 performance changes were not modified. No database migration is required. Older stored analyses remain compatible and immutable. Canonical-only legacy retry orchestration remains its existing P3 contract; P3.1 does not add a new durable retry service.

Production still has 59 sessions, 69 results, schema `b7f2c9a4d610`, zero pending/submitted claims and the unchanged session-242 selected run 86. Analyzer and the deployed P3 container identities are unchanged during P3.1 verification, with no errors, OOM/restarts or analysis POSTs. No P3.1 production deployment, automatic reanalysis, bulk processing or model change occurred.

Review artifacts: [source-only patch](P3_1_INTERRUPTED_CAPTURE_REVIEW.patch), [machine evidence and hashes](P3_1_INTERRUPTED_CAPTURE_ELIGIBILITY.json). Verification files are in `/tmp/ecuread-p3-1-20261024T074743Z`; copied production input remains only in the protected rollout backup directory `/home/hthnh/p3-rollout/20261024T074257Z`. The pinned P3 image/tag was not rebuilt or overwritten.

A later separately authorized P3.1 rollout can package these four reviewed runtime files over the deployed P3 image, preserving dependencies, schema, Analyzer and models. It should recheck live/source identities and use the established image-only backup/rollback procedure. **This task stops at verified source changes; no production reanalysis is needed to establish eligibility.**
