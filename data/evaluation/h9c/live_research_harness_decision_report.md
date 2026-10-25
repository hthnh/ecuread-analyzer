# H9C Live Research Harness Decision Report

## Decision

`advance`

## Answers

1. Was Contextual Battery previously running in live analysis or only offline?

   It was offline/research only. The pre-change live path registered only `IsolationForestDetector` in `run_inference()`.

2. What changed to make the research harness live?

   `AnalysisService` now runs an additive research-shadow harness after production inference. It reuses the production Isolation Forest result, executes `contextual_battery_voltage` from the existing H3 reference, runs H5 aggregation, RCA v2, and H8 historical lookup, then stores the result in live `diagnostic_evidence`.

3. Are live IF research results identical to production IF results?

   Yes. The live research payload reuses the production IF detector object. Smoke parity checks showed identical IF status, scored-window count, and anomaly-window count for these live runs:

   - `analysis_20261006T152201Z_4a6ffda1dd13`
   - `analysis_20261006T152205Z_0440b6379d5b`
   - `analysis_20261006T152212Z_ee9b4f310572`
   - `analysis_20261006T152214Z_bc6406160d24`
   - `analysis_20261006T152237Z_523244bc5457`
   - `analysis_20261006T152239Z_b084de452fea`
   - `analysis_20261006T152308Z_4654328dc05c`

4. Can DriveSafe now show per-detector output?

   Yes. The DriveSafe inspector shows Isolation Forest and Contextual Battery rows with status, applicability, finding, local score, threshold, and expandable detector details.

5. Can DriveSafe show skipped/failed/applicability states?

   Yes. Tests cover contextual skipped and failed states; skipped/failed/not applicable are displayed separately from normal findings.

6. Can DriveSafe show agreement/disagreement?

   Yes. Aggregate evidence state, agreement windows, disagreement windows, and bounded event cards are visible.

7. Can DriveSafe show RCA v2 observations and checks?

   Yes. Observations, symptoms, possible causes, recommended checks, and the empty possible-cause message render from analyzer-provided RCA v2 sections.

8. Can DriveSafe show historical recurrence/trend?

   Yes. Live analyzer payloads include historical evidence when the H8 index is available, and DriveSafe displays compact recurrence/trend details and limitations.

9. Does any research evidence alter production anomaly/status/health?

   No. Production `overall_status`, `health_score`, `anomaly_ratio`, anomaly-window counts, severity, alerts, and notifications remain based on the existing production path.

10. What is the runtime overhead?

   Smoke runs recorded contextual detector time from about 5.9 ms to 44.9 ms, aggregation from about 33.4 ms to 536.4 ms, RCA below 0.5 ms, history lookup from about 40.4 ms to 65.5 ms, and total research-shadow overhead from about 105 ms to 623 ms on the sampled sessions. Larger sessions spend most added time in aggregation.

11. Are any states still invisible on the web app?

   No required H9C state is invisible. DriveSafe intentionally renders a bounded event list instead of every per-window row; full per-window evidence remains in analyzer artifacts.

## Real Session Smoke Results

| Session | Analysis run | Production status | IF | Contextual | Aggregate | History | Persisted |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `calib-20260812-152832-2a54f0` | `analysis_20261006T152201Z_4a6ffda1dd13` | `attention` | positive | skipped/unavailable | single_detector_evidence, partial | available | yes |
| `calib-20260814-153329-812a3f` | `analysis_20261006T152205Z_0440b6379d5b` | `attention` | positive | positive | detector_disagreement, full | available | yes |
| `calib-real-run-r0024d0-c93a5fb4-part-01` | `analysis_20261006T152212Z_ee9b4f310572` | `monitor` | positive | positive | detector_disagreement, full | available | yes |
| `ride-20260907-1157` | `analysis_20261006T152214Z_bc6406160d24` | `monitor` | positive | positive | detector_disagreement, full | available | yes |
| `ride-20260916-1321` | `analysis_20261006T152237Z_523244bc5457` | `limited_data` | positive | positive | multiple_detector_evidence, full | available | yes |
| `ride-20260902-1050` | `analysis_20261006T152239Z_b084de452fea` | `ok` | positive | negative | single_detector_evidence, full | available | yes |
| `calib-real-run-r0024d0-c93a5fb4-part-02` | `analysis_20261006T152308Z_4654328dc05c` | `ok` | negative | negative | no_evidence, full | available | yes |

The v1 fixture `esp-fake-low-battery-20260920144735` was not counted as a successful H9C live smoke because the selected v2 model correctly rejected the telemetry-schema mismatch.

## DriveSafe Smoke

Actual live analyzer payloads were sent through DriveSafe ingress in an isolated test app:

- `analysis_20261006T152308Z_4654328dc05c` -> `h9c-live-analyzer-payload-smoke`: no-evidence payload persisted, API view built, detector table rendered, history visible.
- `analysis_20261006T152205Z_0440b6379d5b` -> `h9c-live-analyzer-payload-disagreement-smoke`: disagreement payload persisted, bounded event cards rendered, detector table rendered, history visible.

## Verification

- ECURead Analyzer full pytest: passed.
- ECURead Analyzer Pi handoff verifier: passed (`model_version=iforest-baseline-20260920T091709Z`, `feature_count=81`).
- DriveSafe full unittest suite: passed.
- Production-vs-research IF parity smoke: passed for all listed live runs.

## Constraints Confirmed

- No new detector was introduced.
- No MAP logic was added.
- `temporal_battery_shift` and `temporal_rpm_stability` remain archived/excluded.
- No threshold tuning, score fusion, voting, confidence percentage, LLM, agent, vector database, or new RCA rule was added.
- DriveSafe remains a visualization consumer and does not compute analyzer logic.
