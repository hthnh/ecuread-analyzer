# Minimum Evidence Policy

## Quantization

| window_count | one_window_ratio | monitor_threshold_2pct | attention_threshold_15pct |
| --- | --- | --- | --- |
| 5 | 20.00% | 1 window already exceeds | 1 window exceeds |
| 7 | 14.29% | 1 window exceeds | 2 windows needed |
| 10 | 10.00% | 1 window exceeds | 2 windows needed |
| 20 | 5.00% | 1 window exceeds | 3 windows needed |

Small session sizes make anomaly ratios jump in coarse steps. This affects
session-level interpretation only; per-window anomaly scores should remain
available.

## Policy Comparison

| model | policy | semantics | affected_session_count | affected_session_ids | normal_holdout_false_before | normal_holdout_false_after | known_abnormal_limited_count | status_distribution_after |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| old | none | no evidence gate | 0 |  | 2 | 2 | 0 | attention=11, monitor=15, ok=9 |
| old | policy_a_window_lt_10 | window_count < 10 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=9, limited_data=2, monitor=15, ok=9 |
| old | policy_b_eligible_lt_140 | eligible_sample_count < 140 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=9, limited_data=2, monitor=15, ok=9 |
| old | policy_c_duration_lt_30000 | duration_ms < 30000 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=9, limited_data=2, monitor=15, ok=9 |
| old | combined_or | window_count < 10 OR eligible_sample_count < 140 OR duration_ms < 30000 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=9, limited_data=2, monitor=15, ok=9 |
| old | combined_and | window_count < 10 AND eligible_sample_count < 140 AND duration_ms < 30000 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=9, limited_data=2, monitor=15, ok=9 |
| old | window_n_5 | window_count < 5 | 1 | ride-20260916-1321 | 2 | 2 | 0 | attention=10, limited_data=1, monitor=15, ok=9 |
| old | window_n_10 | window_count < 10 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=9, limited_data=2, monitor=15, ok=9 |
| old | window_n_15 | window_count < 15 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=9, limited_data=2, monitor=15, ok=9 |
| old | window_n_20 | window_count < 20 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=9, limited_data=2, monitor=15, ok=9 |
| candidate | none | no evidence gate | 0 |  | 2 | 2 | 0 | attention=10, monitor=15, ok=10 |
| candidate | policy_a_window_lt_10 | window_count < 10 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=8, limited_data=2, monitor=15, ok=10 |
| candidate | policy_b_eligible_lt_140 | eligible_sample_count < 140 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=8, limited_data=2, monitor=15, ok=10 |
| candidate | policy_c_duration_lt_30000 | duration_ms < 30000 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=8, limited_data=2, monitor=15, ok=10 |
| candidate | combined_or | window_count < 10 OR eligible_sample_count < 140 OR duration_ms < 30000 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=8, limited_data=2, monitor=15, ok=10 |
| candidate | combined_and | window_count < 10 AND eligible_sample_count < 140 AND duration_ms < 30000 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=8, limited_data=2, monitor=15, ok=10 |
| candidate | window_n_5 | window_count < 5 | 1 | ride-20260916-1321 | 2 | 2 | 0 | attention=9, limited_data=1, monitor=15, ok=10 |
| candidate | window_n_10 | window_count < 10 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=8, limited_data=2, monitor=15, ok=10 |
| candidate | window_n_15 | window_count < 15 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=8, limited_data=2, monitor=15, ok=10 |
| candidate | window_n_20 | window_count < 20 | 2 | ride-20260901-0715;ride-20260916-1321 | 2 | 2 | 0 | attention=8, limited_data=2, monitor=15, ok=10 |

## Implemented Policy

The production Analyzer now uses the smallest clear interpretation gate:

```text
if window_count < 10:
    session_status = limited_data
```

This captures the 4-window and 7-window sessions without adding redundant
sample-count and duration gates. The raw anomaly ratio, health score, and
per-window scores should still be returned for debugging.

`limited_data` does not mean healthy. It also does not mean anomalous. It means
there is insufficient session-level evidence to interpret the existing window
scores strongly.

The current constant is:

```text
MIN_SESSION_WINDOWS_FOR_STATUS = 10
```

This is a product interpretation policy. It does not change the Isolation
Forest model, feature extraction, health score formula, anomaly thresholds, or
the per-window anomaly classifications.
