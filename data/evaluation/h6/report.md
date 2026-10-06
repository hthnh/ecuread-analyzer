# H6 Active Detector Validation Report

## Dataset

- H2 parity passed: `True`
- H2 evaluation-ready sessions: `57`
- H2 label metrics computed: `False`
- Review items: `33`
- Region types: `{'contextual_only_event': 2, 'disagreement_region': 5, 'insufficient_coverage_region': 6, 'isolation_forest_only_event': 6, 'no_evidence_region': 8, 'overlapping_event': 6}`
- Independent labels: `{'controlled_condition': 4, 'inconclusive': 19, 'observed_normal_behavior': 2, 'observed_unusual_behavior': 8}`
- Detector predictions and validation labels are stored separately.
- Unlabeled and unverified events remain inconclusive.

## Metrics

- `isolation_forest` controlled-condition recall: `1.0` (3/3 labeled cases)
- `contextual_battery_voltage` controlled-condition recall: `1.0` (1/1 labeled cases)
- Not computed: `{'event_level_precision': 'No independently verified detector-positive normal/artifact event labels are available.', 'event_level_recall': 'Controlled labels are session/condition-level in current data, not exhaustive event-level annotations.', 'false_alerts_per_hour': 'No verified normal operating-hour set is available; alert-rate can be counted but not called false alerts.', 'agreement_with_expert_labels': 'Blind expert review packet is prepared but not completed.'}`

## Decisions

- `isolation_forest`: `retain_with_limitations`
  Supported: Broad multivariate response to available controlled abnormal sessions.
- `contextual_battery_voltage`: `retain_with_limitations`
  Supported: Context-conditioned battery-voltage deviation in modeled RPM/TPS operating contexts.

## Compatibility

- Production inference remains unchanged.
- DriveSafe behavior was not modified.
- Rejected temporal candidates remain excluded.
