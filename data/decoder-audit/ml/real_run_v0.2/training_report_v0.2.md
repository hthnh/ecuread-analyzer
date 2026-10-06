# Profile v0.2 Real-Run ML Training Report

## Input Quality

- `r0024d0_c93a5fb4.dat.jsonl`: 6922 records, 6922 valid, 6922 ML-eligible, checksum failures 0, time regressions 0
- `r1032c0_849aa7f8.dat.jsonl`: 6899 records, 6899 valid, 6899 ML-eligible, checksum failures 0, time regressions 0
- `session_000001.jsonl`: 117 records, 117 valid, 117 ML-eligible, checksum failures 0, time regressions 0
- `session_000002.jsonl`: 5781 records, 5781 valid, 5781 ML-eligible, checksum failures 0, time regressions 0
- `session_000003.jsonl`: 1249 records, 1249 valid, 1249 ML-eligible, checksum failures 0, time regressions 0
- `session_000004.jsonl`: 3017 records, 3017 valid, 3017 ML-eligible, checksum failures 0, time regressions 0
- `session_000005.jsonl`: 7851 records, 7851 valid, 7851 ML-eligible, checksum failures 0, time regressions 0
- `session_000006.jsonl`: 923 records, 923 valid, 923 ML-eligible, checksum failures 0, time regressions 0
- `session_000007.jsonl`: 6389 records, 6389 valid, 6389 ML-eligible, checksum failures 0, time regressions 0

## Training Dataset

- Decoder profile: `honda_keihin_71_17_v0.2`
- Total records: 39148
- Valid frames: 39148
- ML-eligible frames: 39148
- Windows generated: 3874
- Feature count: 94
- Signal columns: `rpm`, `tps_voltage`, `tps_raw_candidate`, `tps_percent_calibrated`, `iat_c`, `ect_c`, `battery_voltage`

## Model

- Model directory: `data/models/honda_keihin_71_17_v0.2_real_run`
- Model version: `iforest-baseline-20260815T060116Z`
- Training score p05: -0.529521
- Training decision median: 0.137517

This model is an experimental unsupervised road-run baseline. It was trained without labels and without anomaly scoring the source sessions as faults.
