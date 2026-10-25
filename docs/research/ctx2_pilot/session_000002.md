# session_000002: Warm-Up Pilot

Desk review, 2026-10-09. No annotation accepted. Rider description is context,
not timestamp ground truth. Times below are seconds from the full RAW start.
Source: `real_data/real_run/session_000002.jsonl`, SHA-256
`19071f9dac5a874ca4245233a3bb94770dc34d8db10a1dfa54fbf6cdb29c5eaa`.
Rules/config: `ctx2-rules-v1` /
`fd67888efcb842876003fd3140c48c88af78fab2a0f84df985ab18f1e41a2a6b`.

## Selected Intervals

Speed endpoints are medians of the first/last few measured samples, not fitted
slopes. RPM is the interval median. TPS is measured voltage.

| Proposed interval s | Label | RAW speed km/h | RPM | Measured TPS V | Review finding |
| --- | --- | --- | ---: | --- | --- |
| 700.376-713.378 | accelerating | 19.5 -> 56.5; range 19-58 | 5708 | 1.045 -> 2.637 | Sustained speed increase, not a small fluctuation. |
| 982.877-1001.376 | decelerating | 75.5 -> 33; range 31-76 | 5751 | 1.113 -> 0.488 | Sustained decrease; does not prove braking. |
| 1229.127-1244.127 | steady | 4 -> 4; range 3-7 | 1861 | 0.508 -> 0.488 | Slow movement; must not become stationary from RPM alone. |
| 808.376-840.377 | variable | 24.5 -> 30; range 19-32 | 4286 | 0.840 -> 0.938 | Mixed changes support variable rather than one monotonic trend. |
| 930.877-945.627 | stationary-running | 0 -> 0; all samples <=1 | 1412 | 0.488 -> 0.869 | Actual near-zero speed supports the stop proposal. |
| 118.877-124.377 | unknown | range 0-2 | 2966 | 0.488 -> 0.654 | Creeping/short persistence, not missing RAW or evidence of a complete stop. |
| 1436.127-1446.503 | engine-off | range 0-1 | 0 | 0.488 throughout | Sustained observed zero RPM; no assumption about before capture. |

The stationary start has speed 3-23 km/h in the preceding three seconds and
0-1 immediately after. The exit has 0-1 before and 2-22 after. These boundaries
correspond to measured stop/departure changes, within sample/temporal-rule limits.

## Thermal and Events

Warming is proposed at 0-180.626s: ECT rises from 48 to 87C in the first 180s.
Then 180.626-190.626s is unknown, followed by stable 190.626-956.877s. Around the
first boundary, the preceding 30s rise 83 -> 87C and the following 30s stay 87C.
This supports a transition from rising to locally flat temperature, not a precise
physical instant of completed warm-up. Later stable/unknown excursions and
end-of-capture uncertainty remain open for review.

TPS burst 808.502-810.751s has smoothed rise 0.664V, rate 0.379V/s and 0.498s
hold. Raw interval TPS is 0.605 -> 1.230V, speed 24 -> 28 km/h. This is a
throttle-opening marker, not a claim that every opening is a distinct maneuver.
Engine-stop marker 1435.127-1437.127s brackets observed nonzero-to-zero RPM;
its two-second support range is not a separate operating segment.

## Review Priority

203 operating runs and 10 thermal runs yield 203 joint intervals; there are
83 TPS bursts and one engine-stop marker. Three verified stops total 32.0s.
Two mixed moving intervals are below four seconds, adjacent to uncertainty;
10 short unknown intervals should not be stretched into known states. Review
those exceptions and the slow steady interval before broad acceptance.

Full proposal IDs, boundaries, trend summaries and three-second operating /
30-second thermal neighborhoods are in `exports/ctx2_pilot_cases.json`.
