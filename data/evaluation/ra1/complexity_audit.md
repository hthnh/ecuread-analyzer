# RA1 Complexity Audit

| Component | Classification | Distinct Contribution | Duplication / Removal Note |
|---|---|---|---|
| Canonical telemetry | Necessary | Shared source of deterministic signal data. | Cannot remove without replacing the pipeline. |
| Window features | Necessary | Stable feature contract for detectors. | Required by production and research. |
| Model Harness | Necessary | Detector lifecycle and failure isolation. | Small enough to keep. |
| Isolation Forest | Necessary | Only production anomaly detector. | Required for current behavior. |
| Contextual Battery | Useful but optional research | Context-specific voltage evidence IF can miss or disagree with. | Remove only if future validation contradicts usefulness. |
| Temporal Battery | Rejected | Stable but no unique event value. | Archive for reproducibility; do not keep active. |
| Temporal RPM | Rejected | Possible distinct event but not stable/covered enough. | Archive for reproducibility; do not keep active. |
| Evidence Aggregator | Useful research infrastructure | Coverage/disagreement semantics without fusion. | Valuable while more than one detector is researched. |
| RCA v1 | Candidate for removal from active docs | Reproducibility of old hypothesis behavior. | Superseded by v2; keep archived artifacts only. |
| RCA v2 | Research-only | Separates observations, symptoms, checks and causes. | Rename conceptually to Diagnostic Evidence Interpretation Engine. |
| Historical Evidence | Research-only | Recurrence/trend/check-priority context. | Keep offline until validated with user-facing workflow. |
| Diagnostic Evidence Contract | Useful integration | Additive stable payload boundary. | Integrated end-to-end through H9B. |
| DriveSafe Diagnostic Evidence Consumer | Necessary integration surface | Persistence/API/UI presentation of analyzer-owned evidence. | Does not duplicate detector inference, aggregation, RCA rules or historical recurrence/trend computation. |

Current unnecessary complexity in the active architecture is mainly the temptation to keep temporal candidates active. The audit recommendation is to preserve their artifacts but exclude them from the active research detector set.

RA1.1 duplication check: Analyzer owns analysis logic. DriveSafe owns ingestion, persistence, API presentation and UI. No H9B duplication of detector inference, aggregation semantics, RCA rules or historical recurrence/trend computation was found.
