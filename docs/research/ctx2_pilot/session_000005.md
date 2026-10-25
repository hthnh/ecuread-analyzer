# session_000005: Thermally Stable Start Pilot

Desk review, 2026-10-09. No annotation accepted. The description is not a
timestamp label. Times are seconds from the full original RAW start.
Source: `real_data/real_run/session_000005.jsonl`, SHA-256
`260b706c68fc4807cf23ddb8977059ca3523d77c105d7f27093354b9d7072be4`.
Rules/config: `ctx2-rules-v1` /
`fd67888efcb842876003fd3140c48c88af78fab2a0f84df985ab18f1e41a2a6b`.

## Selected Intervals

Speed/TPS endpoints summarize the first/last few raw samples; RPM is the median.

| Proposed interval s | Label | RAW speed km/h | RPM | Measured TPS V | Review finding |
| --- | --- | --- | ---: | --- | --- |
| 194.123-210.374 | accelerating | 6 -> 37.5; range 5-39 | 4413 | 0.996 -> 1.846 | Clear sustained speed increase. |
| 538.124-550.874 | decelerating | 53 -> 28; range 28-55 | 4783 | 0.488 -> 0.938 | Speed decreases despite non-monotonic TPS; no braking inference. |
| 554.874-566.124 | steady | 27.5 -> 27; range 24-28 | 4152 | 0.811 -> 0.967 | Local stable speed, not constant throttle. |
| 1386.124-1430.874 | variable | 28 -> 22; range 19-31 | 4181 | 0.918 -> 0.850 | Long mixed interval avoids fitting one global trend. |
| 696.372-726.374 | stationary-running / warming | range 0-1 | 1403 | 0.488 throughout | Valid stop evidence; end is a thermal cut, not departure. |
| 1954.123-1960.874 | unknown | range 0-2 | 1357 | 0.488 -> 1.035 | Near-zero/short persistence ambiguity must stay visible. |

The complete operating stop is 696.3725-742.1235s. Its joint proposal is cut
at 726.3735s (warming -> stable); the stable thermal run continues to 748.373s,
past the stop. At 726.3735s speed stays zero on both sides: do not interpret a joint
boundary as a motion change. At the actual stop start, the preceding three
seconds range 1-11 km/h, and the following three range 0-1.

## Thermal and Events

First 180s ECT is 88-90C, median 89C; there is no large warm-up rise. Unknown
0-14.624s is followed by stable 14.624-298.6235s. This is initial window
uncertainty, not a forced cold-start label. Stable totals 1651.4/1964.0s.

Later warming is proposed at 690.374-726.3735s. Before its start, ECT is 90C;
the following 30s rise 90 -> 93C. Around its end, the preceding 30s rise
90 -> 93C and the following 30s fall 93 -> 92C. The rise is real, but the
warming boundary precedes it because the 60s rule window is centered. This is
a local thermal rise during a hot session, not evidence of a cold engine or
precise completion of warm-up.

TPS burst 793.498-794.999s has smoothed rise 0.674V, rate 0.672V/s and 0.499s
hold. RAW TPS changes 0.664 -> 1.162V while speed changes 42 -> 43 km/h.
This should remain an opening marker, not a separate acceleration claim.

## Review Priority

252 operating runs and 13 thermal runs create 255 joint intervals, plus 138
TPS bursts. Four verified stops total 120.3s. Three mixed moving intervals
remain below four seconds; nine short unknown intervals preserve ambiguity.
Review the shared stop/thermal boundary, initial unknown window and high event
density. Bursts are threshold-qualified openings, not 138 independent maneuvers.

Full proposal IDs and measured boundary neighborhoods are in
`exports/ctx2_pilot_cases.json`.
