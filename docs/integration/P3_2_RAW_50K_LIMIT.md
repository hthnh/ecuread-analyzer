# RAW record limit: 50,000

Updated 2026-10-25T17:41:37.333551+07:00 (Asia/Saigon). Source change only; not deployed. This supersedes the 20,000-record RAW limit for the next coordinated Analyzer/DriveSafe candidate. Previously verified P3.2 images remain historical 20k candidates and must be repackaged before a 50k rollout.

The old cap bounded request decoding, model windows, response size and memory; it was not a detector/model limit. The new cap accepts all 50,000 records and rejects 50,001 explicitly. No partial input is submitted. Both synchronized completed and interrupted captures use the same rule. Canonical-only fallback keeps its existing contract.

Changes:

- Analyzer `app/api/raw.py`: validated array maximum 50,000.
- DriveSafe `services/analysis_submission.py`: matching manual eligibility maximum 50,000.
- DriveSafe catalogue: EN/VI messages now say 50,000 / 50.000.
- Boundary and complete-input regression tests updated/added.

The existing 8 MiB RAW body guard remains. Measured outgoing JSON bodies for 50,000 records with sequence and fractional millisecond timestamps:

| Frame / encoding | Bytes |
| --- | ---: |
| Native 24-byte, compact HEX | 5,794,662 |
| Native 24-byte, separated HEX | 6,944,662 |
| Legacy 29-byte, compact HEX | 6,294,658 |
| Legacy 29-byte, separated HEX | 7,694,658 |

All fit below 8,388,608 bytes. The independent byte guard still rejects oversized encodings/envelopes rather than truncating them. The page/read limit is already 100,000, so it does not cut a 50k input short.

Verification: 33 Analyzer tests and 32 DriveSafe tests passed in repository virtual environments. Real HTTP validation/decoding tests preserve all 50,000 samples for native and legacy inputs; model inference is explicitly stubbed in those boundary tests. The actual endpoint rejects 50,001; completed/interrupted eligibility, complete outgoing input, canonical fallback, recovery and ownership/CSRF remain covered. This is not a 50k full-model throughput/memory benchmark or a production canary.

The P3.2 asynchronous worker, compute/client budgets, admission control, active models and decoder algorithms are unchanged. No production inference, metadata rewrite or deployment was performed.
