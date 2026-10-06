# Training Distribution Audit

## Model

- Model version: `iforest-baseline-20260920T091709Z`
- Training source: `real_data/real_run`
- Training session count: 9
- Metadata training windows: 3874
- Recomputed training windows: 3874
- Feature schema: `ecu-window-features-v1`
- Telemetry schema: `canonical-telemetry-v2`
- Decoder versions: `honda_keihin_71_17:1.0.0`

## Training Sessions

| source_file | sample_count | window_count | duration_ms_min | duration_ms_median | duration_ms_max | rpm_min | rpm_median | rpm_max | tps_voltage_min | tps_voltage_median | tps_voltage_max | tps_raw_min | tps_raw_median | tps_raw_max | battery_voltage_min | battery_voltage_median | battery_voltage_max | iat_c_min | iat_c_median | iat_c_max | ect_c_min | ect_c_median | ect_c_max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r0024d0_c93a5fb4.dat.jsonl | 6922 | 688 | 1732250 | 1732250 | 1732250 | 0 | 4137.5 | 7228 | 0.46875 | 0.742188 | 3.476562 | 0 | 10 | 119 | 10.5 | 14.5 | 15 | 36 | 41 | 47 | 36 | 89 | 93 |
| r1032c0_849aa7f8.dat.jsonl | 6899 | 685 | 1725998 | 1725998 | 1725998 | 20 | 3670 | 7251 | 0.488281 | 0.585938 | 3.515625 | 0 | 3 | 120 | 11 | 14.5 | 15 | 47 | 48 | 49 | 89 | 90 | 93 |
| session_000001.jsonl | 117 | 7 | 28999 | 28999 | 28999 | 789 | 1261 | 3562 | 0.488281 | 0.488281 | 1.074219 | 0 | 0 | 23 | 11 | 14.1 | 14.8 | 39 | 40 | 40 | 45 | 46 | 48 |
| session_000002.jsonl | 5781 | 574 | 1446503 | 1446503 | 1446503 | 0 | 3958 | 7780 | 0.488281 | 0.722656 | 3.203125 | 0 | 9 | 108 | 12.6 | 14.5 | 15.1 | 39 | 42 | 47 | 48 | 89 | 92 |
| session_000003.jsonl | 1249 | 120 | 312499 | 312499 | 312499 | 765 | 4499 | 6993 | 0.46875 | 0.527344 | 3.886719 | 0 | 1 | 135 | 12.1 | 14.5 | 14.9 | 46 | 46 | 47 | 89 | 89 | 90 |
| session_000004.jsonl | 3017 | 297 | 755001 | 755001 | 755001 | 20 | 4073 | 7290 | 0.488281 | 0.625 | 3.359375 | 0 | 5 | 114 | 11.5 | 14.5 | 15 | 46 | 47 | 47 | 89 | 90 | 93 |
| session_000005.jsonl | 7851 | 781 | 1963997 | 1963997 | 1963997 | 0 | 4069 | 7159 | 0.488281 | 0.742188 | 3.476562 | 0 | 9 | 119 | 12.4 | 14.5 | 15.1 | 46 | 47 | 48 | 88 | 90 | 93 |
| session_000006.jsonl | 923 | 88 | 230750 | 230750 | 230750 | 0 | 2986 | 5484 | 0.488281 | 0.566406 | 2.285156 | 0 | 3 | 71 | 10.8 | 14.5 | 15.1 | 46 | 47 | 47 | 70 | 87 | 89 |
| session_000007.jsonl | 6389 | 634 | 1598002 | 1598002 | 1598002 | 20 | 4028 | 7466 | 0.488281 | 0.78125 | 3.300781 | 0 | 11 | 112 | 10.9 | 14.5 | 15.1 | 44 | 44 | 47 | 89 | 90 | 93 |

## Distribution Comparison

Training distribution:

| sample_count | duration_ms_min | duration_ms_median | duration_ms_max | rpm_min | rpm_median | rpm_max | tps_voltage_min | tps_voltage_median | tps_voltage_max | tps_raw_min | tps_raw_median | tps_raw_max | battery_voltage_min | battery_voltage_median | battery_voltage_max | iat_c_min | iat_c_median | iat_c_max | ect_c_min | ect_c_median | ect_c_max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 39148 | 28999 | 1446503 | 1963997 | 0 | 3969 | 7780 | 0.46875 | 0.703125 | 3.886719 | 0 | 8 | 135 | 10.5 | 14.5 | 15.1 | 36 | 46 | 49 | 36 | 90 | 93 |

Current evaluation distribution:

| session_count | sample_count | duration_ms_min | duration_ms_median | duration_ms_max | rpm_min | rpm_median | rpm_max | tps_voltage_min | tps_voltage_median | tps_voltage_max | tps_raw_min | tps_raw_median | tps_raw_max | battery_voltage_min | battery_voltage_median | battery_voltage_max | iat_c_min | iat_c_median | iat_c_max | ect_c_min | ect_c_median | ect_c_max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 35 | 105488 | 21501 | 873500 | 1126253 | 0 | 4452 | 10330 | 0.46875 | 0.820312 | 4.394531 | 0 | 12 | 156 | 10.6 | 14.5 | 15.3 | 31 | 47 | 53 | 32 | 90 | 116 |

## Supported Gaps

- high RPM/load range extends beyond training: training RPM max 7780, eval max 10330
- high TPS raw range extends beyond training: training TPS raw max 135, eval max 156
- high ECT range extends beyond training: training ECT max 93, eval max 116
- low-battery sessions are more common in evaluation: training frame fraction below 12V 0.0002, eval session fraction with min below 12V 0.3429

The current baseline is built from road-run files only. It should not be treated
as proof that cold start, idle-heavy, low-battery key-on/cranking, or very short
sessions are mechanically abnormal unless those states are represented and
labelled in the training/evaluation set.
