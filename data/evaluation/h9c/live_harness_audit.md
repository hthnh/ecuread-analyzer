# H9C Live Harness Audit

Generated before behavior changes for H9C.

## Current Live Path

`/api/v1/analysis` accepts a `CanonicalTelemetrySession` and calls `AnalysisService.analyze(..., process_with_model=True, persist=True)`.

`/api/v1/raw/decode-analyze` decodes raw records through the Honda raw adapter, then calls the same `AnalysisService.analyze(...)` path with the decoded canonical session.

`SessionProcessor.process_import(...)` also calls `AnalysisService.analyze(...)` for uploaded sessions, then writes the session result artifact with the analyzer summary and top-level `diagnostic_evidence`.

The live/session flow is:

1. canonical session
2. `canonical_session_to_frame_data(...)`
3. `build_feature_frame(...)`
4. `run_inference(...)`
5. `ModelHarness([IsolationForestDetector(bundle)])`
6. `build_diagnostic_evidence(...)`
7. API response and persisted `analysis_run.json`

## Registered Live Detectors

The normal live/session model harness registers only:

- `isolation_forest`

`run_inference()` constructs `ModelHarness([IsolationForestDetector(bundle)])`. No contextual, temporal, RCA, or historical component is registered in that live harness.

## Offline/Research Components

The following components exist as research/offline evaluation code and artifacts:

- `contextual_battery_voltage` in `app/ml/contextual_detector.py`
- H5 evidence aggregation in `app/ml/evidence_aggregation.py`
- RCA v2 in `app/ml/root_cause_analysis_v2.py`
- H8 historical evidence in `app/evaluation/historical_evidence.py`

H5 evaluation code registers `ModelHarness([IsolationForestDetector(bundle), ContextualBatteryVoltageDetector(reference)])`, but that is not the production/live `run_inference()` path.

Temporal candidates are archived/rejected in H5 policy and are not part of the live path:

- `temporal_battery_shift`
- `temporal_rpm_stability`

## Current Diagnostic Evidence Shape

Live `diagnostic_evidence.detectors` is built from the live `run_inference()` detector summaries, so the `isolation_forest` entry is real live output.

The live payload currently marks:

- `capability_status.research_evidence_available = false`
- RCA v2 unavailable
- historical evidence unavailable

Therefore, before H9C changes, `diagnostic_evidence.detectors` is real for Isolation Forest but does not represent live execution of the contextual battery detector, H5 aggregation, RCA v2, or H8 historical evidence.

## DriveSafe Current Consumer

DriveSafe accepts optional analyzer `diagnostic_evidence`, persists it in `AnalysisResult.result_summary`, exposes it through session snapshots, and renders a collapsed "Additional diagnostic evidence" section.

Before H9C changes, DriveSafe does not compute detector, RCA, or historical logic. It displays analyzer-provided evidence only.

## Signal/Scope Notes

The current contextual battery detector uses verified RPM, TPS raw, TPS voltage, and battery voltage features. No MAP-dependent contextual logic is required for H9C.

## Audit Answer

Contextual Battery was previously offline/research only for the normal live/session analysis path. Live diagnostic evidence contained real Isolation Forest output plus compatibility/research packaging that explicitly marked contextual/RCA/history sections unavailable.
