# Decoder Audit Summary

## 1. Verified Frame-Level Facts

- Profile: `honda_keihin_71_17_v0.1`
- Total parsed records: 13821
- Structurally valid frames: 13821
- Checksum rule verified as `sum(frame) % 256 == 0` for valid normalized 24-byte frames.
- Normalized 24-byte frames: 13821
- Legacy 29-byte frames normalized: 0

- `r0024d0_c93a5fb4.dat.jsonl`: 6922 parsed records, 6922 valid frames, checksum failure ratio 0.0000%
- `r1032c0_849aa7f8.dat.jsonl`: 6899 parsed records, 6899 valid frames, checksum failure ratio 0.0000%

## 2. High-Confidence Field Candidates

- `rpm`: bytes 4-5, big-endian unsigned integer.
- `tps_voltage_candidate`: byte 6, `raw * 5 / 256`.
- `iat_c_candidate`: byte 11 minus 40 with byte 10 as voltage candidate. This is a high-confidence candidate pending ground-truth validation, not a declared truth.
- `battery_v_candidate`: byte 14, `raw / 10`.

## 3. Weak Candidates

- `tps_position_raw_candidate`: byte 7 preserved raw; no percent formula selected.
- `map`: byte 12 voltage candidate and byte 13 engineering raw; scale unverified.
- `injector_raw_candidate`: bytes 15-16 big-endian raw; `/100`, `/250`, and `/256` are compared but not verified.
- `speed_or_signal_raw_candidate`: byte 18 preserved raw; not named speed without external reference.

## 4. Contradicted Existing Mappings

- `rpm`: bytes 4-5 via `big-endian word` (exact 100.00%, mean error 0)
- `tps_voltage`: byte 6 via `raw * 5 / 256` (exact 100.00%, mean error 0.000258106)
- `tps_percent`: byte 7 via `raw * 100 / 255` (exact 100.00%, mean error 0.00151919)
- `iat_c`: byte 12 via `raw - 40` (exact 100.00%, mean error 0)
- `ect_c`: byte 13 via `raw - 40` (exact 100.00%, mean error 0)
- `battery_v`: byte 14 via `raw / 10` (exact 100.00%, mean error 0)
- `injector_raw`: bytes 15-16 via `big-endian word` (exact 100.00%, mean error 0)
- `injector_ms`: bytes 15-16 via `big-endian word / 100` (exact 100.00%, mean error 0)
- `fuel_cut_inferred`: bytes 15-16 via `big-endian word == 0` (exact 100.00%, mean error 0)

The existing JSON fields `iat_c` and `ect_c` are explained by bytes 12 and 13 using `raw - 40`. Under the v0.1 candidate profile, those bytes belong to the MAP candidate pair, so the old temperature mapping is marked `offset_suspected`.

## 5. Unresolved Fields

`ect`, `map_scale`, `injector_ms_scale`, `ignition_raw_candidate`, `speed_or_signal_raw_candidate`, `bytes_19_22`, `vehicle_scope_metadata`

## 6. Required Ground-Truth Experiments

See `ground_truth_test_plan.md` for key-on engine-off, cold-start, warm-up, throttle sweep, battery, injector, and vehicle-speed validation procedures.

## 7. Dataset Readiness

- `ready_for_ml`: false
- Fields with sufficient candidate status for later ML consideration: `rpm`, `tps_voltage_candidate`, `iat_c_candidate`, `battery_v_candidate`

Blocking reasons:

- existing iat_c is sourced from byte 12, which this profile treats as MAP voltage candidate
- existing ect_c is sourced from byte 13, which this profile treats as MAP engineering raw candidate
- vehicle/ECU identification metadata and ground-truth sensor references are missing
- temperature and sensor offsets remain ambiguous until controlled validation
- trusted feature fields for Isolation Forest have not been approved under the new profile
