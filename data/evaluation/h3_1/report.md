# H3.1 Contextual Detector Review

## Reference Audit

- Baseline terminology: nominal/reference baseline
- `normal_train` verified healthy ground truth: `False`
- Reference/evaluation sample hash overlap: `False`
- Reference sessions: 12

## Disagreements

- Total comparable disagreement windows: 623
- Primary categories: `{'isolation_forest_anomaly_contextual_normal': 621, 'isolation_forest_normal_contextual_anomaly': 2}`
- Observable category tags: `{'context_transition': 43, 'isolation_forest_anomaly_contextual_normal': 621, 'isolation_forest_normal_contextual_anomaly': 2, 'near_threshold_behavior': 271, 'possible_data_quality_artifact': 0, 'repeated_pattern_across_independent_sessions': 621, 'repeated_pattern_within_one_session': 617, 'unresolved': 0}`
- Sessions with disagreements: 23

## Manual Review Dataset

- Contextual events included: 23
- Representative disagreement regions included: 20
- Review annotations are intentionally separate from detector predictions.

## Stability

- Method: leave-one-reference-session-out
- Baseline event count: 23
- Leave-one-out event count range: 23..23
- Low-persistence baseline events: 0
- Unstable under leave-one-out: `False`

## Context Boundaries

- Contextual anomaly windows near boundaries: 4 / 257
- Boundary anomaly enrichment: `0.122429`
- Transition rule justified: `False`

## Decision

- Recommendation: `advance`
- Stable enough for research path: `True`
- Materially distinct from Isolation Forest: `True`
- Existing telemetry interpretable enough: `True`
- Reference/boundary artifacts dominate: `False`
- Most valuable physical measurements: direct battery/charging voltage trace synchronized to ECU samples; starter/engine-running state or alternator charging state; known accessory electrical load state; repeat captures for low-voltage candidate sessions; bench-confirmed TPS/RPM/battery calibration spot checks during context transitions

## Outputs

- `reference_audit.json` and `reference_windows.jsonl`
- `disagreements.jsonl` and `disagreement_summary.json`
- `manual_review_manifest.json`
- `reference_stability.json`
- `context_boundary_review.json`
- `decision_report.json`
