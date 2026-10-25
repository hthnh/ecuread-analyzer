# CTX2.4 Human-Centered Context Review

Campaign: `ecuread-filtered-raw-18-v1`. Date: 2026-10-11.

## Readiness Gates

**Engineering: ready for operator pilot.** All 322 regression tests passed
(0 failures/errors/skips); isolated browser checks passed for all five pilots at
desktop and mobile sizes. The verification record is campaign
`exports/ctx24_engineering_verification.json`.
**Human validation: pending.** The operator will personally review 002, 004,
005, 007 and 018 before approving large-scale labeling. No timed human review,
manual boundary-quality assessment or real-world context-loss assessment has
been performed or fabricated by this implementation. Final decision remains
`pending_human_validation`, not `macro_review_pilot_ready`.

## Corpus Regeneration

The old 57 working sessions were already retired under the prior explicit
deletion request. This run regenerated the 18 SHA-pinned original files directly
from `real_data/fulldata_afterclean`: **100,688 frames / 100,688 canonical samples**.
Session 018 remains one continuous session with 17,976 samples and 4,496.001s
duration. No truncation, duplicate source or artificial capture break is added.
All **3,084 detailed joint operating/thermal proposals and 1,559 event markers**
remain unchanged, including IDs, boundaries, original proposals and evidence.
Only suggestion generation timestamps and separate macro artifacts are refreshed.

RAW, model artifacts, existing thresholds, original CTX2 rule implementation,
attestation, historical research and accepted annotations are unchanged. The
regeneration export records 439 protected-file checks and an external recovery
archive with per-file hashes. No campaign annotation is automatically accepted.

## Deterministic Projection

Rule version: `ctx24-macro-v1`; storage schema: `context-macro-review-v1`.
The [Studio guide](CONTEXT_ANNOTATION_STUDIO.md#current-raw-campaign-and-macro-review-ctx24)
documents exact grouping and review semantics. Related non-steady moving states
join only across contiguous boundaries. Stationary, unknown, engine-off, steady
motion, directly adjacent trend reversals, engine events and capture gaps stay
as boundaries. There is no minimum-duration deletion rule. Thermal review is an
independent coalesced axis. The macro `moving_episode` presentation label is not
injected into the existing CTX2 taxonomy or canonical annotation sidecars.

Mixed movement is not asserted to be steady or uniform. Every internal fine
change remains expandable with its original label/evidence; event and detailed
transition lanes are optional, not mandatory default queue rows. Engine markers
remain visible. The default operating queue contains 941 episodes across all 18
files; the independent thermal queue contains 249 intervals. Original detailed
and event review remains available with separate progress.

## Five-Session Comparison

These are **review-row counts**, not measured human decisions or timing savings.
The old default combined joint intervals and events; the new default is operating
macros. The final column includes independent thermal decisions but excludes
optional event decisions. Those events remain pending and inspectable.

| Session | Detailed intervals | Event markers retained | Old default rows | Operating macros | Thermal | New operating + thermal | Stops before/after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 002 | 203 | 84 | 287 | 45 | 10 | 55 | 3 / 3 |
| 004 | 110 | 64 | 174 | 29 | 9 | 38 | 7 / 7 |
| 005 | 255 | 138 | 393 | 45 | 13 | 58 | 4 / 4 |
| 007 | 216 | 65 | 281 | 45 | 7 | 52 | 3 / 3 |
| 018 | 534 | 312 | 846 | 208 | 17 | 225 | 7 / 7 |

Pilot totals: 1,981 old default rows versus 372 default operating macro rows;
428 including separate thermal review. This is a **78.4% queue-row reduction**
when comparing the old default with operating + thermal, with optional-event
scope explicitly excluded. Operating-only comparison after removing old thermal
intersections: 1,307 runs to 372 macros (**71.5% reduction**). No claim is made
that total review time or all possible detailed review decisions fell by these
percentages. Event review was deferred, not completed.

Every pilot retains 100% of its original proposed interval coverage, all original
child IDs and events, and exact stationary/unknown/engine-off/steady boundaries.
Boundary lineage checks are automated: every macro boundary is an original
proposal boundary or an explicit engine/gap cut. Nearest measured samples and
source timestamps are exposed, and distance-to-sample is exported; that is not a
manual accuracy or real-world ground-truth result. Fine rules may already abstain
on brief or conflicting states; this layer does not relabel those unknowns.

## Human Pilot Pending

For **each** of 002, 004, 005, 007 and 018:

- Actual before/after reviewer time: **pending / null**, never browser runtime.
- Manually inspected boundary accuracy: **pending**.
- Important behavior hidden by mixed grouping: **pending human assessment**.
- New macro campaign decisions: initially **0**. Pre-existing human-confirmed
  detailed annotations for 001 and 002 are retained, not reset. Those prior
  acceptances are not evidence that the CTX2.4 human pilot gates have passed.
- Large-scale labeling approval: **pending operator feedback**.

Prior session descriptions are prioritization hints, not precise ground truth.
Operating context remains distinct from the operator-attested trip metadata.
Only 016/018/023 have confirmed out-of-city continuous-ride metadata. Capture
independence remains operator-attested (18), not machine-verified (null).

## Automated Verification

Machine-readable exports bind verification to current source/proposal hashes:

- `exports/ctx24_macro_quality.json`: all-18 regeneration, full sample counts,
  exact original-proposal equality, per-axis traceability/coverage, preserved
  stationary/unknown/engine-off/steady intervals and pilot count comparisons.
- `exports/ctx24_browser_verification.json`: desktop/mobile, expanded micro
  intervals and four measured signal plots, optional markers, independent thermal
  progress, correction/accept/reject/uncertain, reload/rerun preservation and
  finalized-decision guards. All test actions use temporary sidecars only.
- `exports/ctx24_engineering_verification.json`: final regression counts and
  protected-file verification. No automated test decision is a campaign label.

Research-only local Studio, single process/reviewer. Regeneration is not an
all-campaign filesystem transaction; quiesce other writers first. Existing strict
snapshot requirements are unchanged: completing macro review does not finalize
detailed events or authorize dataset freezing, model training or accuracy evaluation.
