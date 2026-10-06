# ecuread-analyzer

Internal FastAPI service for parsing ECU RAW logs, decoding a conservative V1 signal set, extracting sliding-window features, and running an Isolation Forest anomaly baseline.

## Scientific Limits

This is an experimental data-analysis service, not a validated mechanical diagnostic system. The `health_score` is an internal normalized score from 0..100 and is not a failure probability, not accuracy, and not proof of a specific fault.

The V1 decoder intentionally avoids older or uncertain labels:

- The previous `speed` interpretation is not used.
- TPS voltage is decoded as `tps_voltage`; V1 does not call voltage ratio a real throttle opening percent.
- Byte 12 is `tps_raw_candidate`, not official TPS percent.
- ECT remains `ect_c_candidate`.
- Unknown bytes are stored for research and are not named speed, injector time, or ignition angle.

## Architecture

```text
DriveSafe Core / ingestion layer
  -> RAW frame parser
  -> checksum validation
  -> conservative signal decoder
  -> canonical telemetry
       -> realtime engineer monitor / dashboard / ML analyzer

ML analyzer
  -> canonical telemetry validation
  -> sample-count sliding windows
  -> tabular statistical features
  -> RobustScaler
  -> IsolationForest
  -> analysis result
```

Code is split into API (`app/api`), canonical domain models (`app/domain`), RAW compatibility ingestion (`app/ingestion/raw_ecu`), ML (`app/ml`), file-backed repositories (`app/repositories`), storage/orchestration (`app/services`), and tests (`tests`).

The analyzer ML core does not own ECU byte offsets or checksum formulas. RAW import remains available for research and historical fixtures, but it first converts logs into canonical telemetry before any windowing, feature extraction, training, or inference.

Production DriveSafe integrations should prefer:

```text
POST /api/v1/analysis
```

with canonical decoded telemetry. RAW compatibility upload is explicit:

```text
POST /api/v1/import/raw-session
```

## Local Setup

```bash
cd /home/hthnh/ecuread-analyzer
python -m pip install -e ".[dev]"
pytest
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://localhost:8000/health
```

## Docker

```bash
cd /home/hthnh/ecuread-analyzer
docker compose up --build
```

The service listens on port `8000` and stores mutable runtime data under the bind mount:

```text
./data:/app/data
```

From another container in the same compose network:

```text
http://ecu-analyzer:8000
```

From another device in the LAN:

```text
http://<host-lan-ip>:8000
```

No tunnel, public domain, cloud service, or HTTPS is required for LAN V1.

## Train Model

Training is intended to be done from the CLI so accidental model overwrite through HTTP is avoided.

```bash
python scripts/train_model.py \
  --input tests/fixtures/serial_log_20251105_204330.txt \
  --sample-interval-ms 100
```

Outputs:

```text
data/models/isolation_forest.joblib
data/models/robust_scaler.joblib
data/models/model_metadata.json
```

The metadata records this as `experimental baseline training data`; it does not claim the vehicle was mechanically healthy.

## Upload A Session

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -F "file=@tests/fixtures/serial_log_20251105_204330.txt" \
  -F "device_id=xiao-ecu-01" \
  -F "vehicle_id=test-bike-01" \
  -F "sample_interval_ms=100"
```

Useful endpoints:

```text
GET  /health
POST /api/v1/analysis
POST /api/v1/sessions
POST /api/v1/import/raw-session
GET  /api/v1/sessions?limit=20
GET  /api/v1/sessions/{session_id}
GET  /api/v1/sessions/{session_id}/telemetry?limit=100&offset=0
GET  /api/v1/sessions/{session_id}/analyses
GET  /api/v1/sessions/{session_id}/frames?limit=100&offset=0
GET  /api/v1/sessions/{session_id}/windows?limit=100&offset=0
GET  /api/v1/analyses/{analysis_run_id}
GET  /api/v1/analyses/{analysis_run_id}/windows?limit=100&offset=0
GET  /api/v1/model
```

Model training API exists at `POST /api/v1/model/train`, but is disabled unless `ALLOW_MODEL_TRAINING_API=true`.

## Decoded Signal Schema

Bytes are numbered `b[0]` through `b[28]`.

| Field | Formula |
| --- | --- |
| `rpm` | `(b[9] << 8) \| b[10]` |
| `tps_voltage` | `b[11] * 5.0 / 256.0` |
| `tps_raw_candidate` | `b[12]` |
| `battery_voltage` | `b[15] / 10.0` |
| `iat_c` | `b[16] - 40` |
| `ect_c_candidate` | `b[17] - 40` |
| `map_raw` | `b[18]` |

Unknown/research fields:

```text
signal_b19
signal_word_20_21
signal_b22
signal_b23
signal_b24
```

Initial validation limits are configurable in code and default to:

```text
rpm: 0..16000
tps_voltage: 0.0..5.0
tps_raw_candidate: 0..255
battery_voltage: 6.0..18.0
iat_c: -40..150
ect_c_candidate: -40..180
map_raw: 0..255
```

## Checksum

A 29-byte frame is treated as checksum-valid when:

```python
sum(frame) % 256 == 251
```

Equivalently:

```python
expected_checksum = (251 - sum(frame[:28])) % 256
actual_checksum = frame[28]
```

Frames with bad checksum are preserved in parsed output, but are not used for training or inference windows by default.

## Time Metadata

Old logs do not contain per-frame timestamps. The service always preserves `frame_index`. If `sample_interval_ms` is supplied, it adds `relative_time_ms = frame_index * sample_interval_ms`. If only `sampling_rate_hz` is supplied, relative time is derived from that rate. Without sampling metadata, the time basis remains `sample_index`.

Sliding windows are configured by sample count, not assumed seconds:

```text
WINDOW_SIZE_SAMPLES=50
WINDOW_STEP_SAMPLES=10
```

## Synthetic Anomalies

Synthetic data is only for tests, demos, and experimental evaluation. It is not mixed into default training.

```bash
python scripts/generate_synthetic_anomalies.py \
  --input tests/fixtures/serial_log_20251105_204330.txt \
  --output /tmp/rpm_spike.txt \
  --metadata-output /tmp/rpm_spike.json \
  --anomaly-type rpm_spike \
  --start-frame 100 \
  --end-frame 120 \
  --amplitude 2500
```

Supported types:

```text
rpm_spike
rpm_freeze
tps_voltage_freeze
iat_sudden_jump
ect_sudden_jump
battery_gradual_drop
map_freeze
random_missing_frames
broken_checksum
rpm_tps_relation_mismatch
```

## Storage Layout

```text
data/
├── raw/{session_id}/original.txt
├── processed/{session_id}/frames.csv
├── processed/{session_id}/windows.csv
├── results/{session_id}/result.json
├── telemetry/{session_id}/metadata.json
├── telemetry/{session_id}/samples.jsonl
├── analyses/{analysis_run_id}/analysis_run.json
├── analyses/{analysis_run_id}/analysis_windows.jsonl
├── analyses/{analysis_run_id}/window_features.jsonl
├── entities/sessions/{session_id}.json
├── entities/raw_artifacts/{artifact_id}.json
├── entities/decoder_versions/{decoder_id}_{version}.json
├── entities/model_versions/{model_version_id}.json
└── models/
```

Raw uploads are immutable per session. Session IDs are generated independently of filenames, filenames are sanitized, and upload size is limited by `MAX_UPLOAD_MB`. CSV/JSON exports remain for research/debugging; the file-backed `entities`, `telemetry`, and `analyses` directories represent the production logical boundaries until a relational database is introduced.

Every canonical telemetry session records `ecu_profile_id`, `decoder_id`, and `decoder_version`. Every analysis run records `model_version_id` and `feature_schema_version` (`ecu-window-features-v1`). Re-analyzing a session creates a new analysis run rather than overwriting the session.

## Fixtures

The repository includes copied fixtures under `tests/fixtures`. The requested `/home/hthnh/a/serial_log_20251105_203153.txt` was not present in this workspace, so the available `/home/hthnh/a/serial_log_20251105_203744.txt` was copied instead, along with `/home/hthnh/a/serial_log_20251105_204330.txt`.

## Tests

```bash
pytest
```

Coverage includes parser variants, checksum behavior, decoder expectations, feature stability, API behavior, and an end-to-end train/infer pipeline.
