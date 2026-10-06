# Anomaly Calibration Report

## Limited Data Evidence Gate

The production Analyzer now applies this session-level interpretation gate after
window scoring:

```text
if window_count < 10:
    overall_status = limited_data
```

`limited_data` does not mean healthy and does not mean anomalous. It means there
is insufficient session-level evidence to interpret the existing window scores
strongly. Raw metrics such as `anomaly_ratio`, `anomaly_window_count`,
`health_score`, per-window predictions, and decision scores remain unchanged.

This is a product interpretation policy, not a model, decoder, schema, or
threshold change.

## Round 2 Human-Labelled Baseline

- Recommendation: `KEEP_CURRENT_MODEL`
- Label counts:

| label | count |
| --- | --- |
| battery_low_candidate | 9 |
| high_rpm_candidate | 1 |
| known_abnormal | 4 |
| normal_ride | 16 |
| too_short | 2 |
| unknown | 1 |
| warmup_candidate | 2 |

## Old vs Candidate Normal-Holdout False Positives

| model | count | ok_count | monitor_count | attention_count | false_monitor_count | false_attention_count |
| --- | --- | --- | --- | --- | --- | --- |
| old | 4 | 2 | 2 | 0 | 2 | 0 |
| candidate | 4 | 2 | 2 | 0 | 2 | 0 |

## Cohorts

| human_label | count | old_status_distribution | candidate_status_distribution | old_median_anomaly_ratio | candidate_median_anomaly_ratio | old_median_health | candidate_median_health | candidate_common_features |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| warmup_candidate | 2 | attention=1, monitor=1 | attention=1, monitor=1 | 0.096207 | 0.104054 | 77.565 | 73.915 | iat_c_constant_signal;iat_c_max_absolute_diff;iat_c_range;ect_c_delta;iat_c_delta |
| battery_low_candidate | 9 | attention=1, monitor=6, ok=2 | attention=2, monitor=5, ok=2 | 0.037209 | 0.032558 | 81.03 | 93.88 | ect_c_delta;ect_c_slope;battery_voltage_median;tps_raw_constant_signal;tps_voltage_constant_signal |
| high_rpm_candidate | 1 | attention=1 | attention=1 | 0.242152 | 0.161435 | 60.22 | 74.19 | ect_c_delta;ect_c_slope;tps_voltage_max_absolute_diff;tps_raw_max_absolute_diff;tps_raw_min |
| known_abnormal | 4 | attention=4 | attention=4 | 1 | 1 | 14.97 | 15.62 | ect_c_delta;battery_voltage_median;battery_voltage_constant_signal;iat_c_delta;iat_c_constant_signal |
| too_short | 2 | attention=2 | attention=2 | 1 | 1 | 16.54 | 16.25 | ect_c_delta;battery_voltage_median;ect_c_slope;battery_voltage_mean;iat_c_constant_signal |
| unknown | 1 | monitor=1 | ok=1 | 0.110609 | 0.006772 | 68.96 | 93.78 | iat_c_constant_signal;iat_c_max_absolute_diff;iat_c_range;iat_c_std;iat_c_delta |

## Threshold Search

| model | monitor_ratio | attention_ratio | attention_health_lt | monitor_health_lt | is_current_policy | normal_holdout_count | normal_holdout_ok | normal_holdout_false_monitor | normal_holdout_false_attention | known_abnormal_attention | known_abnormal_monitor | known_abnormal_ok | warmup_distribution | battery_low_distribution | high_rpm_distribution |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| old | 0.02 | 0.1 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=2, monitor=5, ok=2 | attention=1 |
| old | 0.02 | 0.15 | 50 | 80 | True | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=1, monitor=6, ok=2 | attention=1 |
| old | 0.02 | 0.2 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=6, ok=2 | attention=1 |
| old | 0.02 | 0.25 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=6, ok=2 | monitor=1 |
| old | 0.03 | 0.1 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=4, ok=3 | attention=1 |
| old | 0.03 | 0.15 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=1, monitor=5, ok=3 | attention=1 |
| old | 0.03 | 0.2 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=5, ok=3 | attention=1 |
| old | 0.03 | 0.25 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=5, ok=3 | monitor=1 |
| old | 0.05 | 0.1 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=3, ok=4 | attention=1 |
| old | 0.05 | 0.15 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=1, monitor=4, ok=4 | attention=1 |
| old | 0.05 | 0.2 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=4, ok=4 | attention=1 |
| old | 0.05 | 0.25 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=4, ok=4 | monitor=1 |
| old | 0.08 | 0.1 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=3, ok=4 | attention=1 |
| old | 0.08 | 0.15 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=1, monitor=4, ok=4 | attention=1 |
| old | 0.08 | 0.2 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=4, ok=4 | attention=1 |
| old | 0.08 | 0.25 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=4, ok=4 | monitor=1 |
| old | 0.02 | 0.1 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=2, monitor=4, ok=3 | attention=1 |
| old | 0.02 | 0.15 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=1, monitor=5, ok=3 | attention=1 |
| old | 0.02 | 0.2 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=5, ok=3 | attention=1 |
| old | 0.02 | 0.25 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=5, ok=3 | monitor=1 |
| old | 0.03 | 0.1 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=3, ok=4 | attention=1 |
| old | 0.03 | 0.15 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=1, monitor=4, ok=4 | attention=1 |
| old | 0.03 | 0.2 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=4, ok=4 | attention=1 |
| old | 0.03 | 0.25 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=4, ok=4 | monitor=1 |
| old | 0.05 | 0.1 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=2, ok=5 | attention=1 |
| old | 0.05 | 0.15 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=1, monitor=3, ok=5 | attention=1 |
| old | 0.05 | 0.2 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=3, ok=5 | attention=1 |
| old | 0.05 | 0.25 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=3, ok=5 | monitor=1 |
| old | 0.08 | 0.1 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=1, ok=6 | attention=1 |
| old | 0.08 | 0.15 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=1, monitor=2, ok=6 | attention=1 |
| old | 0.08 | 0.2 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | attention=1 |
| old | 0.08 | 0.25 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | monitor=1 |
| candidate | 0.02 | 0.1 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=3, monitor=4, ok=2 | attention=1 |
| candidate | 0.02 | 0.15 | 50 | 80 | True | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=2, monitor=5, ok=2 | attention=1 |
| candidate | 0.02 | 0.2 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=6, ok=2 | monitor=1 |
| candidate | 0.02 | 0.25 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=6, ok=2 | monitor=1 |
| candidate | 0.03 | 0.1 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=3, monitor=2, ok=4 | attention=1 |
| candidate | 0.03 | 0.15 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=2, monitor=3, ok=4 | attention=1 |
| candidate | 0.03 | 0.2 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=4, ok=4 | monitor=1 |
| candidate | 0.03 | 0.25 | 50 | 80 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=4, ok=4 | monitor=1 |
| candidate | 0.05 | 0.1 | 50 | 80 | False | 4 | 3 | 1 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=3, ok=6 | attention=1 |
| candidate | 0.05 | 0.15 | 50 | 80 | False | 4 | 3 | 1 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=1, ok=6 | attention=1 |
| candidate | 0.05 | 0.2 | 50 | 80 | False | 4 | 3 | 1 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | monitor=1 |
| candidate | 0.05 | 0.25 | 50 | 80 | False | 4 | 3 | 1 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | monitor=1 |
| candidate | 0.08 | 0.1 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=3, ok=6 | attention=1 |
| candidate | 0.08 | 0.15 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=1, ok=6 | attention=1 |
| candidate | 0.08 | 0.2 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | monitor=1 |
| candidate | 0.08 | 0.25 | 50 | 80 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | monitor=1 |
| candidate | 0.02 | 0.1 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=3, monitor=4, ok=2 | attention=1 |
| candidate | 0.02 | 0.15 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=2, monitor=5, ok=2 | attention=1 |
| candidate | 0.02 | 0.2 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=6, ok=2 | monitor=1 |
| candidate | 0.02 | 0.25 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=6, ok=2 | monitor=1 |
| candidate | 0.03 | 0.1 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=3, monitor=2, ok=4 | attention=1 |
| candidate | 0.03 | 0.15 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=2, monitor=3, ok=4 | attention=1 |
| candidate | 0.03 | 0.2 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=4, ok=4 | monitor=1 |
| candidate | 0.03 | 0.25 | 40 | 70 | False | 4 | 2 | 2 | 0 | 4 | 0 | 0 | monitor=2 | attention=1, monitor=4, ok=4 | monitor=1 |
| candidate | 0.05 | 0.1 | 40 | 70 | False | 4 | 3 | 1 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=3, ok=6 | attention=1 |
| candidate | 0.05 | 0.15 | 40 | 70 | False | 4 | 3 | 1 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=1, ok=6 | attention=1 |
| candidate | 0.05 | 0.2 | 40 | 70 | False | 4 | 3 | 1 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | monitor=1 |
| candidate | 0.05 | 0.25 | 40 | 70 | False | 4 | 3 | 1 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | monitor=1 |
| candidate | 0.08 | 0.1 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=3, ok=6 | attention=1 |
| candidate | 0.08 | 0.15 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | attention=1, ok=1 | attention=2, monitor=1, ok=6 | attention=1 |
| candidate | 0.08 | 0.2 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | monitor=1 |
| candidate | 0.08 | 0.25 | 40 | 70 | False | 4 | 4 | 0 | 0 | 4 | 0 | 0 | monitor=1, ok=1 | attention=1, monitor=2, ok=6 | monitor=1 |

Threshold changes are not justified in this round. The labelled holdout is still
small, and lower false-positive counts from looser thresholds would be a product
policy choice rather than evidence that the underlying anomaly baseline is
better.

Threshold changes are not baked into the model. Product status policy remains a
separate interpretation layer above Isolation Forest scores.

## Feature Audit

| feature | group | count | median | iqr | p05 | p95 | outlier_frequency_vs_old_training | outlier_frequency_vs_new_training |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| tps_raw_min | old_training_baseline | 3874 | 0 | 0 | 0 | 3 | 0.037687 | 0.001033 |
| tps_raw_min | new_normal_training | 4155 | 0 | 5 | 0 | 30 | 0.267629 | 0.049579 |
| tps_raw_min | normal_holdout | 1784 | 0 | 0 | 0 | 19 | 0.13565 | 0.012332 |
| tps_raw_min | warmup_candidate | 495 | 0 | 0 | 0 | 3 | 0.042424 | 0.006061 |
| tps_raw_min | battery_low_candidate | 2603 | 0 | 6 | 0 | 28 | 0.265079 | 0.03035 |
| tps_raw_min | high_rpm_candidate | 446 | 2 | 11 | 0 | 50.75 | 0.421525 | 0.116592 |
| tps_raw_min | known_abnormal | 464 | 34 | 17.75 | 24 | 95 | 1 | 0.75 |
| tps_voltage_min | old_training_baseline | 3874 | 0.488281 | 0 | 0.488281 | 0.566406 | 0.046464 | 0.001807 |
| tps_voltage_min | new_normal_training | 4155 | 0.488281 | 0.15625 | 0.488281 | 1.269531 | 0.280866 | 0.046209 |
| tps_voltage_min | normal_holdout | 1784 | 0.488281 | 0 | 0.488281 | 0.976562 | 0.143498 | 0.01065 |
| tps_voltage_min | warmup_candidate | 495 | 0.488281 | 0 | 0.488281 | 0.585938 | 0.052525 | 0.006061 |
| tps_voltage_min | battery_low_candidate | 2603 | 0.488281 | 0.15625 | 0.488281 | 1.210938 | 0.283135 | 0.027276 |
| tps_voltage_min | high_rpm_candidate | 446 | 0.546875 | 0.292969 | 0.488281 | 1.767578 | 0.44843 | 0.11435 |
| tps_voltage_min | known_abnormal | 464 | 0.820312 | 0.366211 | 0.585938 | 2.050781 | 1 | 0.25 |
| ect_c_delta | old_training_baseline | 3874 | 0 | 0 | -1 | 2 | 0.05937 | 0.104543 |
| ect_c_delta | new_normal_training | 4155 | 0 | 0 | -1 | 1 | 0.031528 | 0.056799 |
| ect_c_delta | normal_holdout | 1784 | 0 | 0 | -1 | 1.85 | 0.055493 | 0.096413 |
| ect_c_delta | warmup_candidate | 495 | 0 | 1 | -1 | 4 | 0.143434 | 0.20404 |
| ect_c_delta | battery_low_candidate | 2603 | 0 | 0 | -1 | 2 | 0.040722 | 0.106416 |
| ect_c_delta | high_rpm_candidate | 446 | 0 | 0 | -2 | 2 | 0.060538 | 0.121076 |
| ect_c_delta | known_abnormal | 464 | 1 | 3 | -5 | 4 | 0.5 | 0.5 |
| ect_c_first | old_training_baseline | 3874 | 90 | 1 | 85 | 92 | 0.09396 | 0.064275 |
| ect_c_first | new_normal_training | 4155 | 90 | 4 | 88 | 94 | 0.285439 | 0.018532 |
| ect_c_first | normal_holdout | 1784 | 89 | 1 | 89 | 93 | 0.058296 | 0.011211 |
| ect_c_first | warmup_candidate | 495 | 89 | 3 | 61 | 90 | 0.226263 | 0.272727 |
| ect_c_first | battery_low_candidate | 2603 | 90 | 3 | 73 | 93 | 0.24587 | 0.10219 |
| ect_c_first | high_rpm_candidate | 446 | 92 | 2 | 90 | 97 | 0.475336 | 0.150224 |
| ect_c_first | known_abnormal | 464 | 99 | 29 | 72 | 115.85 | 0.75 | 0.834052 |
| ect_c_slope | old_training_baseline | 3874 | 0 | 0 | -0.020408 | 0.040816 | 0.05937 | 0.104543 |
| ect_c_slope | new_normal_training | 4155 | 0 | 0 | -0.020408 | 0.020408 | 0.031528 | 0.056799 |
| ect_c_slope | normal_holdout | 1784 | 0 | 0 | -0.020408 | 0.037755 | 0.055493 | 0.096413 |
| ect_c_slope | warmup_candidate | 495 | 0 | 0.020408 | -0.020408 | 0.081633 | 0.143434 | 0.20404 |
| ect_c_slope | battery_low_candidate | 2603 | 0 | 0 | -0.020408 | 0.040816 | 0.040722 | 0.106416 |
| ect_c_slope | high_rpm_candidate | 446 | 0 | 0 | -0.040816 | 0.040816 | 0.060538 | 0.121076 |
| ect_c_slope | known_abnormal | 464 | 0.020408 | 0.061224 | -0.102041 | 0.081633 | 0.5 | 0.5 |
| iat_c_constant_signal | old_training_baseline | 3874 | 1 | 0 | 0 | 1 | 0 | 0 |
| iat_c_constant_signal | new_normal_training | 4155 | 1 | 0 | 0 | 1 | 0 | 0 |
| iat_c_constant_signal | normal_holdout | 1784 | 1 | 0 | 0 | 1 | 0 | 0 |
| iat_c_constant_signal | warmup_candidate | 495 | 1 | 0 | 0 | 1 | 0 | 0 |
| iat_c_constant_signal | battery_low_candidate | 2603 | 1 | 0 | 0 | 1 | 0 | 0 |
| iat_c_constant_signal | high_rpm_candidate | 446 | 1 | 0 | 0 | 1 | 0 | 0 |
| iat_c_constant_signal | known_abnormal | 464 | 0.5 | 1 | 0 | 1 | 0 | 0 |
| battery_voltage_mean | old_training_baseline | 3874 | 14.52 | 0.022 | 14.48 | 14.552 | 0.094476 | 0.16572 |
| battery_voltage_mean | new_normal_training | 4155 | 14.518 | 0.018 | 14.494 | 14.5446 | 0.051264 | 0.098917 |
| battery_voltage_mean | normal_holdout | 1784 | 14.518 | 0.02 | 14.494 | 14.554 | 0.06222 | 0.132287 |
| battery_voltage_mean | warmup_candidate | 495 | 14.52 | 0.02 | 14.498 | 14.552 | 0.064646 | 0.131313 |
| battery_voltage_mean | battery_low_candidate | 2603 | 14.518 | 0.02 | 14.486 | 14.548 | 0.067998 | 0.130234 |
| battery_voltage_mean | high_rpm_candidate | 446 | 14.518 | 0.016 | 14.496 | 14.534 | 0.033632 | 0.044843 |
| battery_voltage_mean | known_abnormal | 464 | 12.5 | 0.5 | 10.8 | 12.8 | 1 | 1 |
| battery_voltage_median | old_training_baseline | 3874 | 14.5 | 0 | 14.5 | 14.55 | 0.077956 | 0.109706 |
| battery_voltage_median | new_normal_training | 4155 | 14.5 | 0 | 14.5 | 14.5 | 0.042359 | 0.061853 |
| battery_voltage_median | normal_holdout | 1784 | 14.5 | 0 | 14.5 | 14.6 | 0.072309 | 0.098655 |
| battery_voltage_median | warmup_candidate | 495 | 14.5 | 0 | 14.5 | 14.55 | 0.028283 | 0.062626 |
| battery_voltage_median | battery_low_candidate | 2603 | 14.5 | 0 | 14.5 | 14.55 | 0.062236 | 0.084134 |
| battery_voltage_median | high_rpm_candidate | 446 | 14.5 | 0 | 14.5 | 14.5 | 0.026906 | 0.03139 |
| battery_voltage_median | known_abnormal | 464 | 12.5 | 0.5 | 10.8 | 12.8 | 1 | 1 |
