# RA1 Rejected Alternatives

## Three-model requirement

Why considered: A third detector was requested as Core 3.

Why rejected or deferred: H4/H4.1 did not produce a justified third active detector.

Evidence to reconsider: A validated phenomenon current detectors demonstrably miss.

## Temporal Battery detector

Why considered: Sustained voltage shifts are plausible temporal evidence.

Why rejected or deferred: One stable event but zero unique event value.

Evidence to reconsider: Independent temporal voltage phenomenon with unique, verified value.

## Temporal RPM detector

Why considered: Closed-throttle RPM instability might be complementary.

Why rejected or deferred: Low coverage and parameter sensitivity despite one Core3-only event.

Evidence to reconsider: Repeatable, validated RPM-stability phenomenon with adequate coverage.

## Majority voting

Why considered: Multiple detectors invite a consensus rule.

Why rejected or deferred: Only two active detectors exist and detector scopes differ.

Evidence to reconsider: At least three validated detectors with calibrated comparable decision semantics.

## Score fusion

Why considered: A single score is attractive for UI simplicity.

Why rejected or deferred: Scores use incompatible semantics and scales.

Evidence to reconsider: Calibration study proving cross-detector score comparability.

## Universal anomaly score

Why considered: Could simplify downstream consumers.

Why rejected or deferred: Would hide detector-local meaning and skipped/failed states.

Evidence to reconsider: Validated transformation preserving detector semantics and uncertainty.

## Arbitrary confidence percentages

Why considered: Could appear user-friendly.

Why rejected or deferred: No probability calibration or ground-truth base rates exist.

Evidence to reconsider: Calibrated probabilistic model with verified labels.

## RCA v1 causal interpretation

Why considered: Initial H7 hypothesis engine explored diagnostic language.

Why rejected or deferred: H7.2/H7.3 found 52 unsupported causal claims.

Evidence to reconsider: Physical/expert evidence-to-cause mappings.

## Automatic baseline learning

Why considered: History could update normal behavior.

Why rejected or deferred: Risks contaminating baseline with anomalies.

Evidence to reconsider: Governed baseline approval workflow and verified normal evidence.

## Vector DB / embedding memory

Why considered: Could retrieve similar historical cases.

Why rejected or deferred: Structured keys were sufficient and more reproducible.

Evidence to reconsider: Unstructured evidence corpus where deterministic retrieval fails.

## LLM RCA

Why considered: LLMs could phrase or reason over diagnostics.

Why rejected or deferred: Current needs are deterministic, traceable and testable.

Evidence to reconsider: A demonstrated natural-language/tool reasoning requirement that deterministic logic cannot meet.

## Agent harness

Why considered: Agents could orchestrate diagnosis.

Why rejected or deferred: Autonomy would reduce reproducibility and add failure modes.

Evidence to reconsider: Human-approved workflow requiring adaptive tool use beyond fixed evaluation logic.

## Deep temporal network

Why considered: LSTM/Transformer models are technically possible.

Why rejected or deferred: Data and labels do not justify complexity.

Evidence to reconsider: Large validated temporal dataset and simpler-method failure on a meaningful task.
