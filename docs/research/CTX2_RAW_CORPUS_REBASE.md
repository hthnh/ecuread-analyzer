# CTX2.2R Original RAW Corpus Rebase

Date: 2026-10-11. Decision: `raw_corpus_rebased_and_harness_verified`.
This decision establishes the pinned annotation inputs and complete harness
compatibility, **not** physical capture independence, ground-truth labels,
segmentation-quality approval, or a frozen evaluation dataset.

## Separate Campaign

Dataset/input registry: `ecuread-filtered-raw-18-v1`.

```text
data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json
```

The archive and extracted staging directory were already present locally.
Imported originals retain their exact supplied filenames under the campaign's
`raw/` directory and are made read-only. Manifest, suggestions, annotations and
exports are separate from the historical 57-entry campaign. Nothing absent in
008-013 is fabricated, inferred, or automatically discovered. Importing identical
sources again reuses the same files and byte-identical manifest; a changed staged,
archived or previously imported hash fails without overwriting that source.

```bash
.venv/bin/python -m app.context_annotation.raw_campaign \
  --archive ecuread_raw_18_corpus_bundle.zip \
  --local-input real_data/fulldata_afterclean \
  --generate-suggestions

.venv/bin/python -m app.context_annotation.web \
  --corpus-manifest data/context_annotation/campaigns/ecuread-filtered-raw-18-v1/corpus_manifest.json \
  --port 8768
```

The RAW manifest schema automatically places sidecars inside its own campaign.
The old `data/context_annotation/curated_corpus.json` and its launch command
continue to work. Generic `--input-dir` mode is unchanged.

## Input Integrity

`exports/input_integrity.json` recomputes SHA-256, bytes, counts, complete native
headers/checksums and every timestamp against archive metadata and local staging.

- Exactly **18 distinct pinned files**, 001-007 and 014-024.
- **100,688 valid native24 frames**, **25,189.348 s** summed duration.
- Zero invalid checksum/header/decoded frames; strictly increasing timestamps;
  no interval over 1 s and no backwards timestamps.
- Seven sources over 5,000 frames; three over 10,000: 016=17,965,
  018=17,976, 023=17,539.
- No duplicate source SHA and no exact contiguous cross-file block of 16 complete
  RAW frames, checking every possible start offset and verifying bytes on hits.
- Original 001-007 SHA values match `real_data/real_run` historical originals.
- Research speed candidate is present in all 100,688 frames, mapped by the
  already-approved research-only native byte 19 contract. Rider corroboration is
  recorded separately; there is no independent GPS calibration.

These observations establish input integrity, not 18 independent physical runs.
Both `independent_captured_sessions` and verified independent physical captures
are **null/unknown**. User attestation is separate from technical evidence. Each
input has a SHA-based source correlation group, but no verified physical capture
group. All 18 remain `evaluation_eligible: false` until those groups are resolved.

## Old-To-New Reconciliation

`exports/old_to_raw_lineage.json` and `.csv` describe all 57 old entries, parent
source IDs/hashes, sample and time offsets, decoder contracts, correlation groups,
old annotation hashes and migration candidacy. This reuses the pinned CTX2.1
lineage and validates its manifest digest before matching.

Matching requires the entire contiguous sequence of six synchronized decoded
signals plus four reserved bytes and all relative timestamps within 5 ms. When
old samples contain RAW, every normalized 24-byte frame must match the original;
embedded RAW is independently redecoded to expose legacy conversion differences.
Names, lengths and dates alone never establish lineage.

| Classification | Old entries |
| --- | ---: |
| Full original-linked view | 11 |
| Split original-linked view | 26 |
| Alias | 4 |
| Embedded-RAW replay with legacy decoder conflict | 2 |
| Outside the selected 18-file corpus | 7 |
| Synthetic/demo | 7 |
| Unresolved old source linkage | 0 |

**43** old entries are technically linked to one selected original; **14** remain
excluded. Physical capture independence remains unresolved even for linked files.
Excluded out-of-corpus entries:

```text
calib-20260812-152832-2a54f0
calib-20260814-152603-8fd9ed
calib-20260814-153329-812a3f
calib-real-run-r0024d0-c93a5fb4-part-01
calib-real-run-r0024d0-c93a5fb4-part-02
calib-real-run-r1032c0-849aa7f8-part-01
calib-real-run-r1032c0-849aa7f8-part-02
```

Excluded synthetic/demo entries:

```text
esp-fake-low-battery-20260920144735
esp-fake-overheat-20260920144735
esp-fake-rpm-spike-20260920144735
ride-20260904-1810
ride-20260909-0725
ride-20260914-1935
ride-20260918-0815
```

`esp-real-session_000018-20260920144735` matches all 17,976 original frames and
timestamps at offset zero; legacy battery/ECT differ on all 17,976 samples.
`esp-real-session_000024-20260920144735` similarly matches all 7,790 original
frames, with battery/ECT differences on all 7,790 samples. This proves replay
linkage and a conversion-contract mismatch, not a new physical capture. Both old
canonical datasets and detector outputs remain unchanged, and neither is an
annotation migration candidate. RAW1 corrects future native file ingestion using
the existing native decoder; it does not rewrite these historical records.

Only `ride-20260920-1504` currently qualifies as a source/offset/decoder-equivalent
annotation migration **candidate**. No annotation or review history was migrated.
No prior quarantine is implicitly lifted for evaluation.

## Evaluation Guard

```bash
.venv/bin/python -m app.context_annotation.raw_campaign \
  --validate-split path/to/proposed-split.json
```

The JSON maps partition names to original source IDs. Validation fails closed
for unverified physical capture groups, ineligible/unknown/repeated sources,
empty partitions overall, and groups crossing partitions. The current registry
therefore rejects any nonempty train/test assignment. Different file hashes or
multiple old aliases cannot silently become independent evaluation observations.
Freezing this registry is explicitly blocked; no dataset snapshot was created.

## Full Harness And Review

All 18 originals passed native file ingestion, canonical validation, complete
50/10 window generation, real existing Isolation Forest and Contextual Battery
shadow inference, Evidence Aggregation, RCA v2, Historical Evidence and persisted
`diagnostic_evidence`. Required 002/005/007 and 016/018/023 are included. There
were **9,989 persisted/IF-scored windows** and no incompatible-model skips.
Applicability-based contextual skips are counted separately. Detailed per-source
hashes, time/RSS, all scored/skipped counts, persistence hashes and parity proof
are in `data/research/raw1_upload/summary.json` and `session_*.json`.
See [RAW1_NATIVE_V2_UPLOAD_COMPATIBILITY.md](RAW1_NATIVE_V2_UPLOAD_COMPATIBILITY.md)
for route fixes, 018's 17,976/1,793 regression, resource tests and deployment limits.

CTX2 uses uninterrupted original timelines and native canonical signals. All
18 suggestion files were generated from measured telemetry only: **3,084
operating segments and 1,559 event markers**, all **4,643 pending**. Nothing was
auto-accepted, copied from detector verdicts, or declared accurate from rider
narratives. Existing CTX2 taxonomy, rules and thresholds are unchanged.

`exports/browser_verification.json` and `exports/browser/*.png` verify original
018 in an actual browser, using temporary sidecars only. It checks full sample
count/duration and speed provenance, selected measured evidence, correction plus
uncertainty, next pending, reload and regeneration persistence, no accepted
coverage, and desktop/mobile rendering. Local timings: initial view **11.21 s**,
reload **7.01 s**, full-session auto-suggestion **6.30 s**. No console errors or
mobile overflow; speed canvas was nonblank. Timings are local observations with
other local verification work running, not interactive latency guarantees.

Charts display at most 1,200 points per signal; original endpoints are preserved
after correcting a display-only decimation bug. The API reports all 17,976 samples
and the complete 4,496.001 s timeline. Display decimation/pagination is distinct
from ingestion, rule evaluation, and persisted analysis coverage.

`exports/annotation_campaign_status.json`: **18 unstarted, zero partial/complete;
zero annotated duration; zero explicitly unknown duration; 25,189.348 s truly
unannotated**. Pending or uncertain proposals are not confirmed labels. All 18
sessions still require human review; no annotation was accepted or frozen.

## Invariance And Tests

`exports/read_only_baseline.json` pins 433 pre-existing protected files;
`exports/source_invariance.json` confirms every hash unchanged. This includes
original RAW, archive, old 57-entry manifest/sources/suggestions/annotations,
models, calibration labels and H2-H9 reference outputs. The browser separately
hash-verifies the old accepted sidecar and new real pending sidecar unchanged.

Full pytest: **287 passed, zero failed**. Added tests cover idempotent/hash-pinned
archive import, unlisted/duplicate ZIP members, stage/import corruption,
manifest count/identity errors, unknown independence, isolated annotation storage,
freeze rejection, fail-closed physical-group splits and full timeline endpoints.
RAW1 includes separate real-model all-18 and bounded HTTP resource probes.
No production model artifact, feature schema, threshold, DriveSafe behavior or
existing research evaluation output was changed.
