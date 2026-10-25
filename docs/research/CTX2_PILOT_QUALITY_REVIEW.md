# CTX2.1 Pilot Quality Review

Review date: 2026-10-09. Decision: **`provenance_blocked`**.

The proposal count is explained, not itself evidence of an error. Selected
boundaries mostly represent measurable changes, and speed-based abstention is
working. Large-scale acceptance/evaluation of the current 57-entry campaign is
not authorized: capture independence is unresolved, synthetic entries remain
pinned, and replayed legacy telemetry has decoder-contract conflicts. No
annotations were accepted, no dataset was frozen, and no production analyzer,
decoder, ML model or segmentation thresholds were changed.

This is an engineering desk review, not a human-reviewed timestamp truth set.
No segmentation accuracy, precision or recall estimate is claimed.

## Inputs and Reproduction

- Authoritative unchanged manifest: `data/context_annotation/curated_corpus.json`.
- Manifest SHA-256: `538bf38dfc395084f7f08cdd342d9bbf8ac10d757c298623ca8cb80cb6e1c61e`.
- All 57 existing suggestion documents, 5,035 proposals, `ctx2-rules-v1`.
- Config SHA-256: `fd67888efcb842876003fd3140c48c88af78fab2a0f84df985ab18f1e41a2a6b`.
- Four full original RAW captures are additional read-only pilot views, not
  four extra campaign entries. Their overlapping pinned slices are not counted
  as independent pilot cases.

Run from the repository root:

```bash
.venv/bin/python -m app.context_annotation.pilot_quality \
  --corpus-manifest data/context_annotation/curated_corpus.json \
  --suggestions-dir data/context_annotation/suggestions \
  --output-dir data/context_annotation/exports
```

This only writes audit/evidence exports. It does not regenerate campaign
proposals, modify telemetry, decide reviews or invoke freezing. Input hashes
are checked before/after; sources must match the manifest. Repeated runs have
the same measured content; generation timestamps change.

Artifacts:

- `data/context_annotation/exports/ctx2_proposal_quality.json`: all-entry quality
  audit, separate label axes, short-run IDs, oscillations and evidence checks.
- `data/context_annotation/exports/ctx2_provenance_reconciliation.json`: all 57
  classifications, parent hashes, offsets, correlation groups and quarantine.
- `data/context_annotation/exports/ctx2_pilot_cases.json`: selected proposals,
  original evidence, complete run boundaries and measured neighborhoods.
- `data/context_annotation/exports/ctx21_review_verification.json`: isolated
  correction/uncertainty browser verification, without accepting any proposal.

## What Creates 5,035

**2,449 joint operating/thermal intervals + 2,586 event markers = 5,035.**
The joint intervals contain two labels. Coalescing each axis independently
recovers 2,172 operating runs and 416 thermal runs; those two counts must not
be added to obtain the proposal count. Thermal intersections add 277 cuts over
the operating axis. A thermal cut need not correspond to a motion change.
All joint intervals are contiguous and nonoverlapping within each entry.

| Axis | Count | Min s | P10 s | Median s | P90 s | Max s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Joint intervals | 2449 | 0.1245 | 4.00 | 6.00 | 23.25 | 4415.50 |
| Operating runs | 2172 | 0.1245 | 4.00 | 5.75 | 14.98 | 4496.00 |
| Thermal runs | 416 | 0.2505 | 11.31 | 40.00 | 262.00 | 4415.50 |

Operating counts: engine-off 14; stationary-running 72; accelerating 419;
decelerating 439; steady 100; variable 974; unknown 154. Thermal counts:
warming 43; stable 174; unknown 199. Per-label duration distributions are
included in the JSON rather than conflating the two axes.

Events: throttle-burst **2,568**, engine-stop **11**, repeated-stop-go **7**,
engine-start **0**. A burst is a threshold-qualified opening, not necessarily
an independent maneuver. Events overlap duration labels and add no coverage.
Stored event evidence is checked against the declared rise/rate/hold, cycle
or RPM-transition requirements; that check is not an independent truth label.

Entry-weighted duration is 44,732.094s. Totals repeat content across aliases and
overlapping slices and must not be presented as independent recording hours.
Original RAW files shared by multiple IDs and the duplicate sample-file hashes
explain additional repeated proposal content.

## Short Runs and Oscillation

Counts use a 1-microsecond numerical tolerance around duration thresholds.
There are 147 joint intervals below four seconds, 67 below one second and
1,786 below ten seconds. After separating axes, operating has 128 below four
seconds: **107 unknown and 21 variable-moving**. Acceleration, deceleration,
steady and stationary runs all meet four seconds. The short variable exceptions
abut unknown/non-motion runs or capture edges: persistence cannot expand them
across an evidence boundary without inventing certainty. Thermal has 101 runs
below 20s, all unknown; no known thermal run violates its persistence minimum.

Screening definitions are explicit in the export: operating A-B-A with middle
<=8s and full triplet <=40s; thermal middle <=20s and full triplet <=120s.
Overlapping triplets and duplicated entries are deliberately reported, not
misrepresented as independent errors.

There are 836 operating A-B-A candidates. Their median RAW speed range is
21km/h (P90 38), so most are not one-byte noise. Only 13 have a range <=3km/h;
12 involve unknown/near-stop creeping. The remaining moving-variable / steady /
variable example at 841.874-853.875s in
`calib-real-run-r0024d0-c93a5fb4-part-01` spans 46-49km/h and merits review
of the steady-spread threshold. It does not establish corpus-wide excessive
fragmentation. Thermal has 12 A-B-A candidates, six within a 2C range, including
duplicate/replayed observations. These are stable/unknown transitions, not
rapid warming/stable flips.

The 4-6s moving subsegments and many TPS markers still create substantial review
work. Short mixed runs, threshold-borderline steady runs and short thermal
unknown bands remain targeted review items; no smoothing change was justified
solely by the count and none was applied.

## Coverage and Evidence

| Measure | Entry-weighted seconds | Fraction |
| --- | ---: | ---: |
| Proposed operating unknown | 27694.4095 | 61.912% |
| Proposed thermal unknown | 7615.6240 | 17.025% |
| Duration in speed-unavailable entries | 27394.3410 | 61.241% |

Thirty entries have unavailable speed. Their operating unknown duration is
27,340.8395s; some independently supported engine-off intervals remain.
Speed-available entries contain 353.5700s operating uncertainty. None of these
proposed unknown labels is an explicitly reviewed unknown annotation.

All **2,047 stationary/moving joint proposals** have verified, aligned original
RAW speed for every sample in their ranges. All stationary intervals satisfy
RAW speed <=1km/h and RPM >=400. No speed-unavailable entry receives moving
or stationary labels. Candidate speed is rider-corroborated, not GPS-calibrated;
1-2km/h creeping remains ambiguous. Available RPM/TPS/ECT samples are complete;
109,527 samples lack resolved speed. Missing-speed duration is not filled from
RPM or presumed route context.

There are zero declared RPM-zero/moving-speed conflicts, zero rejected speed
steps and no timestamp gaps in this proposal batch. These are narrow rule
checks, not proof that all evidence is sound. Five legacy sidecars contain
complete embedded RAW hex that preflight did not search. All of those frames
pass structural/checksum validation, but canonical values differ when decoded
under the production native contract:

- Both `esp-real-session_000018-*` and `000024-*` differ for battery and ECT
  on every sample (17,976 and 7,790 respectively).
- All three `esp-fake-*` differ for ECT on all 1,200 samples each; low-battery
  also differs for battery on 270 samples.

These are legacy byte-contract/demo-value conflicts, not justification for
silently replacing measured values or enabling speed in pending proposals.
The reconciliation redecodes embedded frames solely to establish lineage.
Legacy/demo entries stay quarantined until their decoding and origins are
explicitly reconciled. No production decoder behavior was altered.

## Provenance Reconciliation

| Entry classification | Count |
| --- | ---: |
| Independent real capture, full representative | 7 |
| Split capture | 22 |
| Alias | 4 |
| Synthetic/derived | 7 |
| Unresolved provenance | 17 |

The seven full representatives are three controlled calibration captures and
four complete real-run files. Another 16 split entries and four aliases map to
the accessible originals, giving **27 entries linked to 12 RAW capture groups**.
Twelve original file identities are not a corrected total for the entire corpus.
**Corrected independent-capture count: unresolved (`null`).**

Six additional splits match embedded RAW replay content: four `ride-20260913-*`
entries at sample offsets 0/4500/9000/13500 in `esp-real-session_000018-*`, and
two `ride-20260920-*` entries at 0/4500 in `esp-real-session_000024-*`. Complete
six-signal/four-reserved-byte sequences and relative timestamps agree within
5ms. Their original real-world recording origins remain unresolved, so the
parent replays and these six children are quarantined together.

There are no duplicate pinned directory/source hashes and all 57 still match.
However, **five pairs have byte-identical `samples.jsonl` hashes**: the four
calibration/ride alias pairs (000001/000003/000004/000006), and synthetic
`ride-20260904-1810` / `ride-20260918-0815`. Unique metadata-containing directory
hashes did not reveal this. All pairs and hashes are explicitly recorded.
Thirty-six entries have a verified content/capture relationship to another
entry. Synthetic siblings sharing embedded RAW are also grouped even when
their canonical values were altered. Unknown original parent IDs/offsets remain
null, never guessed from dates or consecutive session names.

The manifest is unchanged. Its historical `independent_captured_sessions: 57`
field is an obsolete independence claim, not an audited result. No corrected
manifest or new campaign membership was silently substituted.

### Evaluation Guard

The separate reconciliation assigns every entry a correlation group and explicit
eligibility: 27 may be considered only in capture-group-aware research splits;
30 synthetic/unresolved-origin entries are quarantined. This is provenance
eligibility only, not verified annotation truth or model-evaluation readiness.

Validate a future JSON `{ "train": ["session ID"], "test": ["session ID"] }`:

```bash
.venv/bin/python -m app.context_annotation.pilot_quality \
  --corpus-manifest data/context_annotation/curated_corpus.json \
  --validate-split path/to/proposed_split.json
```

The command fails for quarantined, unknown, repeated or cross-partition capture
IDs and for stale manifest/canonical/RAW/evidence hashes. Future context research
must use this guard and group IDs; older analyzer evaluation workflows are not
retroactively changed. The previously catalogued real-run files were already
used by an existing anomaly model, so these artifacts must not be claimed as
an untouched holdout for that model either.

## Four-Session Pilot

| Full original | Joint / operating / thermal runs | Verified stops / duration | TPS bursts | Main observation |
| --- | --- | --- | ---: | --- |
| 000002 | 203 / 203 / 10 | 3 / 32.0s | 83 | ECT 48 -> 87C in first 180s; measured warm-up trend. |
| 000004 | 110 / 108 / 9 | 7 / 116.5s | 63 | More stops than 007; thermal abstention despite complete ECT. |
| 000005 | 255 / 252 / 13 | 4 / 120.3s | 138 | ECT initially 88-90C; later local rise is not a cold-start claim. |
| 000007 | 216 / 215 / 7 | 3 / 51.7s | 64 | Mostly stable thermal state; localized stop-go still occurs. |

Concise boundary/evidence reviews:
[000002](ctx2_pilot/session_000002.md),
[000004](ctx2_pilot/session_000004.md),
[000005](ctx2_pilot/session_000005.md),
[000007](ctx2_pilot/session_000007.md).

Representative acceleration/deceleration intervals show large measured speed
changes; stops have near-zero RAW speed with visibly different surrounding
motion. In 005, the 726.3735s joint cut is a thermal transition while speed stays
zero: the operating stop continues to 742.1235s. Thermal uses centered 60s
windows and boundary snapping, so a warming boundary can precede the local rise.
These are approximate trend boundaries, not precise physical-state transitions.
Session-level urban/intercity/warm-up descriptions were not used as ground truth.

## Review Workflow

The existing frontend remains. Two small additions address sequential-review
effort: **Next pending** opens the next unreviewed proposal, zooms to its range
and shows its evidence; **Uncertain** persists a named review-history entry and
any correction without human confirmation or annotation coverage. Deferred
items remain separately filterable and visually distinct from accepted labels.
Batch range actions skip them; resolving one requires an explicit selection.

Accept/Correct/Reject, evidence inspection, reviewed/proposed lanes and overlap
guards were already present. Accepted annotations remain protected from batch
replacement. `unknown` is a semantic label; `uncertain` is review status, and
neither pending nor deferred proposals enters accepted coverage or freezing.

Isolated browser testing on real 004 telemetry verified next-pending navigation,
correction/uncertainty persistence after reload, visible measured evidence and
desktop/mobile framing. No proposal was accepted even in that browser test.
Canvas contains rendered signal pixels; no mobile overflow or browser errors.
The campaign manifest, all sources/proposals and existing human sidecar hashes
remain unchanged. Screenshots are in `exports/ctx21_browser_verification/`.

## Acceptance Criteria

| Criterion | Result |
| --- | --- |
| Meaningful boundaries | Supported for selected operating intervals; thermal-only cuts and centered-window approximation disclosed. Not a full human boundary approval. |
| No excessive noise fragmentation | Most reversals have substantial speed changes; small-amplitude and short mixed exceptions require targeted review, not count-based rejection. |
| Stationary/moving uses actual speed | Pass: 2047/2047 supported; zero stationary-constraint failures. |
| Consistent thermal labels | Broad trends supported; centered windows, short unknown bands and legacy ECT conflicts prevent blanket thermal approval. |
| Corrections survive reload | Pass in isolated browser and persistence tests; accepted-annotation protection retained. |
| Missing-speed abstention | Pass: zero unsupported stationary/moving labels. |
| Provenance disclosed and leakage prevented | Groups/quarantine and fail-closed split validator added; independent count still unresolved. Full-corpus gate remains blocked. |

Verification: 51 focused CTX tests passed (four acceptance/freeze tests deliberately
deselected); browser verification passed with zero accepted proposals and zero
freezes. New tests exercise coalescing, oscillation screening, split quarantine,
same-parent leakage rejection, correction/reload and uncertain status. Earlier
acceptance verification is not relabelled as new campaign review.

## Required Before Broad Acceptance

Resolve original capture identities for quarantined real/replay entries and
explicitly decide synthetic campaign membership. Reconcile legacy ECT/battery
contracts with original recording evidence; do not infer fixes from narratives.
Review the selected short/threshold-borderline intervals and centered thermal
boundaries. Only then propose an explicit manifest revision and rerun this gate.

Campaign coverage is unchanged: 56 unstarted, one partial, zero complete;
51.609s annotated, zero explicitly reviewed unknown seconds and 44,680.485s
truly unannotated. All 5,035 campaign proposals remain pending. The current
57-entry campaign is not approved for large-scale acceptance or context freeze.
