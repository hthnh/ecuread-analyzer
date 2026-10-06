# H7.1 Expert Validation of RCA Hypotheses

## Dataset

- Review cases: `33`
- Source region types: `{'contextual_only_event': 2, 'disagreement_region': 5, 'insufficient_coverage_region': 6, 'isolation_forest_only_event': 6, 'no_evidence_region': 8, 'overlapping_event': 6}`
- RCA states: `{'hypotheses_generated': 20, 'insufficient_evidence': 5, 'no_anomaly_evidence': 8}`
- All contract rules represented: `True`
- Review presentations hide rule identifiers; provenance keeps them traceable after judgment.

## Expert Judgments

- Completed hypothesis judgments: `0`
- Reviewed cases: `0`
- Inter-expert agreement computed: `False`

## Rule Decisions

- Rule decision counts: `{'insufficient_expert_evidence': 7}`

## Case Decisions

- Case-level summary: `{'case_count': 33, 'reviewed_case_count': 0, 'unreviewed_case_count': 33, 'cases_with_reasonable_hypothesis': 0, 'cases_with_omitted_important_hypothesis': 0, 'cases_with_unsupported_hypotheses': 0, 'cases_with_useful_next_action': 0}`

## Overall Decision

- Recommendation: `insufficient_expert_evidence`
- No physical root cause is confirmed by RCA or expert judgment alone.

## Compatibility

- Production inference remains unchanged.
- DriveSafe behavior was not modified.
- H1-H7 outputs are reused rather than rewritten.
