# ECU Context Annotation Studio

The Context Annotation Studio is an offline research utility for manually
annotating operating context over ECU telemetry sessions. It is not a diagnosis
workflow, not ground-truth fault labeling, and not part of the production
DriveSafe analyzer API.

## Current RAW Campaign And Macro Review (CTX2.4)

The active campaign is `ecuread-filtered-raw-18-v1`. Its explicit manifest is
`data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json`,
referencing the 18 originals in `real_data/fulldata_afterclean` directly. The
57-entry inventory below is historical: its working sessions were retired at
the operator's request, with a verified recovery archive outside the repository.
Historical inventories, attestation and research reports are not deleted.
Default launch follows the SHA-pinned `data/context_annotation/active_campaign.json`;
explicit `--input-dir` still selects exploratory folder mode.

```bash
.venv/bin/python -m app.context_annotation.web --port 8767 \
  --corpus-manifest data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json
```

Macro Episodes is the default human queue. Operating and thermal tabs have
independent decisions and progress. Detailed Intervals & Events retains the
entire original CTX2 queue and its separate progress. Event markers and detailed
transitions are optional timeline lanes; engine start/stop markers remain visible.
An episode expands into every referenced original interval, original proposal,
measured evidence, timestamp boundaries, events and transitions. The episode
dialog plots actual speed/RPM/TPS/ECT samples; each child can open the existing
detailed editor. Unavailable values and capture gaps are not interpolated.

`ctx24-macro-v1` is a separate deterministic review projection, not a change to
CTX2 taxonomy, production thresholds or its detailed rules:

- Only exactly contiguous intervals may join; gaps never join.
- Acceleration, deceleration and variable-motion intervals may join as a mixed
  `moving_episode`. A directly adjacent accelerating/decelerating reversal splits.
- Steady motion, stationary-running, engine-off and unknown are separate states.
  No interval in these states is deleted, shortened or absorbed into movement.
- Engine event timestamps and capture-gap endpoints are hard operating cuts.
- Thermal intervals coalesce only when their thermal label is identical and
  contiguous, independently of operating episodes and engine events.
- No macro minimum duration is used. A short legitimate stop remains a stop.
- Internal moving changes remain original intervals and optional transition
  markers; mixed episodes never assert steady motion. Whether this presentation
  hides an important real-world change is still a **pending human pilot check**.

Macro reviews live in campaign `macro_suggestions/*.macro.json`, separately from
`suggestions/*.suggestions.json` and accepted `annotations/*.context.json`.
Accept/Reject/Uncertain/Save correction requires a named, explicit reviewer action.
Finalized macro decisions cannot be overwritten through this interface.
Regeneration preserves corrections and decisions; obsolete reviewed identities
are retained visibly for reconciliation, not silently reused on different evidence.
Source/evidence mismatches lock review. Macro acceptance does not accept its
children, events or thermal labels, and does not write existing accepted sidecars.
It is not automatically ground truth and does not satisfy the existing detailed
annotation snapshot/freeze gate. No training or detector evaluation follows it.

To regenerate suggestions from all 18 pinned originals and export traceability,
coverage and before/after pilot metrics:

```bash
.venv/bin/python -m scripts.regenerate_context_macro_campaign
```

Run regeneration with other Studio writers quiescent. The command first creates
a hash-verified recovery archive outside the repository. It preserves RAW,
models, attestation, historical research and accepted annotations, evaluates the
unchanged CTX2 engine again, checks original proposal/evidence equality, and
rebuilds the macro projection. It never silently adds another source. A failure
stops the command with a nonzero status; per-file JSON writes are atomic, but the
whole multi-file campaign is not a single filesystem transaction.

Current automated audit: campaign `exports/ctx24_macro_quality.json`.
Engineering/browser/regression results: `exports/ctx24_engineering_verification.json`.
Pilot report: [CTX2.4 Human-Centered Review](CTX2_4_HUMAN_CENTERED_REVIEW.md).
Human timing, manual boundary quality, important-context-loss assessment and
large-scale acceptance approval remain explicitly pending. Browser timings and
isolated test decisions are not human review results. Review-history timers
measure visible dialog time through an action, not full end-to-end reviewer time.

## Scope

CTX1 exists to add human operating-context evidence to telemetry sessions before
later conditional analysis. The annotation dimensions describe circumstances
visible from the capture, such as engine state or throttle state. They do not
describe causes, faults, anomaly detector verdicts, or RCA conclusions.

The tool is isolated under `app/context_annotation/` and is launched as a
standalone localhost web app. It reuses existing raw ECU ingestion and canonical
telemetry models, including decoder provenance and signal names, but does not
modify Isolation Forest semantics, model harness registration,
diagnostic_evidence, RCA, historical evidence, or DriveSafe integration.

## Folder Mode

Folder mode is exploratory. Place source sessions under:

```text
data/context_annotation/input/
```

Supported inputs include:

- Native Honda 0x17 JSONL files with `raw_hex` records.
- Legacy RAW text/log files parsed by the existing RAW ECU parser.
- Canonical telemetry JSON files.
- Canonical telemetry directories containing `metadata.json` and
  `samples.jsonl`.
- Canonical `samples.jsonl` files.

Unsupported or malformed sessions remain visible in the browser as
`unsupported`, `parse_error`, or `missing_required_signals`.
Context review requires RPM and either TPS voltage or TPS RAW; battery voltage
is an optional display signal, not a prerequisite for CTX2 rules.

The studio never modifies files under `data/context_annotation/input/`.

Launch folder mode with:

```bash
python -m app.context_annotation.web \
  --input-dir data/context_annotation/input
```

## Manifest Mode

Manifest mode is the reproducible research workflow for the CTX1 annotation
campaign. It uses:

```text
data/context_annotation/curated_corpus.json
```

The curated manifest explicitly pins each intended source session by session ID,
source path, and source SHA-256. In manifest mode only listed sessions appear.
Missing files are shown as `source_missing`, SHA changes are shown as
`source_hash_mismatch`, and extra files in `data/telemetry` are ignored.

Launch manifest mode with:

```bash
python -m app.context_annotation.web \
  --corpus-manifest data/context_annotation/curated_corpus.json
```

The corpus audit that explains included and excluded sessions is written to:

```text
data/context_annotation/exports/corpus_audit.json
```

The CTX1.1 audit resolved the apparent 58-session folder discovery to 57
curated sessions plus one untracked generated telemetry directory:
`0cd6f2eb-0a4e-5ef6-9fad-dbd03b81d960`.

## Launch Command

```bash
python -m app.context_annotation.web
```

The default bind address is `127.0.0.1:8765`.

Optional paths:

```bash
python -m app.context_annotation.web \
  --input-dir data/context_annotation/input \
  --annotations-dir data/context_annotation/annotations \
  --exports-dir data/context_annotation/exports
```

Add `--corpus-manifest data/context_annotation/curated_corpus.json` to run the
same app over the locked source set.

## Annotation Taxonomy V2

CTX2 operating segments use two fields:

- `operating_context`: `engine_off`, `stationary_running`, `moving_accelerating`,
  `moving_decelerating`, `moving_steady`, `moving_variable`, `unknown`.
- `thermal_phase`: `warming`, `stable`, `unknown`.

Optional event markers are separate from operating segments: `throttle_burst`,
`repeated_stop_go`, `engine_start`, and `engine_stop`. Events may coincide with
operating segments or other event types; they contribute no annotation duration.
The UI's default segment selector uses v2. An existing legacy segment opens its
v1 selectors explicitly.

Route metadata is optional and human-provided: `route_type`, `route_notes`, and
`known_traffic_conditions`. Neither route categories nor traffic conditions are
auto-generated from sensor traces.

## Legacy Taxonomy

CTX1 v1 segment labels use independent dimensions:

- `engine`: `off`, `running`, `unknown`
- `throttle`: `closed`, `low`, `mid`, `high`, `unknown`
- `dynamics`: `steady`, `opening`, `closing`, `transition`, `unknown`
- `thermal`: `cold`, `warming`, `stable`, `unknown`

Session-level labels are optional:

- `capture_type`: `road`, `stationary`, `calibration`, `unknown`
- `start_thermal_state`: `cold`, `warm`, `unknown`
- `notes`: free text

Unknown is valid data when the researcher explicitly labels a range unknown.
Unannotated time remains unannotated and is exported as null context, not as
unknown.

## Annotation Schema

Sidecar files are written to:

```text
data/context_annotation/annotations/<source_id>.context.json
```

Legacy v1 example:

```json
{
  "schema_version": "context-annotation-v1",
  "session_id": "session_001",
  "source": {
    "filename": "session_001.jsonl",
    "sha256": "abc123...",
    "session_id": "session_001",
    "decoder_provenance": {
      "decoder_id": "honda_keihin_71_17",
      "decoder_version": "1.0.0"
    },
    "source_session_id": null,
    "derivation_type": null,
    "augmentation_id": null
  },
  "session_labels": {
    "capture_type": "road",
    "start_thermal_state": "unknown",
    "notes": ""
  },
  "segments": [
    {
      "id": "ctx_001",
      "start_s": 0.0,
      "end_s": 14.7,
      "labels": {
        "engine": "running",
        "throttle": "closed",
        "dynamics": "steady",
        "thermal": "warming"
      },
      "notes": ""
    }
  ],
  "created_at": "2026-10-09T00:00:00+00:00",
  "updated_at": "2026-10-09T00:00:00+00:00"
}
```

Decoded telemetry is not stored redundantly in annotation files.

A v2 sidecar uses `schema_version: context-annotation-v2`. Each new operating
segment has `taxonomy_version: context-taxonomy-v2` and a `labels` object with
`operating_context` and `thermal_phase`. Legacy segments retain their v1 labels
and `context-taxonomy-v1`; accepting v2 suggestions explicitly upgrades the
document envelope without translating those labels. Event records live in the
separate `events` array. Pending or rejected records cannot validate as accepted
annotation segments.

## Auto Suggestions and Review

The session page's Auto-label action runs deterministic signal processing over
captured timestamps, RPM, TPS, candidate speed, and ECT. Proposed/reviewed
operating and thermal lanes share chart elapsed-time coordinates, including
zoom and pan. The review table provides evidence inspection, boundary/context
correction, accept/reject, selection across pages, and range acceptance.
Range acceptance includes whole pending proposals within the selected bounds,
skipping conflicts with reviewed context. Confirmed segments are never replaced
by auto-generation or batch acceptance.

Speed uses native Table 17 byte 19 only after the existing frame normalization,
header/checksum checks, and original-source alignment. It is a
rider-corroborated candidate, not independently GPS-calibrated. A canonical
session without verified accessible original RAW receives no motion-specific
speed inference. RAW source mapping is pinned separately:

```bash
python -m app.context_annotation.preflight \
  --corpus-manifest data/context_annotation/curated_corpus.json \
  --output data/context_annotation/raw_signal_sources.json
```

The preflight audit found 27 of 57 entries with verified RAW ranges; 30 entries
abstain from motion-specific suggestions. The pinned entry set includes aliases,
split captures, and synthetic records. Its historical `independent_captured_sessions`
field is not a validated count of independent physical captures. See
`CTX2_PREFLIGHT.md` before making independence claims.

Preview the entire campaign without changing accepted annotations:

```bash
python -m app.context_annotation.auto_label \
  --corpus-manifest data/context_annotation/curated_corpus.json \
  --output data/context_annotation/suggestions/
```

Each `<source_id>.suggestions.json` contains proposed segments/events, original
rule/config/evidence, source and RAW hashes, timestamp integrity, review status,
and coverage/fragmentation metrics. `batch_preview.json` distinguishes processing
success/failure, unavailable speed, partial evidence, and pending human review.
Custom thresholds can be provided with `--thresholds thresholds.json`; the
complete effective configuration and its hash are recorded with proposals.

Suggestions do not count toward verified annotation coverage. Explicit
acceptance requires a reviewer name and records `origin: auto_suggested`,
`review_status: accepted`, `human_confirmation`, original rule/config/evidence,
and corrections in the accepted sidecar. Rejection stays in the suggestion
store. Reruns preserve reviewed decisions and confirmed annotations. Editing an
accepted auto-origin segment/event records a fresh named confirmation and retains
earlier confirmations in `confirmation_history`. The accepted sidecar is
authoritative if interruption occurs between annotation and review
status writes. Undo affects accepted annotation content; a previous review
decision remains visible in suggestion history.

The standalone server accepts `--suggestions-dir` and `--raw-source-map`, in
addition to the existing folder/manifest/annotation/export paths. Production
analyzer and decoder behavior is unchanged.

Rebuild the measured report after a batch preview:

```bash
python -m app.context_annotation.validation_report
```

See `CTX2_AUTO_LABEL_VALIDATION.md` for algorithms, thresholds, cross-session
measurements, missing-data behavior, and limits. Rider narratives are not
timestamp-level ground truth; no segmentation accuracy is claimed.

## Source Immutability

Each annotation stores the source SHA-256. If the source changes after
annotation, the browser displays `SOURCE CHANGED`, and save operations reject
the stale annotation rather than silently reusing it.

Annotation writes are atomic: the studio writes a temporary file, fsyncs it, and
replaces the sidecar file.

## Visualization

The session view renders synchronized elapsed-time charts for:

- RPM
- TPS voltage
- TPS raw
- Vehicle speed candidate when verified original RAW is accessible
- Battery voltage
- ECT when available
- IAT when available

Honda SH Mode scope has no MAP signal, so MAP is intentionally not shown.

Charts share an elapsed-time X axis, support hover inspection, range selection,
wheel zoom, Shift-drag pan, and individual signal show/hide.

## Progress

Progress is computed as:

```text
annotated duration / valid session duration
```

A session is complete only when the elapsed session duration is covered by
annotation segments. A session with one short segment is partial, not complete.

## Outputs

Dataset manifest:

```text
data/context_annotation/exports/context_dataset_manifest.json
```

Annotation campaign status:

```text
data/context_annotation/exports/annotation_campaign_status.json
```

The campaign status reports total curated sessions, unstarted/partial/complete
counts, total corpus duration, annotated duration, explicitly unknown duration,
and truly unannotated duration. Missing annotations do not count as unknown.

Context-aware canonical records:

```text
data/context_annotation/exports/context_canonical_records.jsonl
```

Unannotated context columns are null. Explicit unknown labels are exported as
`unknown`.

## Freeze

After every curated source session has been reviewed, freeze a research snapshot:

```bash
python -m app.context_annotation.freeze_dataset \
  --corpus-manifest data/context_annotation/curated_corpus.json \
  --output data/context_annotation/datasets/context-v1
```

The freeze writes an immutable-style snapshot containing the dataset ID
`ecu-context-v1`, source hashes, annotation hashes, taxonomy/schema version,
coverage stats, session-level labels, segment/event labels, and a creation timestamp.
It stores references and hashes rather than copying raw telemetry.
V1 and v2 taxonomy versions are retained explicitly; only reviewed sidecars
enter the snapshot, including accepted auto-origin provenance. Suggestions are
never treated as accepted labels merely because a preview exists.

By default the freeze refuses incomplete campaigns. For validation-only dry
runs, `--allow-incomplete` can produce a snapshot with
`freeze_complete: false`; final research freezes should not use that flag.

The snapshot includes a `content_fingerprint_sha256` computed from deterministic
content. Repeating a freeze over unchanged inputs produces the same content
fingerprint, even though the creation timestamp may differ.

## Fixed Source Before Augmentation

The source corpus is locked before augmentation so later derived sessions cannot
masquerade as independent captured sessions. The current campaign manifest pins
57 entries. CTX2 found aliases, split captures, and synthetic records; its
historical `independent_captured_sessions` field needs a separate provenance
correction before it can establish an independent-capture count.
CTX2.1 now supplies a separate all-entry reconciliation and quality gate in
`CTX2_PILOT_QUALITY_REVIEW.md`. Its current outcome is `provenance_blocked`;
57 remains the pinned entry count, not a verified independent-capture count.
`exports/ctx2_provenance_reconciliation.json` records aliases, slices, replay
lineage, synthetic records, correlation groups and quarantine without changing
the curated manifest. Future context evaluation must validate capture-group
splits with `python -m app.context_annotation.pilot_quality --validate-split ...`.
Unresolved/synthetic entries and cross-partition aliases fail that guard.

The review UI also supports Next pending and Uncertain. Uncertain is a deferred
review decision, not the semantic unknown label: it retains corrections/history,
adds no accepted annotation coverage and is skipped by batch range decisions.
Deferred proposals can be inspected, corrected and explicitly resolved by ID.

Future augmented records must carry provenance such as `source_session_id`,
`derivation_type`, and `augmentation_id`.

## Limitations

CTX2 suggestions require explicit human review. Candidate speed is not
GPS-calibrated, sensor context is not real-world route/traffic truth, and no
segmentation accuracy is claimed from rider narratives. The tool does not
display anomaly verdicts, generate synthetic data, or alter production analysis
or H2/H3/H6 evaluation datasets.

## Original RAW Campaign (CTX2.2R)

The new research campaign is separate from the historical 57-entry corpus:

```bash
python -m app.context_annotation.web \
  --corpus-manifest data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json \
  --port 8768
```

This `context-raw-corpus-v1` manifest pins exactly 18 supplied original files and
100,688 frames, with one uninterrupted annotation timeline per original. Its
annotations, suggestions and exports default to directories beside that manifest,
so launching it does not write into the old campaign. Folder mode and the old
manifest remain supported. Research speed uses original RAW byte 19 with source
hash provenance; it is rider-corroborated, not GPS-calibrated or a production ML
feature. Charts decimate for display only and retain the full timeline endpoints.

Import or verify the already-local handoff artifact idempotently with:

```bash
python -m app.context_annotation.raw_campaign \
  --archive ecuread_raw_18_corpus_bundle.zip \
  --local-input real_data/fulldata_afterclean \
  --generate-suggestions
```

Generated proposals remain pending; neither they nor deferred/uncertain reviews
count toward confirmed coverage. No old labels are automatically migrated.
The registry records verified independent physical captures as unknown rather
than equating 18 hashes with 18 physical runs. Proposed evaluation partitions
must pass `python -m app.context_annotation.raw_campaign --validate-split ...`;
unverified physical groups fail closed. Freeze rejects this unfrozen registry.

The old freeze command above remains specific to the old manifest. Fixing input
identity and capture correlation before any augmentation prevents aliases,
sub-slices and derived records from inflating the independent evidence count or
crossing evaluation partitions. See `CTX2_RAW_CORPUS_REBASE.md` for all-entry
lineage, excluded old entries, unchanged-source proof and full harness/browser
results. This rebase does not certify segmentation quality or accept annotations.

## Collector Attestation (CTX2.3)

The selected RAW registry now uses `context-raw-corpus-v2`. The original v1
registry remains byte-for-byte in `provenance/corpus_manifest.pre_ctx23.json`.
The exact 18 IDs and source SHA-256 hashes are unchanged. Collector confirmation
is recorded in `provenance/operator_attestation.v1.json`, with a separate
`capture_groups.v1.json` historical lineage overlay. The registry explicitly
reports `operator_attested_independent_captures: 18` and
`machine_verified_independent_captures: null`. The historical 57-entry campaign,
its independent-count field, annotations and research results are not rewritten.

Each selected original has one distinct operator-attested physical capture group.
All 43 technically linked historical originals/aliases/slices/replays inherit
their parent's group. Two decoder-conflicted replays remain evaluation-ineligible;
seven synthetic/demo and seven outside-selected-corpus entries remain excluded.
The original strict policy still refuses these unverified physical identities.
An explicit alternative validates grouping only, without evaluating any detector:

```bash
python -m app.context_annotation.raw_campaign \
  --campaign-dir data/context_annotation/campaigns/ecuread-filtered-raw-18-v1 \
  --evaluation-policy operator_attested_capture_groups_v1 \
  --validate-split proposed_split.json
```

`proposed_split.json` maps partition names to source session IDs. A capture group
may occur in only one partition, including its historical aliases and splits.
Unknown/unregistered derived records fail closed. Any future derived record must
use `attestation.inherited_group` with the exact parent SHA, retain its source
lineage, and be explicitly registered/validated before evaluation; new groups
must never be minted for augmentation. Synthetic or unresolved parents are
rejected. No derived records are created by CTX2.3. Every downstream result using
the opt-in policy must disclose reliance on collector attestation, not independent
capture-metadata verification. Omitting the policy flag retains the strict guard.

Only 016, 018 and 023 have confirmed collector trip metadata: road capture,
out-of-city, continuous long ride. This read-only metadata is exposed separately
from operating labels in the Studio. No GPS, traffic, precise location, or
time-specific labels are inferred. Other sessions retain unknown collector trip
metadata. The five review priorities are 002, 004, 005, 007 and 018; their priority
descriptions are not segment ground truth.

The existing review controls include evidence, editable boundaries, Next pending,
Accept, Reject and Uncertain. The Kind filter separates operating segments,
events and throttle bursts; Next pending and range acceptance respect that filter.
Accepted labels remain in authoritative sidecars and cannot be edited through
the pending-suggestion route. Explicit accepted-label edits require a reviewer;
regeneration preserves human decisions. Named manual labels also retain review
provenance. No automatic suggestion acceptance occurs.

Generate the read-only pilot effort audit at any stage:

```bash
python -m app.context_annotation.review_effort \
  --corpus-manifest data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json
```

It writes `exports/ctx23_review_effort.json`: original joint-proposal count,
coalesced operating runs/median duration, events, review outcomes, boundary
changes and measured A-B-A oscillations. Named dialog review actions record
visible elapsed time (hidden-tab/network waits excluded, capped at one hour per
action). Batch/direct API actions without timing remain unmeasured; batch times
are deduplicated by action ID. This is a self-reported elapsed-time proxy, not
verified attention. No human review has been recorded yet, so manual time is
`null`, never a fabricated zero. Isolated browser checks do not enter this audit.

## Annotation-Only Snapshot

The original `freeze_dataset` command and strict old-campaign semantics remain
unchanged. For this attested RAW campaign, the separate annotation-only command is:

```bash
python -m app.context_annotation.annotation_snapshot \
  --corpus-manifest data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json \
  --output data/context_annotation/datasets/ecuread-filtered-raw-18-v1-annotations-v1
```

It refuses to create output until all 18 sessions have full accepted segment
coverage, no pending or uncertain segment/event suggestions, and explicit named
human review provenance. Rejected proposals add no coverage; accepted explicit
`unknown` is allowed and reported separately from absent annotations. Uncertainty
is a review state, not an unknown context label. There is no incomplete override.

The exclusive-create snapshot stores annotations and review decisions, their
hashes, source references/hashes, decoder versions, context schema/taxonomy/rule
versions, collector attestation, physical groups and coverage. RAW is not copied.
Existing output is never overwritten. `content_sha256` excludes the creation
timestamp; unchanged inputs have a stable content identity. Fixed timestamps
produce byte-identical snapshot content. This is an annotation research artifact,
not detector accuracy evidence, a new model, or an automatic training trigger.
No snapshot has been created for the currently unreviewed campaign.

See `CTX2_3_CAPTURE_ATTESTATION_REVIEW.md` for verification, limits and pilot effort.
# Current Active Corpus

The legacy 57 sessions were deleted from the working telemetry/session stores
and their annotation/suggestion sidecars removed at the operator's request on
2026-10-11. A verified recovery archive is outside the repository under
`/home/hthnh/ecuread-analyzer-backups/`. Historical reports and the old
`curated_corpus.json` inventory remain as provenance records, not active data.

`data/context_annotation/active_campaign.json` now selects the attested 18-source
campaign. Launching the Studio without an explicit input/manifest uses that
hash-pinned active campaign. An explicit `--input-dir` still selects folder mode.
The active manifest references the original files directly in
`real_data/fulldata_afterclean`, without creating slices or duplicate sessions.

```bash
python -m app.context_annotation.web --port 8767
```

Regeneration produced 3,084 operating proposals and 1,559 events across all
100,688 original samples. Suggestions are pending, not accepted annotations.
See `RAW_CORPUS_RESET.md` and the campaign's `exports/raw_reset_autolabel.json`.
Sections below describing the old 57-entry campaign are historical workflows;
its deleted sources are no longer available for review or freeze.

## CTX2.5 Operator-Approved Campaign

The earlier pending/unreviewed status descriptions are historical. The collector
has now explicitly endorsed the current 18-source suggestions in bulk, without
claiming individual inspection of every interval. All 5,833 fine/macro/event
items are accepted with distinct provenance; existing human actions are preserved.
Completed sessions open the accepted macro queue, whose bulk entries display
`Bulk endorsed`. Their original intervals, evidence and endorsement record remain
inspectable. Thermal review stays independent. Auto-label generation never
implicitly performs endorsement or overwrites accepted annotations.

The offline `app.context_annotation.bulk_endorsement` command requires explicit
`--operator-authorized`, pins exact approved file hashes, stages recoverable
before/after sidecars, and supports idempotent retry. Stop Studio writers first.
Rejected, uncertain, corrected and overlapping human decisions are preserved and
reported, not overridden. Bulk acceptance does not generate fake named interval
confirmations or measured review times.

The unchanged `annotation_snapshot` gate currently blocks these bulk records.
The authoritative **non-frozen** annotation-only handoff is
`data/context_annotation/datasets/ecuread-filtered-raw-18-v1-approved-v2/`.
Its manifest hashes annotations, original proposals, macro links, approval,
attestation, coverage and deterministic canonical-sample/50-sample-window mappings.
Unknown, unannotated and boundary-crossing windows remain distinct. The initial
v1 export is superseded because named batch actions must not be classified as
individual inspection. Only use the authoritative version named in the campaign's
`exports/ctx25_export_publication.json`; do not combine versions as new sessions.

See `CTX2_5_CONTEXT_DATASET_FINALIZATION.md` for counts, provenance coverage,
freeze limitations, reproducibility checks and commands. Bulk plausibility
endorsement does not establish segmentation accuracy or detector ground truth.

