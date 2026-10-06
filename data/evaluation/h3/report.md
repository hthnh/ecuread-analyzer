# H3 Contextual Shadow Detector Report

## Feasibility

- Feasible detector scope: `True`
- Scope: narrow contextual statistical detection for verified battery-voltage deviations within RPM/TPS-defined operating contexts
- Verified signals used: RPM, TPS voltage, TPS raw, battery voltage.
- IAT and ECT were excluded because they are high-confidence/provisional, not strictly verified.
- Modeled contexts: idle_closed_throttle, low_load_running, mid_load_running
- Reference sessions: 12

## Shadow Benchmark

- Sessions evaluated: 40
- Isolation Forest statuses: `{'ok': 35, 'skipped': 5}`
- Core 2 statuses: `{'ok': 32, 'skipped': 8}`
- Core 2 scored windows: `9653`
- Core 2 skipped windows: `290`
- Core 2 events: `23`
- Window disagreements: `623`
- Label metrics computed: `False`
- Label metrics reason: no verified binary normal/fault labels are available; context labels are preserved but not scored as truth

## Decision

- Does telemetry support meaningful contextual detection? `True` for the narrow battery-voltage scope only.
- Does Core 2 provide distinct information beyond Isolation Forest? `True`.
- Remaining limitations: Only verified RPM, TPS raw, TPS voltage and battery voltage are used. IAT/ECT contextual thermal behavior remains excluded until those signals are verified. No supervised accuracy metrics are computed because verified binary normal/fault labels are unavailable. Contextual anomalies indicate statistical deviation only.
- Recommendation: `advance_for_offline_shadow_revision_not_production`
- Rationale: Core 2 is feasible as a narrow battery-voltage contextual shadow detector; keep it offline until manual review validates events.
- Core 2 remains offline/shadow only and does not alter production anomaly decisions.

## Manual Review

- ride-20260904-1810 label=known_abnormal windows=0..115 reason=contextual battery-voltage statistical deviation
- ride-20260909-0725 label=known_abnormal windows=0..115 reason=contextual battery-voltage statistical deviation
- ride-20260914-1047 label=battery_low_candidate windows=211..212 reason=contextual battery-voltage statistical deviation
- ride-20260904-1623 label=battery_low_candidate windows=0..0 reason=contextual battery-voltage statistical deviation
- ride-20260907-1157 label=battery_low_candidate windows=0..0 reason=contextual battery-voltage statistical deviation
- ride-20260908-1431 label=battery_low_candidate windows=0..0 reason=contextual battery-voltage statistical deviation
- ride-20260909-1748 label=battery_low_candidate windows=0..0 reason=contextual battery-voltage statistical deviation
- ride-20260912-1556 label=battery_low_candidate windows=0..0 reason=contextual battery-voltage statistical deviation

## Outputs

- `feasibility_audit.json`
- `contextual_reference.json`
- `shadow_benchmark_results.json`
- `shadow_window_results.jsonl`
- `decision_report.json`
