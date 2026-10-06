# Human-Labelled Retrain Report

## Labels

| label | count |
| --- | --- |
| battery_low_candidate | 9 |
| high_rpm_candidate | 1 |
| known_abnormal | 4 |
| normal_ride | 16 |
| too_short | 2 |
| unknown | 1 |
| warmup_candidate | 2 |

Only `normal_ride` is used for baseline fitting. Candidate/context labels remain
evaluation-only and are not treated as faults.

## Candidate Model

- Version: `iforest-human-normal-8122563e-e4680f77`
- Artifact directory: `data/models/honda_keihin_71_17_v2_candidate_human_normal`
- Training windows: 4155
- Feature schema: `ecu-window-features-v1`
- Telemetry schema: `canonical-telemetry-v2`
- Decoder: `honda_keihin_71_17:1.0.0`
- Hyperparameters: n_estimators=200, contamination=0.03, random_state=42

## Split

- Training sessions (12): `ride-20260902-1050`, `ride-20260903-1349`, `ride-20260905-0840`, `ride-20260905-0858`, `ride-20260908-1449`, `ride-20260911-1335`, `ride-20260913-0748`, `ride-20260913-0807`, `ride-20260919-1129`, `ride-20260919-1206`, `ride-20260919-1225`, `ride-20260920-1504`
- Holdout sessions (4): `ride-20260911-1239`, `ride-20260911-1257`, `ride-20260911-1316`, `ride-20260913-0730`
- Leakage: zero sessions overlap.

## Normal Holdout False-Positive Metric

Old model:

| count | ok_count | monitor_count | attention_count | false_monitor_count | false_attention_count |
| --- | --- | --- | --- | --- | --- |
| 4 | 2 | 2 | 0 | 2 | 0 |

Candidate model:

| count | ok_count | monitor_count | attention_count | false_monitor_count | false_attention_count |
| --- | --- | --- | --- | --- | --- |
| 4 | 2 | 2 | 0 | 2 | 0 |

## Evaluation Cohorts

| human_label | count | old_status_distribution | candidate_status_distribution | old_median_anomaly_ratio | candidate_median_anomaly_ratio | old_median_health | candidate_median_health | candidate_common_features |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| warmup_candidate | 2 | attention=1, monitor=1 | attention=1, monitor=1 | 0.096207 | 0.104054 | 77.565 | 73.915 | iat_c_constant_signal;iat_c_max_absolute_diff;iat_c_range;ect_c_delta;iat_c_delta |
| battery_low_candidate | 9 | attention=1, monitor=6, ok=2 | attention=2, monitor=5, ok=2 | 0.037209 | 0.032558 | 81.03 | 93.88 | ect_c_delta;ect_c_slope;battery_voltage_median;tps_raw_constant_signal;tps_voltage_constant_signal |
| high_rpm_candidate | 1 | attention=1 | attention=1 | 0.242152 | 0.161435 | 60.22 | 74.19 | ect_c_delta;ect_c_slope;tps_voltage_max_absolute_diff;tps_raw_max_absolute_diff;tps_raw_min |
| known_abnormal | 4 | attention=4 | attention=4 | 1 | 1 | 14.97 | 15.62 | ect_c_delta;battery_voltage_median;battery_voltage_constant_signal;iat_c_delta;iat_c_constant_signal |
| too_short | 2 | attention=2 | attention=2 | 1 | 1 | 16.54 | 16.25 | ect_c_delta;battery_voltage_median;ect_c_slope;battery_voltage_mean;iat_c_constant_signal |
| unknown | 1 | monitor=1 | ok=1 | 0.110609 | 0.006772 | 68.96 | 93.78 | iat_c_constant_signal;iat_c_max_absolute_diff;iat_c_range;iat_c_std;iat_c_delta |

## Feature Distribution Highlights

| feature | group | count | median | iqr | p05 | p95 | outlier_frequency_vs_old_training | outlier_frequency_vs_new_training |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| tps_raw_min | old_training_baseline | 3874 | 0 | 0 | 0 | 3 | 0.037687 | 0.001033 |
| tps_raw_min | new_normal_training | 4155 | 0 | 5 | 0 | 30 | 0.267629 | 0.049579 |
| tps_raw_min | normal_holdout | 1784 | 0 | 0 | 0 | 19 | 0.13565 | 0.012332 |
| tps_voltage_min | old_training_baseline | 3874 | 0.488281 | 0 | 0.488281 | 0.566406 | 0.046464 | 0.001807 |
| tps_voltage_min | new_normal_training | 4155 | 0.488281 | 0.15625 | 0.488281 | 1.269531 | 0.280866 | 0.046209 |
| tps_voltage_min | normal_holdout | 1784 | 0.488281 | 0 | 0.488281 | 0.976562 | 0.143498 | 0.01065 |
| ect_c_delta | old_training_baseline | 3874 | 0 | 0 | -1 | 2 | 0.05937 | 0.104543 |
| ect_c_delta | new_normal_training | 4155 | 0 | 0 | -1 | 1 | 0.031528 | 0.056799 |
| ect_c_delta | normal_holdout | 1784 | 0 | 0 | -1 | 1.85 | 0.055493 | 0.096413 |
| ect_c_first | old_training_baseline | 3874 | 90 | 1 | 85 | 92 | 0.09396 | 0.064275 |
| ect_c_first | new_normal_training | 4155 | 90 | 4 | 88 | 94 | 0.285439 | 0.018532 |
| ect_c_first | normal_holdout | 1784 | 89 | 1 | 89 | 93 | 0.058296 | 0.011211 |
| ect_c_slope | old_training_baseline | 3874 | 0 | 0 | -0.020408 | 0.040816 | 0.05937 | 0.104543 |
| ect_c_slope | new_normal_training | 4155 | 0 | 0 | -0.020408 | 0.020408 | 0.031528 | 0.056799 |
| ect_c_slope | normal_holdout | 1784 | 0 | 0 | -0.020408 | 0.037755 | 0.055493 | 0.096413 |
| iat_c_constant_signal | old_training_baseline | 3874 | 1 | 0 | 0 | 1 | 0 | 0 |
| iat_c_constant_signal | new_normal_training | 4155 | 1 | 0 | 0 | 1 | 0 | 0 |
| iat_c_constant_signal | normal_holdout | 1784 | 1 | 0 | 0 | 1 | 0 | 0 |
| battery_voltage_mean | old_training_baseline | 3874 | 14.52 | 0.022 | 14.48 | 14.552 | 0.094476 | 0.16572 |
| battery_voltage_mean | new_normal_training | 4155 | 14.518 | 0.018 | 14.494 | 14.5446 | 0.051264 | 0.098917 |
| battery_voltage_mean | normal_holdout | 1784 | 14.518 | 0.02 | 14.494 | 14.554 | 0.06222 | 0.132287 |
| battery_voltage_median | old_training_baseline | 3874 | 14.5 | 0 | 14.5 | 14.55 | 0.077956 | 0.109706 |
| battery_voltage_median | new_normal_training | 4155 | 14.5 | 0 | 14.5 | 14.5 | 0.042359 | 0.061853 |
| battery_voltage_median | normal_holdout | 1784 | 14.5 | 0 | 14.5 | 14.6 | 0.072309 | 0.098655 |
