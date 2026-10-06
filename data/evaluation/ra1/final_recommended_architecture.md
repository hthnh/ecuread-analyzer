# RA1 Final Recommended Architecture

Final decision: `freeze_current_research_architecture`.

## Required Core

- Canonical telemetry schema and deterministic feature engineering.
- Model Harness.
- Isolation Forest production detector using the existing model artifacts, scaler and thresholds.
- Existing public analysis response fields.
- Additive `diagnostic_evidence` contract with unavailable sections allowed.
- DriveSafe ingestion, persistence and session-review presentation that preserves existing production status/health behavior.

## Research Extensions

- Contextual Battery detector in offline/shadow mode.
- Evidence Aggregator over Isolation Forest and Contextual Battery.
- RCA v2, interpreted as a Diagnostic Evidence Interpretation Engine.
- Historical Evidence for recurrence, persistence, trend and check-priority context.

## Archived Experiments

- Temporal Battery Shift detector.
- Temporal RPM Stability detector.
- RCA v1 causal-hypothesis semantics.

## Completed Integration

- H9B DriveSafe Web App storage/API/UI for `diagnostic_evidence` is complete as an engineering integration.
- The completed integration does not promote research evidence to production certification.

## Pending Validation

- Expert validation remains pending because H7.1 contains no completed expert judgments.

## Components Not To Develop Further Without New Evidence

- Core 3 detector work.
- Score fusion or majority voting.
- Causal RCA output.
- Automatic baseline learning.
- LLM/agent diagnosis.
- Deep temporal networks.

## Freeze Meaning

`freeze_current_research_architecture` does not mean every component is scientifically validated, research evidence is production-certified, RCA root causes are established, or no future work is possible.

It means no additional architectural capability is currently justified without new evidence.
