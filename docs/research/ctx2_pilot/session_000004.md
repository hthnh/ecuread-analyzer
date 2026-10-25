# session_000004: Urban Stops Pilot

Desk review, 2026-10-09. No annotation accepted. The urban description is not
timestamp ground truth. All times reference the full RAW capture start.
Source: `real_data/real_run/session_000004.jsonl`, SHA-256
`8fe7ee4652a9154007eed9d04d199911c909d13327b0f5bf90c7f3bddb34a0d7`.
Rules/config: `ctx2-rules-v1` /
`fd67888efcb842876003fd3140c48c88af78fab2a0f84df985ab18f1e41a2a6b`.

## Selected Intervals

Speed/TPS endpoints are medians of the first/last few measured samples; RPM is
the interval median. These do not constitute independently labelled ground truth.

| Proposed interval s | Label | RAW speed km/h | RPM | Measured TPS V | Review finding |
| --- | --- | --- | ---: | --- | --- |
| 396.875-406.126 | accelerating | 9.5 -> 39.5; range 8-41 | 5063 | 0.498 -> 1.895 | Large sustained change supports acceleration. |
| 66.626-85.125 | decelerating | 48 -> 10; range 9-48 | 4002 | 1.260 -> 0.605 | Measured slowing, not inferred traffic cause. |
| 201.376-209.875 | steady | 11 -> 8; range 8-11 | 1606 | 0.645 -> 0.488 | Relative steadiness permits slight drift; clearly moving. |
| 723.375-750.376 | variable | 6 -> 15; range 5-31 | 3670 | 0.586 -> 0.908 | Multiple changes are appropriately grouped as mixed. |
| 111.375-143.125 | stationary-running | 1 -> 0; all samples <=1 | 1428 | 0.488 -> 1.006 | Near-zero RAW speed supports a stop despite throttle activity at departure. |
| 38.877-40.876 | unknown | range 0-2 | 1760 | 0.547 -> 1.133 | Transitional/creeping evidence, not proof of stopping. |

At 111.375s, speed changes from 2-22 km/h in the preceding three seconds to
0-1 after. At 143.1255s it changes from 0 before to 2-20 after. The proposed
stop boundaries correspond to meaningful measured motion changes.

## Thermal and Events

Stable 0-101.6265s changes to unknown 101.6265-281.376s, then stable to 369.125s.
The first boundary's preceding 30s have ECT 89-90C; following 30s have 90-92C.
The unknown span includes temperature rises/falls that do not sustain the
warming rule for 20s. It is conservative abstention, not missing ECT (all
samples are available), a cold-start claim or overheating diagnosis.

Repeated-stop-go marker 111.375-233.375s groups three speed-supported cycles:
111.375-149.375, 213.876-220.625 and 220.625-233.375s. The last two are adjacent;
inspect the short restart/re-stop, rather than inferring three traffic lights.
TPS burst 396.751-399.000s has smoothed rise 0.742V, rate 0.424V/s and 0.499s
hold. Raw speed initially falls 10 -> 8 km/h while TPS opens 0.488 -> 1.309V,
then the accelerating interval begins. A TPS burst is not itself acceleration.

## Review Priority

108 operating runs and 9 thermal runs yield 110 joint intervals; 63 TPS bursts
and one stop-go marker are separate. Seven verified stops total 116.5s,
versus three / 51.7s in 007. One mixed moving interval remains below four
seconds; nine short unknown intervals preserve transition ambiguity.
Thermal unknown coverage is 385.7/755.0s, despite complete measured ECT. Check
that abstention is acceptable for the campaign's thermal taxonomy before review
at scale. No operating/thermal causes are inferred from the route narrative.

Full proposal IDs and measured boundary neighborhoods are in
`exports/ctx2_pilot_cases.json`.
