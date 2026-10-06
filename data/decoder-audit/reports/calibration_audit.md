# Phase 2 Calibration Decoder Audit

## v0.1 Uncertainty Resolution

| v0.1_uncertainty | controlled_experiment | result | new_confidence |
| --- | --- | --- | --- |
| RPM identity/scaling not grounded in controlled states | engine-off/key-on + cold-start | 0 while off, non-zero when engine operates | VERIFIED |
| TPS percent used raw*100/255 without endpoint validation | engine-off TPS sweep | byte6/byte7 verified as TPS-related; old percent full-scale is rejected | VERIFIED |
| IAT location unresolved | ambient cold baseline + warm-up | bytes10-11 behave as IAT | HIGH_CONFIDENCE |
| bytes12-13 were hypothesized as MAP | engine-start response + 15-min warm-up | MAP hypothesis rejected; bytes12-13 behave as engine-temperature thermistor pair | HIGH_CONFIDENCE |
| battery scaling lacked external reference | key-on VOM measurement | byte14/10 agrees within a small offset | VERIFIED |

## Calibration Sessions

- `cal_20260812_152832_2a54f0` / `cal_02_tps_sweep`: 471 frames, 471 valid, checksum failures 0, header failures 0, malformed records 0, duration 117.58 s
- `cal_20260814_152603_8fd9ed` / `cal_01_cold_key_on`: 741 frames, 741 valid, checksum failures 0, header failures 0, malformed records 1, duration 186.21 s
- `cal_20260814_153329_812a3f` / `cal_03_cold_start_warmup`: 3629 frames, 3629 valid, checksum failures 0, header failures 0, malformed records 1, duration 907.59 s

## Required Signal Decision Table

| Signal | v0.1 mapping/hypothesis | Calibration evidence | Identity status | Scaling status | Recommended ML use |
| --- | --- | --- | --- | --- | --- |
| RPM | bytes 4-5 big-endian unsigned integer | 0 RPM in stationary engine-off sessions; non-zero and plausible after engine operation begins | VERIFIED | VERIFIED | use |
| TPS voltage | byte6 * 5 / 256 | TPS sweep byte6 25.0 to 225.0 (0.488 V to 4.395 V) | VERIFIED | VERIFIED | use |
| TPS raw | byte7 raw/position candidate | TPS sweep byte7 0.0 to 156.0, corr(byte6, byte7)=0.999978 | VERIFIED | VERIFIED_AS_RAW | use raw or calibrated percent |
| TPS percent | byte7 * 100 / 255 | old formula gives full=61.18% at observed full raw=156.0; endpoint calibration gives 100% | VERIFIED | REJECTED_OLD_FORMULA | use endpoint-calibrated percent, not raw*100/255 |
| IAT | bytes10-11 as IAT pair; byte11 - 40 | baseline byte11-40 is 36.00 C; ambient error 1.50 C; 15-min warm-up drift is small | HIGH_CONFIDENCE | HIGH_CONFIDENCE | use |
| ECT | bytes8-9 generic ECT unavailable; byte13 was old ect_c source | bytes8-9 remain FF/FF; byte13-40 starts 36.00 C (ambient error 1.50 C) and reaches warm operating temperature | HIGH_CONFIDENCE | HIGH_CONFIDENCE | use |
| MAP voltage | byte12 * 5 / 256 as MAP voltage | byte12 does not show an immediate manifold-pressure transition; it slowly falls during warm-up with byte13 rising | REJECTED | REJECTED_AS_MAP | exclude as MAP |
| MAP engineering raw | byte13 raw as MAP engineering value | byte13 follows slow engine-temperature warm-up, not pressure dynamics | REJECTED | REJECTED_AS_MAP | exclude as MAP |
| Battery | byte14 / 10 | external VOM 12.75 V vs ECU mean 12.497 V | VERIFIED | VERIFIED | use |
| Injector raw | bytes15-16 big-endian raw | HIGH_CONFIDENCE injector-related raw control signal; /100 milliseconds remains UNVERIFIED | HIGH_CONFIDENCE | VERIFIED_AS_RAW | exclude from ML V1 |
| Injector ms | bytes15-16 / 100 ms | no oscilloscope or logic-analyzer pulse-width reference was provided | HIGH_CONFIDENCE | UNVERIFIED | exclude |
| Byte17 | unknown raw byte | UNVERIFIED; relationships are reported but no physical identity is assigned | UNVERIFIED | UNVERIFIED | exclude |
| Byte18 / speed | speed_or_signal_raw_candidate | REJECTED as direct vehicle speed: all calibration scenarios were stationary while byte18 is non-zero and changes | REJECTED | REJECTED_AS_SPEED | exclude |

## Key Evidence

- TPS endpoints: byte6 `25` to `225`; byte7 `0` to `156`; byte6/byte7 correlation `0.999978`.
- TPS old percentage formula at full: `61.18%`; endpoint-calibrated full is `100.00%`.
- IAT candidate: byte10 voltage candidate and `byte11 - 40`; ambient error `1.50 C`.
- ECT candidate: bytes8-9 remain `[255]` / `[255]`; engine-temperature signal found at byte13 with `byte13 - 40`, ambient error `1.50 C`.
- v0.1 MAP hypothesis: byte12 delta 2-10 s after start `0.000`, byte13 delta `0.000`; over warm-up byte12 falls while byte13 rises.
- Battery: VOM `12.75 V`, ECU byte14-derived mean `12.497 V`, difference `-0.253 V`.

## Old Mappings Rejected

`iat_c = byte12 - 40`, `MAP hypothesis for bytes12-13`, `speed_kmh/direct speed interpretation for byte18`, `tps_percent = byte7 * 100 / 255 as physical full-scale percent`

## Promotions

- VERIFIED: `RPM`, `TPS voltage`, `TPS raw`, `Battery`
- HIGH_CONFIDENCE: `TPS percent calibrated`, `IAT`, `ECT`, `Injector raw`
- UNVERIFIED/UNAVAILABLE: `MAP actual location`, `Injector ms`, `Byte17`, `Byte18 speed`, `bytes8-9 legacy ECT pair`

## ML Readiness

- `ready_for_ml`: true
- `ml_approved`: `RPM`, `TPS voltage`, `TPS raw`, `TPS percent`, `IAT`, `ECT`, `Battery`

Reasons:

- controlled calibration now supports a defensible non-anomaly-training signal set
- use profile-derived v0.2 fields, not the old production iat_c field

## v0.2 Profile

Generated: `data/decoder-audit/profiles/honda_keihin_71_17_v0.2.json`
