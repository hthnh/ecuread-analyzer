# H7.3 RCA v2 Semantic Promotion

## Boundary

- RCA v2 separates observations, symptoms, possible causes and recommended checks.
- Empty possible-cause lists are valid.
- Recommended checks may exist without a causal hypothesis.
- Production inference and DriveSafe behavior remain unchanged.

## Comparison

- H7 v1 hypothesis count: `52`
- RCA v2 observation count: `95`
- RCA v2 symptom count: `8`
- RCA v2 possible-cause count: `0`
- RCA v2 recommended-check count: `109`
- Unsupported v1 causal claims removed: `52`
- Case coverage retained: `33/33`
- Insufficient-evidence handling changed: `False`

## Decision

- Recommendation: `promote_v2_for_research`
- Replace H7 v1 as default offline research interpretation: `True`
- H7 v1 remains available for historical reproducibility.
