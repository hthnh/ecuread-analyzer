# Current Model False-Positive Audit

## Scope

- Evaluated model: `iforest-baseline-20260920T091709Z`
- Evaluated sessions: 35
- Evaluated telemetry records: 105488
- Telemetry schema: `canonical-telemetry-v2`
- Decoder: `honda_keihin_71_17:1.0.0`
- Excluded non-current-schema sessions: 5

Isolation Forest is treated as an out-of-distribution detector, not mechanical
ground truth. A high anomaly score means the window differs from the training
baseline, not that the vehicle is faulty.

## Current Status Distribution

| value | count |
| --- | --- |
| attention | 11 |
| monitor | 15 |
| ok | 9 |

## Objectively Known Labels

| value | count |
| --- | --- |
| known_abnormal | 4 |
| too_short | 2 |
| unknown | 29 |

No `normal_ride` labels were assigned from score appearance alone. Unknown
sessions are intentionally held for human review.

## Short-Session Sensitivity

| window_bucket | session_count | median_anomaly_ratio | status_distribution | health_p10 | health_median | health_p90 |
| --- | --- | --- | --- | --- | --- | --- |
| 1-4 | 1 | 1 | attention=1 | 17.63 | 17.63 | 17.63 |
| 5-9 | 1 | 1 | attention=1 | 15.45 | 15.45 | 15.45 |
| 20-49 | 1 | 0.163265 | attention=1 | 64.89 | 64.89 | 64.89 |
| 50+ | 32 | 0.038967 | attention=8, monitor=15, ok=9 | 15.967 | 81.33 | 95.996 |

The current ratio thresholds are unstable for low window counts. For example,
one anomalous window in seven windows is a 14.3% ratio, while fourteen anomalous
windows in seven hundred windows is only 2.0%. Those cases carry very different
evidence weight even when the same ratio thresholds are used.

Sessions downgraded by the audit-only minimum-evidence policy:

- `ride-20260901-0715`: 7 windows, 7 anomalous, ratio 1.000, current `attention`
- `ride-20260916-1321`: 4 windows, 4 anomalous, ratio 1.000, current `attention`

## Commonly Flagged Features

- `tps_raw_min`: 3647 window mentions
- `ect_c_delta`: 3487 window mentions
- `tps_voltage_min`: 3346 window mentions
- `ect_c_first`: 2736 window mentions
- `ect_c_slope`: 2716 window mentions
- `ect_c_min`: 2467 window mentions
- `ect_c_mean`: 1826 window mentions
- `iat_c_constant_signal`: 1545 window mentions
- `battery_voltage_max_absolute_diff`: 1424 window mentions
- `iat_c_range`: 1393 window mentions
- `iat_c_max_absolute_diff`: 1389 window mentions
- `iat_c_first`: 1347 window mentions

These features are not automatically bad. Several are plausible indicators of
training-distribution gaps: battery level, cold/warm-up ECT movement, and
constant IAT/TPS behavior can be normal operational states if they are properly
represented in the baseline.

## Finding

The false-positive behavior is primarily consistent with model calibration and
training distribution mismatch, plus low-window evidence quantization. Cloud,
HTTP transport, RAW normalization, canonical V2 semantics, and ECU byte mapping
are outside this audit and were not changed.
