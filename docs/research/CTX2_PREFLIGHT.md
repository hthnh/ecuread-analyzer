# CTX2 Preflight

Audit date: 2026-10-09. Repository: `/home/hthnh/ecuread-analyzer`.

## Existing Studio

CTX1 lives exclusively in `app/context_annotation`, its templates, and its static
assets. It loads canonical telemetry through existing ingestion adapters, writes
atomic annotation sidecars, and validates bounds, overlaps, and source hashes.
CTX1.1 pins 57 entries in `data/context_annotation/curated_corpus.json`.
Folder discovery remains available. Coverage and freeze exports consume accepted
sidecars, so CTX2 proposals must use a separate store.

The existing `ride-20260920-1504.context.json` is a v1 human annotation covering
0-51.609 seconds. Its engine/throttle/dynamics/thermal labels must retain their
original meaning. Reading it, generating suggestions, and rerunning rules must
not rewrite it. V2 acceptance explicitly upgrades the document envelope while
preserving legacy segments with their v1 taxonomy. There is no automatic label
translation.

## RAW Availability

Correction from the CTX2.1 audit: the initial preflight overlooked complete
frames in `quality_flags.raw_hex` in five legacy canonical directories. These
embedded frames establish replay/slice relationships but expose ECT/battery
decoder-contract conflicts and do not establish original capture provenance.
They remain outside the unchanged CTX2 speed-resolution map. See
`CTX2_PILOT_QUALITY_REVIEW.md` and `exports/ctx2_provenance_reconciliation.json`.

The other canonical directories do not retain complete original frames.
Production v2 samples retain `candidate_signals.unknown_reserved_raw`, including
native byte 19, but this is insufficient to revalidate the original header and
checksum. Older samples use different candidate offsets. CTX2 therefore requires
an accessible full RAW frame before decoding candidate speed.

There are 12 accessible original RAW files: nine files in `real_data/real_run`
and three calibration `raw_frames.jsonl` files. Existing session IDs and
`data/evaluation/h2/evaluation_manifest.json` identify candidate provenance, but
do not provide verified sample range mappings. A read-only audit compared every
sample's six production signals and four reserved bytes against contiguous
original RAW ranges. Exactly 27 canonical entries match, uniquely; 30 do not.
The generated `data/context_annotation/raw_signal_sources.json` pins original
SHA-256, canonical SHA-256, sample offset/count, and alignment evidence. Runtime
resolution rechecks hashes, sample values, timestamp intervals, frame headers,
and checksums. An absent or changed original means unavailable speed.

The four accessible cases are `session_000002.jsonl`, `session_000004.jsonl`,
`session_000005.jsonl`, and `session_000007.jsonl`. Captures 002, 005, and 007
also exist as split canonical entries. Original file order must be respected;
part-relative timestamps must never align to the beginning of the full capture.

## Corpus Identity Caveat

The pinned manifest's `independent_captured_sessions: 57` is not established by
its previous directory/hash audit. Exact RAW matching reveals repeated original
captures under `calib-real-run-*` and `ride-*` aliases, with different slice
boundaries. At least seven entries are explicitly synthetic: three `esp-fake-*`
entries and four `ride-*` entries marked `explicit_demo_seed_generation` in
`data/calibration/session_labels.csv`. Different directory hashes do not prove
capture independence. CTX2 preserves the authorized 57-entry campaign and does
not infer an independent capture count from these derived entries. Research
reports must distinguish corpus entries from original captures; the remaining
origin audit is required before making independent-capture claims.

## Implementation Boundaries

Candidate speed is native Honda Table 17 byte 19, decoded after the existing
24-byte / FF-prefixed 29-byte normalization and frame validation. It is
rider-corroborated, not GPS-calibrated. No production decoder changes, MAP,
GPS invention, supervised models, analyzer verdicts, anomaly rules, or RCA
dependencies belong in CTX2. RPM-only evidence cannot establish movement or
stationarity. Missing speed, timing, or conflicting evidence must be visible.

All proposals remain pending until a researcher explicitly accepts them.
Acceptance records the reviewer and timestamp and retains original rule
version, evidence, and corrections. Events use a separate collection and do
not add annotation duration. Source and existing annotation hashes will be
checked again after corpus preview and real-session review verification.
