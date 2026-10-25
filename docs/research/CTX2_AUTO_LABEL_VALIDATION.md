# CTX2 Auto-Label Validation

Generated: 2026-10-09T13:44:13.052795+00:00. Rules: `ctx2-rules-v1`.

## Scope and Provenance

The implementation is deterministic signal processing under
`app/context_annotation`; there are no models, LLMs, fault/anomaly interpretations,
analyzer verdict dependencies, or production decoder changes. Candidate speed
uses native Honda Table 17 byte 19 after shared 24-byte / FF-prefixed 29-byte
normalization, header validation, and checksum validation. Decoder version:
`ctx2-honda-table17-speed-v1`. Speed is rider-corroborated, not GPS-calibrated.
RAW SHA, offset/count, alignment basis, and unavailable reasons are persisted.

The pinned corpus has 57 entries, of which 27 have verified full RAW ranges and
30 have unavailable speed. The 57 entries include aliases, slices, and at least
seven synthetic sessions. They do not establish 57 independent captures. The
preflight and `raw_signal_sources.json` document these provenance limitations.

## Algorithms and Thresholds

Timestamp validation rejects missing mixed timestamps, duplicates, and reversed
time. A complete declared sampling interval can supply timing if all timestamps
are absent; sample index alone cannot. Gaps exceed max(3 seconds, 8 median
sampling intervals). Gaps are explicit unknown bands, without interpolation.

Within each capture block, time-based median filtering uses a 1-second window.
Speed requires original validated frames; missing/invalid samples stay missing.
Stationarity requires both raw and smoothed speed <=1 km/h and RPM >=400.
Movement enters at >=3 km/h and exits at <=1, exceeding byte quantization noise.
RPM 1000-2200 is reported as an idle-region observation, never proof of stopping.
Zero RPM requires baseline TPS and no conflicting movement for engine-off
proposals. The common Honda RPM value 20 is not silently treated as zero.

Acceleration/deceleration uses robust first/last thirds of a 6-second window:
>=0.7 km/h/s and >=3 km/h change. Steady movement has an 8-second robust speed
spread <=3 km/h. Minimum operating persistence is 4 seconds. Short motion
changes expand into mixed intervals, preserving long trends and evidence gaps.
Speed steps >35 km/h/s are withheld as possible candidate corruption rather
than interpreted as acceleration. No braking inference is made.

Thermal analysis uses a 60-second temporal window, at least 20 seconds of data,
and median thirds. Warming needs >=0.05 C/s and >=2 C rise. Stable needs an
absolute slope <=0.03 C/s and robust spread <=3 C; cooling or insufficient
evidence remains unknown. Thermal states persist for 20 seconds, and nearby
boundaries snap to operating transitions. These trend thresholds exceed ECT
quantization; absolute temperature never proves completion of warm-up.

TPS baseline is its valid-sample fifth percentile. A burst requires >=0.30 V
rise, >=0.35 V/s opening, and >=0.40 V above baseline held for 0.5 seconds.
For RAW-only TPS the equivalent research thresholds are 20 counts rise,
20 counts/s, and 25 counts above baseline. These conservative deltas use the
sensor's voltage/raw scale, not the maximum throttle seen in one ride.
Events have a 3-second cooldown. Repeated stop-go requires three verified
stationary-to-moving cycles within 180 seconds. Start/stop events require
sustained observed zero/nonzero RPM, without guessing the state before capture.

Thresholds are explicit in `RuleConfig`, the proposal document, and original
proposal provenance. They express quantization margins, plausible temporal
scales, and sustained behavior. They are research defaults, not vehicle-wide
calibration and not fitted to reproduce the four narratives.

## Four Real RAW Cases

Every frame in the four files passed speed frame validation. Narratives are
qualitative rider observations, not independently labelled timestamp truth.
No precision, recall, probability, or full segmentation accuracy is claimed.

| Session | Segments | Median duration s | Unknown motion | Stationary intervals/time | TPS bursts | Thermal warming/stable/unknown s | Segments/min | Speed median/p95/max km/h |
| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | --- |
| session_000002 | 203 | 5.75 | 1.62% | 3 / 32.0s | 83 | 180.6 / 1148.5 / 117.4 | 8.42 | 22.0 / 48.0 / 79.0 |
| session_000004 | 110 | 5.00 | 0.96% | 7 / 116.5s | 63 | 0.0 / 369.3 / 385.7 | 8.74 | 23.0 / 51.0 / 64.0 |
| session_000005 | 255 | 5.50 | 1.03% | 4 / 120.3s | 138 | 36.0 / 1651.4 / 276.6 | 7.79 | 24.0 / 48.0 / 62.0 |
| session_000007 | 216 | 6.00 | 0.88% | 3 / 51.7s | 64 | 24.7 / 1449.9 / 123.4 | 8.11 | 22.0 / 41.0 / 66.0 |

004 has more stationary time and stop intervals than 007 in these captures.
002 contains an observable rising ECT interval; 005 is mostly stable and is not
forced to have a full warm-up. Any warming in an already-hot capture describes
a measured rise, not a conclusion that the engine was cold.

Stationary segments were checked against actual speed values, throttle events
against measured rise/rate/hold evidence, and operating segments for overlap.
All four checks pass: True.

Median within-segment speed p90-p10 spread:

| Session | Steady km/h | Variable km/h |
| --- | ---: | ---: |
| session_000002 | 2.00 | 5.50 |
| session_000004 | 2.35 | 7.60 |
| session_000005 | 2.00 | 6.50 |
| session_000007 | 1.55 | 5.50 |

## Pinned Corpus Preview

Successful: 57; failed: 0;
unavailable speed: 30; partial evidence:
56; proposals pending human review:
5035. Verified coverage added: 0 seconds.
Event distribution across corpus entries: {'engine_stop': 11, 'repeated_stop_go': 7, 'throttle_burst': 2568}.
The following includes additional captures and calibration sessions outside the
four case studies, using the same thresholds without refitting.

| Corpus entry | Speed | Segments | Min/median/p90/max s | Unknown motion | Stops | Segments/min | Conflicting samples | Thermal warming/stable/unknown s |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| calib-20260812-152832-2a54f0 | available | 2 | 13.38 / 58.75 / 95.05 / 104.12 | 88.6% | 0 | 1.02 | 0 | 0.0 / 117.5 / 0.0 |
| calib-20260814-152603-8fd9ed | available | 1 | 185.50 / 185.50 / 185.50 / 185.50 | 0.0% | 0 | 0.32 | 0 | 0.0 / 185.5 / 0.0 |
| calib-20260814-153329-812a3f | available | 12 | 0.25 / 32.62 / 220.17 / 262.50 | 0.0% | 1 | 0.79 | 0 | 281.0 / 175.6 / 450.4 |
| calib-real-run-r0024d0-c93a5fb4-part-01 | available | 172 | 0.12 / 5.50 / 13.50 / 29.50 | 2.9% | 6 | 8.25 | 0 | 221.9 / 912.3 / 117.4 |
| calib-real-run-r0024d0-c93a5fb4-part-02 | available | 58 | 0.25 / 6.25 / 17.33 / 22.75 | 0.2% | 5 | 7.24 | 0 | 52.2 / 346.2 / 82.0 |
| calib-real-run-r1032c0-849aa7f8-part-01 | available | 152 | 0.25 / 5.75 / 13.47 / 154.38 | 1.0% | 7 | 7.29 | 0 | 60.5 / 895.9 / 294.6 |
| calib-real-run-r1032c0-849aa7f8-part-02 | available | 58 | 0.25 / 5.00 / 17.47 / 73.62 | 1.9% | 5 | 7.33 | 0 | 39.0 / 357.5 / 78.3 |
| calib-real-run-session-000001 | available | 7 | 0.75 / 4.50 / 7.42 / 7.50 | 38.8% | 2 | 14.48 | 0 | 29.0 / 0.0 / 0.0 |
| calib-real-run-session-000002-part-01 | available | 176 | 0.75 / 5.88 / 12.50 / 32.00 | 1.8% | 3 | 8.44 | 0 | 180.6 / 965.4 / 105.2 |
| calib-real-run-session-000002-part-02 | available | 28 | 1.00 / 5.63 / 13.36 / 15.50 | 0.5% | 0 | 8.62 | 0 | 0.0 / 176.9 / 18.1 |
| calib-real-run-session-000003 | available | 41 | 0.25 / 5.25 / 10.00 / 70.87 | 0.1% | 2 | 7.87 | 0 | 0.0 / 307.9 / 4.6 |
| calib-real-run-session-000004 | available | 110 | 0.12 / 5.00 / 13.03 / 31.75 | 1.0% | 7 | 8.74 | 0 | 0.0 / 369.3 / 385.7 |
| calib-real-run-session-000005-part-01 | available | 167 | 0.25 / 5.75 / 13.10 / 30.75 | 0.5% | 4 | 8.01 | 0 | 36.0 / 964.7 / 249.5 |
| calib-real-run-session-000005-part-02 | available | 89 | 1.25 / 5.50 / 15.10 / 44.75 | 1.9% | 0 | 7.48 | 0 | 0.0 / 693.9 / 19.6 |
| calib-real-run-session-000006 | available | 30 | 0.12 / 5.00 / 16.70 / 31.75 | 8.3% | 3 | 7.80 | 0 | 127.6 / 86.9 / 16.2 |
| calib-real-run-session-000007-part-01 | available | 168 | 0.50 / 6.00 / 12.57 / 28.50 | 1.0% | 3 | 8.06 | 0 | 24.7 / 1119.8 / 106.3 |
| calib-real-run-session-000007-part-02 | available | 50 | 0.88 / 5.25 / 13.43 / 23.50 | 0.3% | 0 | 8.65 | 0 | 0.0 / 310.3 / 36.8 |
| esp-fake-low-battery-20260920144735 | unavailable | 3 | 18.88 / 18.88 / 213.38 / 262.00 | 100.0% | 0 | 0.60 | 0 | 0.0 / 262.0 / 37.8 |
| esp-fake-overheat-20260920144735 | unavailable | 7 | 3.75 / 42.25 / 75.00 / 87.00 | 100.0% | 0 | 1.40 | 0 | 42.2 / 129.0 / 128.5 |
| esp-fake-rpm-spike-20260920144735 | unavailable | 3 | 18.88 / 18.88 / 213.38 / 262.00 | 100.0% | 0 | 0.60 | 0 | 0.0 / 262.0 / 37.8 |
| esp-real-session_000018-20260920144735 | unavailable | 6 | 1.25 / 21.69 / 2223.37 / 4415.50 | 100.0% | 0 | 0.08 | 0 | 30.4 / 4446.7 / 18.9 |
| esp-real-session_000024-20260920144735 | unavailable | 27 | 3.25 / 39.75 / 231.45 / 312.25 | 100.0% | 0 | 0.83 | 0 | 0.0 / 1518.1 / 430.9 |
| ride-20260901-0715 | available | 7 | 0.75 / 4.50 / 7.42 / 7.50 | 38.8% | 2 | 14.48 | 0 | 29.0 / 0.0 / 0.0 |
| ride-20260902-1032 | available | 153 | 1.50 / 6.00 / 12.50 / 32.00 | 1.4% | 2 | 8.15 | 0 | 180.6 / 858.8 / 86.9 |
| ride-20260902-1050 | available | 48 | 0.75 / 5.38 / 13.47 / 21.00 | 2.3% | 1 | 9.00 | 0 | 0.0 / 250.3 / 69.7 |
| ride-20260903-1349 | available | 41 | 0.25 / 5.25 / 10.00 / 70.87 | 0.1% | 2 | 7.87 | 0 | 0.0 / 307.9 / 4.6 |
| ride-20260904-1623 | available | 110 | 0.12 / 5.00 / 13.03 / 31.75 | 1.0% | 7 | 8.74 | 0 | 0.0 / 369.3 / 385.7 |
| ride-20260904-1810 | unavailable | 1 | 299.75 / 299.75 / 299.75 / 299.75 | 100.0% | 0 | 0.20 | 0 | 0.0 / 0.0 / 299.8 |
| ride-20260905-0840 | available | 147 | 0.25 / 5.75 / 13.60 / 30.75 | 0.6% | 3 | 7.84 | 0 | 36.0 / 875.6 / 213.6 |
| ride-20260905-0858 | available | 107 | 0.25 / 5.25 / 15.45 / 44.75 | 1.6% | 1 | 7.66 | 0 | 0.0 / 775.5 / 63.0 |
| ride-20260907-1157 | available | 30 | 0.12 / 5.00 / 16.70 / 31.75 | 8.3% | 3 | 7.80 | 0 | 127.6 / 86.9 / 16.2 |
| ride-20260908-1431 | available | 152 | 0.50 / 6.00 / 12.47 / 26.50 | 1.2% | 3 | 8.10 | 0 | 24.7 / 994.5 / 106.3 |
| ride-20260908-1449 | available | 65 | 0.88 / 5.75 / 13.50 / 23.50 | 0.2% | 0 | 8.26 | 0 | 0.0 / 432.2 / 40.0 |
| ride-20260909-0725 | unavailable | 3 | 18.88 / 18.88 / 213.38 / 262.00 | 100.0% | 0 | 0.60 | 0 | 0.0 / 262.0 / 37.8 |
| ride-20260909-1748 | unavailable | 3 | 4.75 / 68.12 / 195.72 / 227.62 | 100.0% | 0 | 0.60 | 0 | 295.7 / 0.0 / 4.8 |
| ride-20260910-0922 | unavailable | 1 | 132.50 / 132.50 / 132.50 / 132.50 | 100.0% | 0 | 0.45 | 0 | 132.5 / 0.0 / 0.0 |
| ride-20260911-1239 | unavailable | 9 | 3.25 / 36.75 / 221.30 / 865.50 | 100.0% | 0 | 0.48 | 0 | 96.6 / 959.7 / 69.1 |
| ride-20260911-1257 | unavailable | 17 | 1.75 / 35.38 / 179.70 / 323.00 | 100.0% | 0 | 0.91 | 0 | 132.1 / 824.4 / 168.8 |
| ride-20260911-1316 | unavailable | 20 | 3.75 / 40.00 / 136.71 / 214.50 | 100.0% | 0 | 1.07 | 0 | 63.0 / 817.7 / 244.3 |
| ride-20260911-1335 | unavailable | 25 | 0.25 / 40.25 / 72.55 / 212.75 | 99.5% | 0 | 1.34 | 0 | 174.5 / 688.1 / 255.4 |
| ride-20260912-1556 | unavailable | 8 | 13.00 / 32.44 / 346.01 / 354.50 | 100.0% | 0 | 0.55 | 0 | 69.2 / 722.1 / 82.1 |
| ride-20260913-0730 | unavailable | 8 | 30.25 / 61.12 / 291.34 / 663.12 | 100.0% | 0 | 0.43 | 0 | 0.0 / 842.4 / 283.4 |
| ride-20260913-0748 | unavailable | 2 | 19.87 / 562.50 / 996.60 / 1105.13 | 100.0% | 0 | 0.11 | 0 | 0.0 / 1105.1 / 19.9 |
| ride-20260913-0807 | unavailable | 5 | 2.62 / 48.37 / 585.95 / 861.25 | 100.0% | 0 | 0.27 | 0 | 0.0 / 1034.3 / 91.0 |
| ride-20260913-0826 | unavailable | 6 | 4.62 / 35.62 / 515.19 / 955.50 | 100.0% | 0 | 0.32 | 0 | 0.0 / 1061.6 / 57.6 |
| ride-20260914-1047 | unavailable | 8 | 10.13 / 37.40 / 152.77 / 285.25 | 98.2% | 0 | 0.87 | 0 | 0.0 / 404.0 / 144.7 |
| ride-20260914-1935 | unavailable | 1 | 299.75 / 299.75 / 299.75 / 299.75 | 100.0% | 0 | 0.20 | 0 | 0.0 / 0.0 / 299.8 |
| ride-20260916-1321 | unavailable | 2 | 10.13 / 10.75 / 11.25 / 11.38 | 52.9% | 0 | 5.58 | 0 | 0.0 / 0.0 / 21.5 |
| ride-20260917-1638 | unavailable | 5 | 10.37 / 76.50 / 442.80 / 645.00 | 98.8% | 0 | 0.34 | 0 | 0.0 / 794.9 / 96.4 |
| ride-20260918-0815 | unavailable | 1 | 299.75 / 299.75 / 299.75 / 299.75 | 100.0% | 0 | 0.20 | 0 | 0.0 / 0.0 / 299.8 |
| ride-20260918-0855 | unavailable | 7 | 6.37 / 30.25 / 231.90 / 240.75 | 99.2% | 0 | 0.56 | 0 | 0.0 / 682.9 / 72.1 |
| ride-20260919-1129 | unavailable | 11 | 6.50 / 40.00 / 148.00 / 648.75 | 100.0% | 0 | 0.59 | 0 | 0.0 / 924.8 / 200.4 |
| ride-20260919-1147 | unavailable | 20 | 9.75 / 30.00 / 89.47 / 357.75 | 100.0% | 0 | 1.07 | 0 | 46.5 / 732.9 / 345.9 |
| ride-20260919-1206 | unavailable | 16 | 9.25 / 35.62 / 180.38 / 282.75 | 100.0% | 0 | 0.85 | 0 | 0.0 / 814.4 / 311.4 |
| ride-20260919-1225 | unavailable | 7 | 5.00 / 10.38 / 371.67 / 720.50 | 99.0% | 0 | 0.41 | 0 | 0.0 / 979.6 / 34.6 |
| ride-20260920-1446 | unavailable | 21 | 0.25 / 39.75 / 111.00 / 200.50 | 100.0% | 0 | 1.12 | 0 | 123.0 / 700.7 / 302.5 |
| ride-20260920-1504 | unavailable | 15 | 0.50 / 33.50 / 99.90 / 245.25 | 100.0% | 0 | 1.09 | 0 | 24.0 / 530.3 / 268.3 |

All pinned source hashes match: True.
All generated operating segments are nonoverlapping:
True. Source files and accepted sidecars
are not written by the preview command. Original RAW references use hashes,
and telemetry is not copied into suggestions.

## Human Review and Compatibility

The UI displays RPM, TPS, candidate speed, and ECT on common elapsed-time axes,
with separate proposed/reviewed operating and thermal lanes and event markers.
Wheel zoom and pan preserve these coordinates. Every proposal can be inspected,
corrected, accepted, or rejected. Batch/range acceptance skips conflicts with
reviewed segments. It cannot overwrite a confirmed human annotation.

Pending/rejected suggestions do not count as coverage or enter dataset freezes.
Accepted segments retain auto origin, original rule/config/evidence, reviewer,
confirmation time, original proposal, and corrections. Events have separate
storage within the accepted document and contribute no duration. Reruns preserve
reviewed decisions and accepted sidecars. V1 labels remain explicitly marked v1,
without automatic translation into v2 operating context.

The real-session browser verification uses isolated annotation/suggestion
directories, so test acceptance does not impersonate review of the campaign.
Workflow verification available: True.

## Verification

Focused CTX1/CTX2 tests cover frame normalization, malformed/missing RAW,
timestamp integrity, gaps, stops, speed trends, TPS bursts, thermal trends,
persistence, source guards, v1 preservation, review persistence, events versus
duration, and reproducible v2 freeze provenance. Browser verification includes
desktop/mobile screenshots, rendered canvas pixels, aligned plot coordinates,
manual correction, acceptance, reload, and unchanged source/annotation hashes.

Analyzer regression testing retained one pre-existing failure:
`tests/test_canonical_pipeline.py::test_feature_schema_mismatch_is_rejected`.
The existing production inference changes degrade incompatible models instead
of raising the exception expected by that test. CTX2 does not modify this path
or the test. Other tests in the selected analyzer regression set passed.

## Ambiguities and Limits

Candidate speed is quantized and not independently calibrated. Near-zero speed
can include very slow creeping, so stationary proposals still need human review. Missing RAW,
changed RAW hashes, failed frame validation, or alignment errors withhold speed.
RPM/TPS alone cannot establish motion or stationarity. Capture gaps, isolated
invalid samples, ambiguous low RPM, and 1-3 km/h transitions retain unknown
bands; some of these are shorter than the operating persistence threshold.
Thermal intersections can also yield short boundary bands. Fragmentation is
reported rather than hidden, and needs researcher assessment before acceptance.

Sensor-only context does not establish city/highway, braking, traffic lights,
traffic jams, route destinations, GPS coordinates, or faults. Route metadata is
human-provided. Freeze readiness still requires review of the intended corpus;
this report establishes a reviewable workflow, not a completed labelled dataset.

Final decision: `auto_label_review_ready`.
