# H2 Model Harness Evaluation Report

## Parity

- Pre-H1 source: `pi_analyzer_handoff.tar.gz`
- Git history available: `False`
- Parity passed: `True`
- Cases: v2_short_or_limited, v2_regular_session
- Limitation: no `.git` directory is present; the checked-in handoff tarball is the historical source.

## Dataset Manifest

- Canonical sessions: 57
- Evaluation-ready canonical sessions: 57
- Calibration datasets cataloged: 3
- Raw real-run sources cataloged: 9
- Unlabeled/context-labeled sessions are not treated as verified normal or faulty data.

## Benchmark

- Sessions evaluated: 57
- Detector statuses: `{'ok': 52, 'skipped': 5}`
- Median latency ms: `218.332984`
- Label metrics computed: `False`
- Label metrics reason: no verified binary normal/fault labels are available; context labels are preserved but not scored as truth

## Outputs

- `evaluation_manifest.json`
- `parity_report.json`
- `benchmark_results.json`
