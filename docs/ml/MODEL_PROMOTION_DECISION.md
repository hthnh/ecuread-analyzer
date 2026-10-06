# Model Promotion Decision

## Decision

`KEEP_CURRENT_MODEL`

## Candidate

- Version: `iforest-human-normal-8122563e-e4680f77`
- Production model preserved: `iforest-baseline-20260920T091709Z`
- Candidate artifact directory: `data/models/honda_keihin_71_17_v2_candidate_human_normal`

## Promotion Criteria

- Normal holdout improved: 2 candidate false statuses vs 2 old false statuses.
- Known-abnormal/candidate sensitivity not obviously destroyed: see `human_label_model_comparison.csv`.
- Schema/decoder compatibility: `canonical-telemetry-v2`, `ecu-window-features-v1`, `honda_keihin_71_17:1.0.0`.
- Evaluation split: session-separated, no leakage.

## Normal Holdout

Old:

| count | ok_count | monitor_count | attention_count | false_monitor_count | false_attention_count |
| --- | --- | --- | --- | --- | --- |
| 4 | 2 | 2 | 0 | 2 | 0 |

Candidate:

| count | ok_count | monitor_count | attention_count | false_monitor_count | false_attention_count |
| --- | --- | --- | --- | --- | --- |
| 4 | 2 | 2 | 0 | 2 | 0 |

## Current Threshold Row For Candidate

| model | monitor_ratio | attention_ratio | attention_health_lt | monitor_health_lt | is_current_policy | normal_holdout_count | normal_holdout_ok | normal_holdout_false_monitor | normal_holdout_false_attention | known_abnormal_attention | known_abnormal_monitor | known_abnormal_ok | warmup_distribution | battery_low_distribution | high_rpm_distribution |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| candidate | 0.02 | 0.15 | 50 | 80 | True | 4 | 2 | 2 | 0 | 4 | 0 | 0 | attention=1, monitor=1 | attention=2, monitor=5, ok=2 | attention=1 |

## Threshold Decision

Threshold changes are not justified in this round. The grid shows that looser
monitor thresholds can reduce normal-holdout monitor statuses, but the holdout
set has only four sessions and the candidate baseline does not improve the
primary false-positive metric under the current production policy.

The candidate should remain side-by-side with production until the owner reviews
candidate/context cohort behavior and decides whether these labels should be
product-normal, limited-data, or out-of-distribution statuses.
