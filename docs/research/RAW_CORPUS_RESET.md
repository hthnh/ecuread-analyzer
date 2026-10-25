# Original RAW Corpus Reset

Operator requested deletion of all 57 old sessions and rerunning auto-labeling
from `/home/hthnh/ecuread-analyzer/real_data/fulldata_afterclean` on 2026-10-11.

## Result

- Deleted the 57 explicitly inventoried `data/telemetry/<id>` directories,
  their 57 `data/entities/sessions/<id>.json` records, 57 suggestion sidecars,
  the one old annotation sidecar, and the obsolete legacy batch summary.
- Kept a SHA-verified recovery archive outside the repository in
  `/home/hthnh/ecuread-analyzer-backups/`; its exact path and all backed-up file
  hashes are in `provenance/legacy57_retirement.json` beside the RAW campaign.
- Preserved historical reports and the old manifest as provenance inventories,
  not live sessions. No unrelated telemetry/session record was deleted.
- The active campaign is `ecuread-filtered-raw-18-v1`, now reading the requested
  original RAW folder directly. All 18 IDs and source hashes remain unchanged;
  operator attestation, capture groups and confirmed trip metadata remain intact.
- Regenerated unchanged `ctx2-rules-v1` suggestions over **100,688 input frames
  and 100,688 canonical samples**: **3,084 operating proposals + 1,559 events =
  4,643 pending suggestions**. No automatic acceptance or dataset freeze.
- Session 018 remains one uninterrupted **17,976-sample / 4,496.001s** timeline.
  All 18 sources have complete verified RAW speed-evidence coverage.
- No RAW data, models, thresholds or historical detector outputs were modified.
  The protected baseline contains 431 unchanged RAW/model/research files.

## Active Studio

`data/context_annotation/active_campaign.json` pins the active manifest hash.
The default Studio now opens the 18-source campaign; explicit folder mode and
explicit manifest mode remain supported. Both previously running local Studio
ports, 8767 and 8768, were restarted with the active campaign.

```bash
python -m app.context_annotation.web --port 8767
```

Regeneration and retirement are reproducible via the guarded command below.
It verifies the 18 exact hashes, makes a recovery backup, stages proposals for
every full source, and only deletes the exact old session paths after all 18
succeed. Repeating it after retirement regenerates suggestions without deleting
anything else. Human-confirmed RAW-campaign annotations are preserved.

```bash
python -m scripts.reset_context_raw_campaign \
  --input-dir /home/hthnh/ecuread-analyzer/real_data/fulldata_afterclean \
  --delete-legacy-57
```

## Verification

`exports/raw_reset_autolabel.json` records per-source hashes, complete input and
canonical counts, decoder/rule/speed provenance, proposals and processing time.
`exports/raw_reset_browser_verification.json` checks that the live Studio lists
only the 18 originals, that 018 retains its full duration/speed evidence, and
that desktop/mobile evidence views remain functional with zero label acceptance.

Full test suite: **312 passed, 0 failed, 0 errors, 0 skipped**. Six new reset tests
cover deletion scope/path traversal, verified non-overwriting backups, explicit
destructive opt-in, missing/extra/changed RAW inputs, active campaign pinning and
preserved explicit folder mode. The existing real split-alignment regression now
builds its temporary canonical slice from the original RAW instead of depending
on an intentionally deleted session. No test was suppressed.
