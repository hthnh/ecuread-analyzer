# session_000007: Intercity Pilot

Desk review, 2026-10-09. No annotation accepted. The rider's intercity description
does not establish timestamp ground truth. Times reference the full RAW start.
Source: `real_data/real_run/session_000007.jsonl`, SHA-256
`509cc87f61d71f39f7ca4f0651424d1ba73dc15b97f49325b7586dcbbec77879`.
Rules/config: `ctx2-rules-v1` /
`fd67888efcb842876003fd3140c48c88af78fab2a0f84df985ab18f1e41a2a6b`.

## Selected Intervals

Speed/TPS endpoints are medians of the first/last few samples; RPM is the median.

| Proposed interval s | Label | RAW speed km/h | RPM | Measured TPS V | Review finding |
| --- | --- | --- | ---: | --- | --- |
| 208.375-223.376 | accelerating | 13 -> 29; range 13-30 | 3909 | 0.742 -> 1.162 | Sustained speed increase. |
| 649.376-663.626 | decelerating | 26 -> 4.5; range 4-27 | 3360 | 0.713 -> 0.488 | Sustained decrease, not a braking label. |
| 479.626-498.626 | steady | 39 -> 40.5; range 38-41 | 5224 | 0.840 -> 1.182 | Narrow speed spread despite varying TPS. |
| 1104.376-1132.876 | variable | 28 -> 13.5; range 12-29 | 3662 | 0.488 -> 0.869 | Mixed local trends; not an uninterrupted steady cruise. |
| 795.875-817.875 | stationary-running | 1 -> 0; all samples <=1 | 1368 | 0.488 -> 0.898 | Actual speed supports a stop even in an intercity description. |
| 0-8.876 | unknown | range 0-2 | 1388 | 0.488 -> 0.918 | Creeping/initial uncertainty, not unsupported stationary inference. |

Before 795.875s, the three-second speed range is 3-27 km/h, and after it is
0-1. At 817.8755s the three-second range changes from 0 before to 2-28 after.
These stop boundaries match meaningful measured movement changes.

## Thermal and Events

Stable is proposed 0-784.3755s; first 180s ECT stays 89-90C. Unknown follows
784.3755-848.877s, then stable 848.877-915.6265s. Later warming
915.6265-940.3745s has 89C immediately before and a rise 89 -> 92C in the
following 30s. Unknown 940.3745-982.126s brackets the subsequent drop
92 -> 90C. Centered windows and snapping make these approximate trend
boundaries, not cold-start or thermostat-event truth.

Repeated-stop-go marker 795.875-961.125s contains three verified cycles:
795.875-823.6255, 856.625-868.126 and 927.6255-961.125s. It is supported by
speed even though this session has fewer stops overall; it does not establish
urban traffic. TPS burst 954.501-956.750s has smoothed rise 0.771V, rate
0.441V/s and 0.498s hold, with RAW TPS 1.172 -> 1.875V and speed 10 -> 26 km/h.

## Review Priority

215 operating runs and 7 thermal runs create 216 joint intervals, plus 64 TPS
bursts and one stop-go marker. Three stops total 51.7s versus seven / 116.5s
in 004. No known operating run is below four seconds; four short unknown runs
remain. Review the local stop-go cluster and later thermal rise, rather than
using the ride description to relabel the whole session as steady movement.

Full proposal IDs and measured boundary neighborhoods are in
`exports/ctx2_pilot_cases.json`.
