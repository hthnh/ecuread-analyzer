# P2 — Evidence-grounded diagnostic interpretation

Date: 24 October 2026. **Implemented and verified in disposable storage. Not deployed.** The working P1.5 production containers, models and stored evidence were preserved; no production analysis request was issued.

Machine results and source fingerprints: [P2 regression evidence](P2_DIAGNOSTIC_REGRESSION_2026_10_24.json). Screenshots: [English desktop](P2_DIAGNOSTIC_DESKTOP_EN.png), [Vietnamese mobile](P2_DIAGNOSTIC_MOBILE_VI.png). Reference boundaries: [current-state audit](ANALYZER_HARNESS_CURRENT_STATE_2026_10_23.md), [P1 integration](P1_HARNESS_DRIVESAFE_INTEGRATION.md), [completed production canary](P1_5_PRODUCTION_CANARY.md).

## Evidence boundary and original limitations

Development started from copies of the production canary run `analysis_20261023T232221Z_2c818f9e015c`, its 1,258 persisted windows/features and 12,625 canonical samples. Reading those copies and regenerating interpretation does not execute ML or rewrite the stored production run. Guest Mode was not used as a source of diagnostic facts.

| Detector | Existing evidence available | Boundary |
| --- | --- | --- |
| Isolation Forest | Per-window prediction, decision/sample scores, unusual-feature names; separately persisted RPM/TPS/voltage/temperature features; sequence and timestamp boundaries | Feature involvement does not establish a physical deviation's direction, an OEM limit or a component fault. The learned IF reference does not expose per-feature contextual reference medians. |
| Contextual Battery | Observed feature value, contextual reference median, robust scale/z-score, signed difference, threshold, operating context and source window | The reference describes observed data, not manufacturer specifications or independently confirmed healthy vehicles. A voltage deviation alone cannot identify an electrical component failure. |
| Contextual Speed | Recorded median speed, speed-change rate and dispersion, RPM/TPS values, operating context, applicability/reason, threshold and per-feature robust deviations | Numeric reference medians/scales are not in the stored speed window evidence. They are not reverse-engineered from absolute z-scores. Byte19 provenance is retained, without claiming independent GPS calibration, transmission failure, wheel slip or speed accuracy. |
| Shared | Decoder/version, source/run identity, time/sequence ranges, detector execution/applicability, positive/negative/unavailable findings and coverage | Different detector scores are not comparable votes. Missing applicability is distinct from a negative result. Evidence is bound to its actual representative window. |

The smallest missing transformations were in interpretation and presentation:

- RCA emitted generic algorithm statements despite retaining concrete measured/reference values in nested evidence.
- Every aggregate event used its first window as the representative. Different detectors can have their strongest evidence in different windows.
- Session deduplication used only the rule/observation ID, losing event-specific descriptions and links.
- Early IF regions could exhaust the representative-event allowance before a later contextual finding was considered.
- Check ordering reflected historical-comparison availability rather than practical diagnostic usefulness. Recommendations did not consistently explain which observation supported them or what evidence they would obtain.

No detector, feature extractor, model, health formula, threshold or causal requirement needed replacing.

## Interpretation and structured contract

The existing diagnostic-evidence and RCA v2 schema identifiers remain accepted. New fields are additive under `interpretation_version: evidence-grounded-interpretation-v1`; older strings and `statement` dictionaries continue to render.

Supported findings retain their original observation/symptom IDs, evidence and provenance, with these additional fields:

| Field | Meaning |
| --- | --- |
| `finding_id` | Deterministic event ID plus existing finding/rule ID; distinct events retain distinct identities |
| `presentation` | Semantic catalogue key and typed parameters; the existing VI/EN catalogue renders them |
| `presentation_role` | Primary measured interpretation or retained technical relationship/feature-list information |
| `affected_signals`, `measurements` | Signal families, actual feature names, numeric values, units and representative-window scope |
| `reference_values`, `deviation` | Only supported reference/difference/score information; reference values explicitly reject an OEM-specification interpretation |
| `operating_context` | Context belonging to the relevant detector's own witness, rather than another detector's strongest window |
| `event_reference` | Original run/event IDs, interval, contributing-window count, exact representative window and `analysis_windows.jsonl` artifact reference |
| `supporting_detector_ids`, `contradictory_detector_ids`, `window_coverage` | Separate support, limiting detector evidence and event applicability |
| `limiting_evidence`, `additional_evidence_needed`, `signal_provenance` | Explicit claim limits, the evidence needed to strengthen a claim, and retained decoder/signal provenance |

`claim_level` remains observation, symptom or conditional possible hypothesis. No confidence percentages were added. Unknown/nonfinite values are not converted into measured zeros. Reference medians are never invented for IF or speed.

Repeated generic aggregate, constant-feature and RPM/TPS messages remain in machine evidence, while concrete event-linked findings receive priority in the human-readable section. Battery and speed findings appear before IF feature-involvement summaries. Up to six primary observations are shown with an explicit shown/total count; full interpretation remains stored.

## Before/after examples from the real canary copy

### Battery

Before: **“Contextual battery-voltage deviation was observed.”**

After: **“Window minimum supply voltage was 10.7 V versus the observed contextual median 14.1 V (difference -3.4 V).”**

The finding identifies idle with closed throttle, window 0, sequences 0–49 and **0–12.250333516 seconds** from session start. Technical evidence preserves robust z **5.0962222222**, threshold **4.5**, scale **0.6671608599**, the original detector version and event link. Reference and component-cause limits remain explicit.

The symptom describes supply-voltage variation relative to the observed operating reference, with persistence and physical impact unestablished. It does not restate the numerical observation as a battery/regulator/charging-system failure. First checks seek a safe stationary independent voltage measurement and connection evidence, followed by comparison with existing recordings under similar RPM/TPS conditions.

### Speed

Before: the detector reported a positive speed finding, while bounded/generic RCA presentation did not expose its specific recorded operating conditions.

After: the copied event at **474.023205982–486.274453254 seconds**, window **183**, sequences **1,830–1,879**, exposes:

- Median recorded speed **46 km/h**.
- Median engine speed **4,942.5 rpm**.
- Median TPS voltage **0.5078125 V**, raw TPS **0**.
- Median speed-change rate **-5.4987156063 km/h/s** and dispersion **1.1014576079 km/h/s**.
- Moving / mid-load context; speed-rate robust deviation **4.9457776342** against threshold **4.5**.

This is a recorded contextual speed-change finding. It does not establish a transmission problem, wheel slip, calibrated speed accuracy or an OEM limit. The check asks for existing speed/RPM/TPS and timestamp comparisons plus independent speed corroboration. No new riding test or ECU write is prescribed.

### Isolation Forest

Before: **“Isolation Forest reported a pattern outside its learned reference distribution.”**

After: the observation names the involved temperature, throttle and voltage families, links their actual witness values and states that involvement alone does not establish the direction or cause of a physical deviation. For the first canary window, engine temperature is **33 °C initially, 34 °C finally, delta +1 °C**, with median **33 °C**. These describe that window; they are not asserted to be overheating, abnormal warm-up or a component fault. Missing IF feature-reference values remain missing.

## Event grouping and traceability

P2 extends the existing `build_aggregate_events` model. The existing complete IF anomaly timeline and every original detector window payload remain intact; there is no second detector timeline or new window generator.

Finding regions group adjacent eligible windows with the same positive/negative/applicability signature and the same positive contextual phenomenon. Frame gaps, changes in detector participation and different battery/speed contexts remain separate. Windows can overlap in time; their count is evidence support, not a count of independent vehicle malfunctions.

Each event adds:

- Original window references, interval and coverage counts.
- A separate strongest local-score witness per detector. Scores are ranked only within the same detector and retain their own direction/semantics.
- Exact source-window values and operating context, so a representative value is not presented as an event-wide physical peak.

Event duration uses supported start/end timestamps. Missing timestamps do not cause overlapping window durations to be summed. Invalid/reversed boundaries do not become an invented zero-duration event.

Representative selection reserves contextual detector/phenomenon witnesses before filling the existing per-type allowance. It remains bounded: up to five representatives per type and 15 exposed event items. Findings/checks preserve their source IDs when merged. DriveSafe's supporting-event links open the existing technical event detail and show its window/time range; machine references identify the original run and window artifact.

On the complete copied canary, grouping produces **14 finding regions**, with **11 reviewed representative finding/coverage events**. Interpretation contains **16 observations** (12 primary and four retained technical messages), **one symptom**, **zero causes** and **10 deduplicated checks**. These are reinterpretation counts on frozen detector evidence, not a newly analyzed production result or fault counts.

## Cause gates and check priorities

The existing RCA gates are preserved:

- Electrical candidates still require independent external voltage measurement or physical electrical-inspection evidence.
- Operating-state candidates still require the existing additional operator-context evidence.
- Acquisition candidates still require independent integrity evidence alongside constant-signal involvement.

No free-form operator note, byte19 signal, feature importance, anomaly score or historical recurrence is promoted into physical proof. The copied canary has no nonmonotonic timestamp pairs and no failed frame/checksum records. Its combined speed-unavailability reason does not establish a specific integrity fault. No safely supported extra physical/operator evidence was available to open a live cause gate, so zero causes is correct. A focused fixture verifies that an existing gate still allows a conditional hypothesis when the required additional evidence is explicitly supplied.

Checks retain their IDs and historical payloads while adding practical diagnostic priority (`first`, `next`, `review`), supporting finding IDs, source-event references, a localized rationale and the evidence the action would provide. Repeated check IDs merge their support instead of duplicating the recommendation. Historical-comparison priority is preserved in stored/technical evidence; it is not presented as a physical-risk score.

Specific voltage, speed and missing-coverage checks precede broad evidence-review guidance. Checks do not prescribe component replacement, ECU writes or unsafe riding tests. The speed check is a deterministic recommendation for an existing detector, not a new detector or causal rule.

## DriveSafe VI/EN presentation

The current diagnostic-summary → observations → symptoms → possible-causes → checks → technical-evidence → execution/coverage → timeline structure is retained.

Semantic message IDs and parameters use the existing shared catalogue and locale reload flow. No second translation service, LLM or additional analyst computation was introduced. Signal labels, contexts, time/value captions, limitations, priority labels, check rationales and expected evidence are localized. Raw enums and provenance JSON remain in technical details. Unknown semantic keys fall back to the existing original-evidence behavior.

Measured/reference values appear directly in contextual descriptions. IF summaries show compact signal snapshots; complete measurements and repeated interpretation limits are collapsible. Supporting links expand the corresponding original event detail. Empty causes retain the existing accurate message. Analyst tables/check lists are not duplicated.

## Verification and preservation

| Check | Result |
| --- | --- |
| Copied real session 242 | All 12,625 samples and 1,258 stored windows interpreted without inference or input mutation |
| Battery / speed / IF scope | Actual measured/reference values, speed limitations and non-causal IF interpretation verified |
| Overlap, disagreement and applicability | Grouped windows retain all references; disagreement remains explicit; skipped/unavailable cannot create positive findings |
| Negative / insufficient / causal boundaries | No verified-health inference; zero unsupported causes; existing explicit-evidence gate still works |
| Check quality | Deduplicated IDs, observation/event support, practical ordering, rationale and expected evidence verified |
| Analyzer initial focused suite | **71 passed** |
| Analyzer final targeted suite | **31 passed**, including new grouping, timestamp and own-witness-context cases |
| DriveSafe combined regression | **110 test executions passed**; final P2-only rerender suite **3 passed** |
| Browser | Chromium 149.0.7827.55; EN/VI, desktop/mobile, concrete reference text, working event links, three execution rows, zero page errors |
| Existing result compatibility | Previous `statement`/legacy text shapes, historical evidence and unknown semantic-key fallback remain covered |

One independent **disposable** full-session HTTP analysis completed through DriveSafe Werkzeug → Analyzer Uvicorn → fresh SQLite/files using copies of existing active artifacts:

- Input: first 12,625 records of existing native `session_000018.jsonl`, a complete logical replay fixture. This is not production session 242 and is not Guest data.
- Run: `analysis_20261024T002727Z_a781b64727a3`.
- Duration: **26.804 seconds**; **1,258 windows**.
- Actual detector results: IF 1,258 scored / five positive; battery 1,253 scored / one positive; speed 1,258 scored / zero positive.
- Persisted interpretation: **nine observations, one symptom, zero causes, seven checks**.
- Original disposable Pi result and RAW preserved; same manual request ID reused one stored server run. Browser checks issued no analysis POSTs. A later read-only browser refresh used this same stored result.

Every one of its **1,258 persisted detector-window payloads is identical to the earlier frozen P1 replay** of that input. Model version, window count, health, overall status, anomaly count and ratio also match exactly. Feature extraction, IF inference, battery and speed implementation files still match the frozen P1 source hashes. Existing dependency deprecation warnings were observed; no failing test was hidden.

All **324 protected production Analyzer files** match their pre-development hashes. Analyzer and DriveSafe production container IDs/images are unchanged. Disposable storage has no production database connection, mutable production mount or production analysis target. Servers were stopped after acceptance; test artifacts were retained under the documented disposable work directory. Temporary browser-session files were removed. Unrelated working-tree changes were preserved.

## Files changed

Analyzer:

- `app/ml/diagnostic_interpretation.py` — additive typed interpretation, evidence-linked checks and merge semantics.
- `app/ml/root_cause_analysis_v2.py` — enrichment after existing gates; applicable speed guard and existing-detector follow-up check.
- `app/ml/evidence_aggregation.py` — context-preserving grouping, per-detector witnesses, original-window references and bounded selection.
- `app/services/research_harness.py` — witness/provenance propagation and event-specific interpretation retention.
- `tests/test_p2_diagnostic_interpretation.py`, `tests/p2_evidence_helpers.py`, `tests/fixtures/p2_canary_windows.json` — bounded real-evidence fixtures and regressions.
- `scripts/verify_p2_interpretation.py` — interpretation-only copied-evidence verification.

DriveSafe (`/home/hthnh/drisafe`):

- `services/diagnostic_presentation.py`, `services/analysis.py` — existing-catalogue semantic rendering and practical check ordering.
- `templates/session_detail.html`, `static/js/sessionDetail.js`, `static/locales/messages.json` — concrete VI/EN content, collapsible limits and working evidence links.
- `tests/test_p2_diagnostic_interpretation.py`, `tests/fixtures/p2_canary_interpretation.json` — localized real-evidence and legacy-compatibility coverage.
- `scripts/verify_p2_disposable.py`, `scripts/verify_p2_browser.cjs` — isolated HTTP and read-only browser acceptance.

This report, regression JSON and two screenshots are added under `docs/integration/`. Exact source hashes are in the regression JSON; no unrelated file was reset, staged or committed.

## Readiness and remaining limits

**P2 passes the requested copied-evidence, focused regression and single disposable integration checks. It is ready for deployment review, with production still on the working P1.5 implementation.** No P2 image was built/deployed and no production session was reanalyzed. No model retraining, new detector, threshold/health change, LLM, migration, Pi5/Sync/K-Line change or automatic analysis trigger was introduced.

Interpretation remains limited by measured evidence. Speed reference medians and independent calibration are unavailable, cause gates still need independent proof, and representative-event/UI bounds remain explicit. Added event-scoped interpretation increases response/storage size; the canary-copy preview is approximately 3.2 MB as indented JSON, without constituting a production memory/ingress acceptance result. The local Werkzeug/Uvicorn integration is not a replacement for a future reviewed image rollout, production proxy check or concurrency test. Stop here; further production execution requires separate authorization.
