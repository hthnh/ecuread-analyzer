# H7 Evidence-Based RCA Prototype Report

## Dataset

- Review items: `33`
- Source region types: `{'contextual_only_event': 2, 'disagreement_region': 5, 'insufficient_coverage_region': 6, 'isolation_forest_only_event': 6, 'no_evidence_region': 8, 'overlapping_event': 6}`
- RCA states: `{'hypotheses_generated': 20, 'insufficient_evidence': 5, 'no_anomaly_evidence': 8}`
- Hypotheses generated: `52` across `20` events
- Insufficient-evidence events: `5`

## Rules

- Rule fire counts: `{'rca.coverage.insufficient_evidence': 5, 'rca.data_quality.constant_signal_pattern': 14, 'rca.detectors.disagreement_preservation': 10, 'rca.electrical.contextual_voltage_deviation': 8, 'rca.iforest.multivariate_pattern': 18, 'rca.no_evidence.no_hypothesis': 8, 'rca.operating_state.throttle_related_pattern': 12}`
- Hypothesis counts: `{'h.electrical_supply_variation': 8, 'h.multivariate_pattern_outside_reference': 18, 'h.operating_state_or_throttle_input_variation': 12, 'h.possible_acquisition_or_constant_signal_artifact': 14}`
- Weak or ambiguous possible-status rules: `{'rca.data_quality.constant_signal_pattern': 14, 'rca.iforest.multivariate_pattern': 18, 'rca.operating_state.throttle_related_pattern': 12}`

## Validation Boundary

- Every hypothesis traceable: `True`
- Detector disagreement preserved: `True` (10/10)
- Detector predictions, RCA hypotheses and expert judgments are stored separately.
- RCA does not modify detector output and does not make physical fault claims.

## Decision

- Recommendation: `advance_to_expert_validation`
- All hypotheses require expert or physical validation before operational use.

## Compatibility

- Production inference remains unchanged.
- DriveSafe behavior was not modified.
- Rejected temporal candidates remain excluded.
