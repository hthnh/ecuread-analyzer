# RA1 Failure-Mode Audit

| Failure Mode | Current Representation | Not Equivalent To |
|---|---|---|
| Isolation Forest fails | DetectorResult status `failed`, production inference degrades to unavailable warning/status behavior. | A normal prediction. |
| Isolation Forest missing features | Harness marks missing features; production inference raises compatibility error for primary detector. | A negative anomaly result. |
| Contextual detector skips | `skipped`, `missing_features`, `no_applicable_context` or per-window not-applicable evidence. | Healthy battery voltage. |
| Both active detectors unavailable | Aggregate state `insufficient_coverage`. | No evidence or normal vehicle. |
| Historical comparison unavailable | History status unavailable and recurrence `historical_comparison_unavailable`. | First observed or non-recurrent pattern. |
| RCA cannot interpret event | RCA v2 emits limitations and may leave possible causes empty. | Confirmed absence of a cause. |
| Evidence payload missing | H9 says existing consumers can ignore nullable/additive field. | Analyzer failure if public anomaly fields exist. |
| Malformed diagnostic evidence | H9B DriveSafe ingestion drops malformed optional evidence and records a warning where safely possible. | Production analysis failure. |
| Legacy DriveSafe session | No diagnostic evidence section is rendered and existing session analysis still works. | Negative research evidence. |
| Analyzer failure | Existing DriveSafe analyzer failure state remains separate from any research evidence presentation. | A research detector result. |
| Contextual detector unavailable | Analyzer/aggregator marks unavailable/skipped/not-applicable states. | Healthy battery voltage. |
| Insufficient detector coverage | Aggregate state `insufficient_coverage`; DriveSafe must render it as research evidence, not normal. | Normal analysis. |
| Historical evidence unavailable | History/provenance says unavailable or preserves cutoff metadata. | Non-recurrent observation. |
| Zero possible causes | RCA v2 may return an empty possible-cause list. | Error or "no diagnosis available" warning. |
| Research evidence rendering unavailable | DriveSafe exposes raw `diagnostic_evidence` and builds display-only `diagnostic_evidence_view`; absent/malformed evidence hides the research section while primary result remains unchanged. | Production anomaly/status/health mutation. |
| Old session lacks research metadata | Research evidence sections become unavailable or incomplete. | Negative research evidence. |

The important invariant is that skipped, failed, not applicable and unavailable are separate states. None should be normalized into a healthy/normal vote.
