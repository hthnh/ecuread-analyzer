# H4.1 Temporal Candidate Revision Report

## Target Audit

- Feasible: `True`
- Target: sustained RPM variability increase within closed-throttle running segments
- Adequate closed-throttle runs: `50`
- Candidate B uses verified RPM/TPS only; battery voltage is excluded from its target.

## Comparative Benchmark

- Candidate A statuses: `{'ok': 30, 'skipped': 10}`
- Candidate B statuses: `{'ok': 23, 'skipped': 17}`
- Candidate B coverage: `{'applicable_window_count': 1230, 'warmup_window_count': 1741, 'skipped_window_count': 6972, 'anomaly_window_count': 6, 'event_count': 1, 'total_window_count': 9943, 'applicable_ratio': 0.123705, 'warmup_ratio': 0.175098, 'skipped_ratio': 0.701197}`
- Candidate B event overlap: `{'rpm_event_count': 1, 'battery_temporal_overlap_event_count': 0, 'core1_overlap_event_count': 0, 'core2_overlap_event_count': 0, 'core3_only_event_count': 1}`
- Pairwise disagreements: `{'contextual_battery_voltage__temporal_battery_shift': {'compared_windows': 6082, 'unavailable_windows': 3621, 'window_disagreement_count': 545, 'event_count_delta': 20, 'window_disagreement_ratio': 0.089609}, 'contextual_battery_voltage__temporal_rpm_stability': {'compared_windows': 1230, 'unavailable_windows': 7102, 'window_disagreement_count': 8, 'event_count_delta': 16, 'window_disagreement_ratio': 0.006504}, 'isolation_forest__contextual_battery_voltage': {'compared_windows': 9653, 'unavailable_windows': 61, 'window_disagreement_count': 623, 'event_count_delta': 137, 'window_disagreement_ratio': 0.06454}, 'isolation_forest__temporal_battery_shift': {'compared_windows': 6082, 'unavailable_windows': 3621, 'window_disagreement_count': 694, 'event_count_delta': 157, 'window_disagreement_ratio': 0.114107}, 'isolation_forest__temporal_rpm_stability': {'compared_windows': 1230, 'unavailable_windows': 7102, 'window_disagreement_count': 314, 'event_count_delta': 140, 'window_disagreement_ratio': 0.255285}, 'temporal_battery_shift__temporal_rpm_stability': {'compared_windows': 913, 'unavailable_windows': 7419, 'window_disagreement_count': 329, 'event_count_delta': 0, 'window_disagreement_ratio': 0.36035}}`
- Four-way states: `{'iforest=0|contextual=0|battery_temporal=0|rpm_temporal=0': 443, 'iforest=0|contextual=0|battery_temporal=1|rpm_temporal=0': 169, 'iforest=1|contextual=0|battery_temporal=0|rpm_temporal=0': 141, 'iforest=1|contextual=0|battery_temporal=1|rpm_temporal=0': 160}`

## Review

- Candidate B review events: `1`
- Review categories: `{'Core3-only RPM temporal event': 'rpm-temporal-event-001', 'sustained RPM variability increase': 'rpm-temporal-event-001'}`

## Stability

- Candidate B variant event count range: 1..2
- Low-persistence Candidate B events: `0`
- Parameter sensitive: `True`

## Selection

- Selection: `both_reject`
- Rationale: Neither temporal candidate currently demonstrates sufficient distinct event-level value.
- Production inference remains Isolation Forest only.

## Outputs

- `target_audit.json`
- `candidate_benchmark_results.json` and `shadow_window_results.jsonl`
- `rpm_review_manifest.json`
- `rpm_parameter_sensitivity.json`
- `candidate_selection.json`
