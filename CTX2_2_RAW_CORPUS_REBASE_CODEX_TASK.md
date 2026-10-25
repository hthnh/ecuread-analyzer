# CTX2.2R — Rebase the Context Campaign on 18 Original RAW Files & Verify Long-Session Harness

Repository: `/home/hthnh/ecuread-analyzer`

Input: `ecuread_raw_18_corpus_bundle.zip` provided by researcher. The archive is a **handoff artifact**, not an automatically mounted path in this repository. Ask researcher to copy/extract it to a local import staging directory before processing.

## Goal

Replace the mixed 57-entry **annotation campaign source** with a new explicitly versioned RAW-first campaign based on the user's 18 selected files, **after provenance reconciliation**. Do not overwrite the old 57-entry manifest, its suggestions, annotations, model artifacts, or evaluation outputs. The original Model Harness must be smoke-tested end-to-end on full RAW sessions longer than 5,000 frames; do not assume a passing annotation render proves harness compatibility.

## Independently measured input acceptance checks

- 18 files (`session_000001` to `000007`, `000014` to `000024`; gaps are intentional as supplied).
- 100,688 valid native 24-byte Honda Table 17 RAW frames, no invalid checksum/header, no backwards time and no timestamp gap >1s.
- Seven files over 5,000 frames; three over 10,000: 016=17,965, 018=17,976, 023=17,539.
- Speed candidate native `RAW[19]` (0-based) appears in all 18 source files, with rider corroboration but no independent GPS calibration.
- Distinct file SHA256s and no observed contiguous cross-file overlap of 16 complete RAW frames. This is NOT proof of 18 independent physical capture runs.
- Original 001–007 hashes match the historical previously-linked original RAW captures.

Compare everything against the archive manifest and recompute digests locally. Reject source hash mismatches.

## Work

1. **Archive import and read-only baseline:** Import sources into a dedicated research data path. Do not edit source RAW. Report old vs new provenance, exact hash checks, unique files, invalid frames, and time continuity. Keep filenames and source identities unchanged. Do not fill absent 008–013.

2. **Reconcile the existing 57 entries:** Reuse CTX2.1 lineage. Match old canonical samples or `quality_flags.raw_hex` to the 18 original RAW captures using multiple synchronized signals, raw sequences, and timestamps/offsets. Distinguish full, alias, split, synthetic/demo, replay, and unresolved. Do not infer lineage solely from length, date string, file name, or similarity. Explicitly investigate the `esp-real-session_000018-*` and `000024-*` legacy ECT/battery decoder conflicts using original native raw; record conversion-contract mismatch without silently modifying legacy canonical values or detector outputs. Quarantined entries remain quarantined unless actual source linkage has been proved.

3. **New dataset manifest:** Generate a *separate* `ecuread-filtered-raw-18-v1` input/capture registry with hashes, counts, source origins and group identities. Treat number of verified independent physical captures as unknown until evidence supports an exact number. Record user attestation separately from technical verification. Require capture-group-aware train/test split; no synthetic/unknown leaks. Keep the old 57-entry manifest and prior research reproducible.

4. **CTX2 integration:** Use the original 18 RAW sessions as new annotation views, maintain a single uninterrupted timeline per original capture, decode speed with the already approved research-only 24-byte mapping, and show RPM/TPS/speed/ECT. Do not create a 5,000-frame split, auto-accept labels, or use detector outputs to seed context. Do not merge or migrate prior human annotations unless exact source+offset+decoder provenance is verified; preserve existing accepted annotations and review history.

5. **Long-session Model Harness compatibility smoke:** On **016 (17,965), 018 (17,976), 023 (17,539)** and at least **002/005/007**, exercise actual RAW ingestion → canonical validation → windows/features → Isolation Forest inference → contextual battery shadow → aggregation → RCA v2 → history → persisted `diagnostic_evidence`, subject to applicability. Record input/canonical samples, duration, windows, scored/skipped windows, any truncation/batching/capping, run time, peak memory if measurable, error states, and resulting persistence. Do not equate bounded display events with truncated inference; if events are capped in the API, separately verify all windows were processed. Do not train/refit models or tune thresholds. A skip due to feature incompatibility is an explicit failure/limitation for the smoke and must not be reported as a PASS.

6. **End-to-end regression:** Test reading/viewing/auto-suggestion/review/reload on at least one >10,000-frame source. Repeated import must be idempotent. Verify originals and old manifest hashes unchanged. Reviewable pending proposals do not count toward confirmed annotation coverage.

## Deliverables

- 18-input SHA validation and source-integrity report.
- Old 57-entry to new 18-RAW lineage/migration candidate table, with unknowns left unknown.
- New versioned research-only manifest and launch command.
- End-to-end long-session Harness smoke measurements and proof of no silent truncation.
- CTX2 performance + read-only/source invariance verification.
- Tests and limitations.

Decision values:
- `raw_corpus_rebased_and_harness_verified` only when 18 inputs validated, provenance mapping documented, new annotation views work, and the long-session **full harness** smoke passes.
- `raw_corpus_rebased_harness_pending` when new annotation campaign works but full harness does not have verified end-to-end results.
- `provenance_partial` if source lineage remains unresolved; explicitly list which entries remain excluded.
- `blocked` on checksum/hash/corruption or data-integrity failure.

No changes to DriveSafe production, production model verdict, model training corpus, or existing H2–H9 results without separate user approval.
