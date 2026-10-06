# Human-Labelled Baseline Coverage

## Session Split

- Random seed: 42
- Train fraction target: 0.75
- Normal training sessions: 12
- Normal holdout sessions: 4
- Session leakage: zero

Training sessions:

- `ride-20260902-1050`
- `ride-20260903-1349`
- `ride-20260905-0840`
- `ride-20260905-0858`
- `ride-20260908-1449`
- `ride-20260911-1335`
- `ride-20260913-0748`
- `ride-20260913-0807`
- `ride-20260919-1129`
- `ride-20260919-1206`
- `ride-20260919-1225`
- `ride-20260920-1504`

Holdout sessions:

- `ride-20260911-1239`
- `ride-20260911-1257`
- `ride-20260911-1316`
- `ride-20260913-0730`

## Coverage Table

| group | sample_count | rpm_min | rpm_p05 | rpm_median | rpm_p95 | rpm_max | tps_voltage_min | tps_voltage_p05 | tps_voltage_median | tps_voltage_p95 | tps_voltage_max | tps_raw_min | tps_raw_p05 | tps_raw_median | tps_raw_p95 | tps_raw_max | battery_voltage_min | battery_voltage_p05 | battery_voltage_median | battery_voltage_p95 | battery_voltage_max | iat_c_min | iat_c_p05 | iat_c_median | iat_c_p95 | iat_c_max | ect_c_min | ect_c_p05 | ect_c_median | ect_c_p95 | ect_c_max | duration_ms_min | duration_ms_p05 | duration_ms_median | duration_ms_p95 | duration_ms_max | window_count_min | window_count_p05 | window_count_median | window_count_p95 | window_count_max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| old_9_session_training | 39148 | 0 | 1402 | 3969 | 6031 | 7780 | 0.46875 | 0.488281 | 0.703125 | 1.816406 | 3.886719 | 0 | 0 | 8 | 53 | 135 | 10.5 | 14.3 | 14.5 | 14.7 | 15.1 | 36 | 38 | 46 | 49 | 49 | 36 | 85 | 90 | 92 | 93 | 28999 | 109699.4 | 1446503 | 1871298.2 | 1963997 |  |  |  |  |  |
| new_normal_training | 42064 | 0 | 1424 | 5384 | 6944.85 | 7919 | 0.46875 | 0.488281 | 0.996094 | 2.070312 | 3.886719 | 0 | 0 | 20 | 62 | 135 | 12.1 | 14.4 | 14.5 | 14.7 | 15.1 | 35 | 35 | 48 | 52 | 53 | 87 | 88 | 90 | 94 | 97 | 312499 | 316625.1 | 1066125 | 1125474.1 | 1125748 | 120 | 122.2 | 420.5 | 446 | 446 |
| normal_holdout | 18000 | 822 | 1442 | 4731.5 | 6676.05 | 7569 | 0.488281 | 0.488281 | 0.839844 | 1.699219 | 3.300781 | 0 | 0 | 13 | 47 | 111 | 12 | 14.4 | 14.5 | 14.7 | 15.1 | 33 | 35 | 45 | 48 | 48 | 79 | 89 | 89 | 93 | 94 | 1125000 | 1125037.5 | 1125375 | 1125712.5 | 1125750 | 446 | 446 | 446 | 446 | 446 |

The new candidate baseline is trained only from confirmed `normal_ride`
sessions and is evaluated against a session-separated normal holdout. The old
baseline coverage is shown as context; no old-training windows are reused for
candidate fitting.
