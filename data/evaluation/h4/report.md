# H4 Temporal Change Detector Report

## Feasibility

- Feasible: `True`
- Target: sustained intra-session battery-voltage shift within stable RPM/TPS operating contexts
- Median window step ms: `2500.0`
- Adequate stable context runs: `208`

## Benchmark

- Temporal statuses: `{'ok': 30, 'skipped': 10}`
- Temporal coverage: `{'applicable_window_count': 6082, 'warmup_window_count': 3571, 'skipped_window_count': 290, 'anomaly_window_count': 329, 'total_window_count': 9943, 'applicable_ratio': 0.611687, 'warmup_ratio': 0.359147, 'skipped_ratio': 0.029166}`
- Pairwise disagreements: `{'contextual_battery_voltage__temporal_battery_shift': {'compared_windows': 6082, 'unavailable_windows': 3621, 'window_disagreement_count': 545, 'event_count_delta': 20, 'window_disagreement_ratio': 0.089609}, 'isolation_forest__contextual_battery_voltage': {'compared_windows': 9653, 'unavailable_windows': 61, 'window_disagreement_count': 623, 'event_count_delta': 137, 'window_disagreement_ratio': 0.06454}, 'isolation_forest__temporal_battery_shift': {'compared_windows': 6082, 'unavailable_windows': 3621, 'window_disagreement_count': 694, 'event_count_delta': 157, 'window_disagreement_ratio': 0.114107}}`
- Temporal events: `1`
- Event overlap: `{'temporal_event_count': 1, 'core1_core3_event_count': 1, 'core2_core3_event_count': 0, 'core3_only_event_count': 0, 'three_detector_agreement_event_count': 0}`
- Three-way states: `{'iforest=0|contextual=0|temporal=0': 5228, 'iforest=0|contextual=0|temporal=1': 169, 'iforest=1|contextual=0|temporal=0': 309, 'iforest=1|contextual=0|temporal=1': 160, 'iforest=1|contextual=1|temporal=0': 216}`
- Label metrics computed: `False`

## Review

- Review events: `1`
- Representative categories: `{'core1_core3_event': 'temporal-event-001'}`

## Stability

- Variant event count range: 1..1
- Low-persistence events: `0`
- Parameter sensitive: `False`

## Decision

- Recommendation: `revise`
- Detects temporal phenomenon: `True`
- Distinct from Core 1/Core 2: `False`
- Stable under parameter variation: `True`
- Artifact dominated: `False`
- Keep in research harness: `True`

## Outputs

- `feasibility_audit.json`
- `shadow_benchmark_results.json` and `shadow_window_results.jsonl`
- `review_manifest.json`
- `parameter_sensitivity.json`
- `decision_report.json`
