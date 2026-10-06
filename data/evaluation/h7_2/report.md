# H7.2 Literature-Grounded Surrogate Validation

## Boundary

- Literature-grounded and internally consistent does not mean physically confirmed.
- Expert-review fields remain untouched; no synthetic expert judgments are created.
- Candidate RCA v2 is generated beside H7 v1 and is not production-registered.

## Source Registry

- Sources: `12`
- Source tiers: `{'Tier A': 4, 'Tier B': 5, 'Tier C': 2, 'Tier D': 1}`

## Rule Grounding

- Rule decisions: `{'retain': 3, 'retain_with_restricted_claim': 1, 'revise': 3}`
- Maximum claim levels: `{'observation': 6, 'symptom': 1}`

## Counterexamples

- Counterexample/limiter counts: `{'rca.data_quality.constant_signal_pattern': 14, 'rca.detectors.disagreement_preservation': 10, 'rca.electrical.contextual_voltage_deviation': 8, 'rca.iforest.multivariate_pattern': 14, 'rca.operating_state.throttle_related_pattern': 12}`

## Case Surrogate Review

- Cases: `33`
- Hypotheses reviewed under surrogate namespace: `52`
- Claim levels: `{'observation': 44, 'symptom': 8}`

## Structural Verification

- Candidate v2 rules: `6`
- Blocking structural errors: `0`
- H7 v1 structural anomalies addressed: `2`

## Candidate V2

- Catalog version: `candidate-rca-rule-catalog-v2`
- Activation status: `candidate_only_not_registered`
- Replaces H7 v1: `False`

## Decision

- Overall recommendation: `revise`
- Per-rule decisions: `{'rca.coverage.insufficient_evidence': {'decision': 'provisionally_grounded', 'maximum_defensible_claim_level': 'observation', 'reason': 'Rule prevents hypotheses without active evidence.'}, 'rca.no_evidence.no_hypothesis': {'decision': 'provisionally_grounded', 'maximum_defensible_claim_level': 'observation', 'reason': 'Rule is conservative and avoids unsupported causes.'}, 'rca.electrical.contextual_voltage_deviation': {'decision': 'grounded_with_limitations', 'maximum_defensible_claim_level': 'symptom', 'reason': 'Retain voltage symptom but split candidate causes and require follow-up checks.'}, 'rca.iforest.multivariate_pattern': {'decision': 'revise', 'maximum_defensible_claim_level': 'observation', 'reason': 'Move outside-reference concept from hypothesis to observation/evidence.'}, 'rca.operating_state.throttle_related_pattern': {'decision': 'revise', 'maximum_defensible_claim_level': 'observation', 'reason': 'Require context evidence that distinguishes normal operator demand from unexplained behavior.'}, 'rca.data_quality.constant_signal_pattern': {'decision': 'revise', 'maximum_defensible_claim_level': 'observation', 'reason': 'Constant/low-variance signal alone is insufficient for acquisition-artifact RCA.'}, 'rca.detectors.disagreement_preservation': {'decision': 'provisionally_grounded', 'maximum_defensible_claim_level': 'observation', 'reason': 'Rule is conservative and prevents unsupported fusion.'}}`

## Compatibility

- Production inference remains unchanged.
- DriveSafe behavior was not modified.
- H7 v1 rules and H7.1 expert-review fields are preserved.
