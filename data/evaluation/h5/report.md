# H5 Multi-Detector Evidence Aggregation Report

## Contract

- Active detectors: `['isolation_forest', 'contextual_battery_voltage']`
- Excluded temporal candidates: `['temporal_battery_shift', 'temporal_rpm_stability']`
- Detector-local scores and thresholds are preserved independently; no universal anomaly score is produced.
- `no_evidence` does not mean verified healthy.

## Coverage

- Evidence states: `{'detector_disagreement': 623, 'insufficient_coverage': 2916, 'multiple_detector_evidence': 255, 'no_evidence': 8776, 'single_detector_evidence': 289}`
- Coverage states: `{'full': 9653, 'none': 2916, 'partial': 290}`
- Applicable detector count distribution: `{'0': 2916, '1': 290, '2': 9653}`
- Detector finding counts: `{'contextual_battery_voltage': {'negative': 9396, 'not_applicable': 61, 'positive': 257, 'unavailable': 3145}, 'isolation_forest': {'negative': 8778, 'positive': 1165, 'unavailable': 2916}}`

## Findings

- IF-only finding windows: `910`
- Contextual-only finding windows: `2`
- Overlapping finding windows: `255`
- Disagreement windows: `623`
- Insufficient-coverage windows: `2916`
- Aggregate event types: `{'coverage_gap_region': 20, 'detector_specific_event': 15, 'disagreement_region': 149, 'overlapping_detector_event': 21}`

## Decision

- Recommendation: `advance`
- Future DriveSafe suitability: `True`
- Production inference remains Isolation Forest only.
- DriveSafe behavior was not modified.

## Outputs

- `aggregation_contract.json`
- `aggregate_benchmark_results.json`
- `aggregate_window_results.jsonl`
- `aggregate_event_review.json`
- `decision_report.json`
