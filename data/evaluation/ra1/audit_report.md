# RA1 Audit Report

## Final Decision

`freeze_current_research_architecture`.

The current research architecture is coherent enough to freeze: production remains Isolation Forest only, the research path has exactly two active detectors, rejected temporal candidates are visible, RCA v2 no longer overclaims causality, and diagnostic evidence integration is additive through DriveSafe. No simplification is required before freeze except keeping temporal candidates archived rather than active.

## Final Audit Questions

1. What problem does the current system actually solve?

It detects and reports unusual ECU telemetry patterns from canonical sessions, preserves detector-local evidence, and organizes research evidence for review. It does not diagnose confirmed faults.

2. What is the minimum architecture required to solve it?

Canonical telemetry, window features, Model Harness, Isolation Forest, and the public analysis response. Additive diagnostic evidence is useful for downstream compatibility but not required for the original anomaly decision.

3. Why are there currently two active detectors rather than three?

Isolation Forest is the production baseline. Contextual Battery survived H3/H3.1 as stable and distinct. Temporal Battery and Temporal RPM were rejected in H4.1 because neither justified Core 3 promotion on current evidence.

4. What does each active detector contribute?

Isolation Forest contributes broad multivariate outlier evidence. Contextual Battery contributes context-conditioned voltage deviation evidence based only on verified RPM/TPS/battery features.

5. What experiments were rejected and why?

Temporal Battery, Temporal RPM, voting, score fusion, universal scores, arbitrary confidence, RCA v1 causal semantics, automatic baseline learning, vector memory, LLM/agent diagnosis and deep temporal networks were rejected or deferred because current evidence does not justify them.

6. Why is score fusion currently unjustified?

The active detectors use different score semantics and scales. H5 explicitly preserved detector-local scores and set `score_fusion_used=false`.

7. What does Evidence Aggregation add?

It adds coverage, availability, disagreement and evidence-state semantics. H5 recorded 12859 windows with states detector_disagreement=623, insufficient_coverage=2916, multiple_detector_evidence=255, no_evidence=8776, single_detector_evidence=289; it does not vote or diagnose.

8. Is RCA v2 actually RCA?

Only in a compatibility sense. Architecturally it is more accurately a Diagnostic Evidence Interpretation Engine. It produced 95 observations, 8 symptoms, 0 possible causes and 109 checks.

9. What does Historical Evidence add?

It adds recurrence, persistence, trend and priority context. H8 changed check priority in 22 cases, added no value in 8 cases, and changed causal hypotheses in 0 cases.

10. What claims are physically/empirically validated?

The strongest empirical claims are deterministic pipeline behavior, H2 parity against the handoff snapshot, verified v2 signal availability for RPM/TPS/battery, and sparse controlled-condition detector response. Physical root causes are not validated.

11. What claims remain provisional?

Contextual Battery usefulness, recurrence-based check prioritization, and detector disagreement interpretability remain provisional.

12. What is still unsupported?

Confirmed root cause, component failure, vehicle health, failure probability, universal anomaly score, and future failure prediction.

13. Which components are production-ready?

Canonical telemetry, feature engineering, Model Harness, Isolation Forest production inference, additive diagnostic evidence packaging, and the DriveSafe consumer that persists/displays research evidence without changing production behavior. Research evidence sections remain non-gating.

14. Which remain research-only?

Contextual Battery, Evidence Aggregation, RCA v2 interpretation, Historical Evidence, H9 retrospective research payloads and the research content shown in DriveSafe's collapsed diagnostic evidence UI.

15. Is any current component unnecessary?

Temporal detector candidates are unnecessary in the active architecture. They should remain archived only for reproducibility.

16. What should not be developed further without new evidence?

Core 3, score fusion, majority voting, causal RCA output, automatic baselines, LLM/agent diagnosis and deep temporal networks.

17. What new evidence would justify reopening rejected directions?

Validated, independent phenomena; calibrated score comparability; completed expert/physical root-cause evidence; governed baseline approval; or a demonstrated need for unstructured reasoning that deterministic logic cannot meet.

18. What are the strongest genuine contributions?

A minimal harness that preserved production behavior, explicit heterogeneous evidence aggregation without fusion, a feasible contextual voltage detector, negative temporal detector results, evidence-grounded RCA v2 semantics, structured historical evidence with cutoff governance, and H9/H9B backward-compatible operational integration.

## Parity and API Compatibility

H2 parity passed: `True`. The historical source was `pi_analyzer_handoff.tar.gz`, with limitation: No .git directory is present, so Git revision checkout of pre-H1 code is not possible.; The pre-H1 reference is the checked-in pi_analyzer_handoff.tar.gz source snapshot plus an isolated copy of its inference logic.. Public API compatibility passed: `True`.

## Terminology and Reference Audit

The H3.1 terminology audit does not treat `normal_train` as verified healthy ground truth. It is nominal/reference baseline data unless independent evidence says otherwise. The contextual reference used 12 reference sessions, and overlap policy excluded reference session IDs and duplicate sample hashes from shadow evaluation. Provenance is preserved through session IDs, artifact hashes, decoder versions, telemetry schema and the reference-window trace.

The verified-signal set for contextual detection is rpm, tps_voltage, tps_raw, battery_voltage. IAT and ECT remain excluded from the contextual detector because H3 did not treat them as strictly verified for that purpose. RPM and TPS are considered time-aligned because canonical rows contain them in the same decoded sample and window features share row boundaries.

## Coverage and Disagreement Audit

H5 active detector coverage was not complete over all windows. Aggregation counted applicable detector coverage as 0=2916, 1=290, 2=9653. It recorded IF-only finding windows=910, contextual-only finding windows=2, overlapping finding windows=255 and disagreement windows=623.

Those numbers are evidence-routing facts, not accuracy metrics. `insufficient_coverage` and unavailable detector states remain distinct from negative findings.

## Detector Evidence Maturity

Isolation Forest controlled-condition recall was 3/3 in H6, but precision and false-alert rates were not computed. Contextual Battery controlled-condition recall was 1/1 for its relevant controlled case, but that does not imply universal 100% recall. H6 explicitly lists ground-truth limitations: Controlled labels are sparse and condition-level.; Human-review labels are useful expert context but are not used as precision/recall ground truth.; Most aggregate events remain inconclusive until blind/manual review is completed..

## Evidence Aggregation Boundary

`no_evidence` means no applicable active detector produced anomaly evidence; it is not verified healthy. `skipped`, `failed`, `not_applicable` and `unavailable` remain distinct from normal predictions.

## What The System Can Say Today

Supported statements include: an unusual multivariate pattern was observed; a context-conditioned battery-voltage deviation was observed; active detectors agreed or disagreed; detector evidence was unavailable or insufficient; a similar structured observation appeared previously; a recommended check should be prioritized for review.

Unsupported statements include: a component has failed; the vehicle is healthy; root cause is confirmed; failure probability is a specific percentage; a component will fail soon; one detector's score is directly comparable to another detector's score.

## RCA and Diagnostic Checks

H7 v1 generated 20 hypothesis-bearing events and needed expert validation. H7.1 had 0 completed judgments, so `insufficient_expert_evidence` was the only defensible outcome. H7.2 surrogate validation let research continue by downgrading claim levels; H7.3 removed 52 unsupported v1 causal claims.

Recommended checks differ from causal claims: they are follow-up actions tied to observed evidence. H7.3 generated 109 checks over 33 cases, which is useful for review but potentially noisy because generic checks can repeat across similar evidence families.

Concepts such as multivariate outlier, constant signal and unusual RPM/TPS pattern cannot automatically become root causes. They are observations or evidence relationships. A root-cause claim needs a higher evidence level: physical measurement, qualified expert judgment, OEM/normative constraints or a validated evidence-to-cause mapping.

## Expert Validation and Evidence Hierarchy

H7.1 prepared expert review infrastructure and a 33-case dataset, but completed expert judgments are unavailable. H7.2 surrogate validation is therefore useful only as literature-grounded and structural review; it cannot substitute for expert or physical validation.

The effective evidence hierarchy is: independent physical ground truth; qualified expert judgment; OEM or normative documentation; peer-reviewed engineering literature; internal controlled empirical evidence; internal observational evidence; engineering assumption. Lower levels can support research direction, but repetition of an assumption cannot promote it to verified truth.

## Historical Evidence Value

H8 indexed 716 historical records across 38 sessions. The current value is useful but narrow: it changes check priority and recurrence context, not root-cause conclusions.

Historical Evidence is not the same thing as a nominal/reference baseline. The nominal baseline is data deliberately eligible to represent reference behavior. The historical corpus contains prior observations, including anomalies, and must not silently redefine normal behavior. H8 baseline governance recorded 13 nominal/reference candidates, 3 excluded candidates and 0 trusted-normal ground-truth sessions.

## Why No LLMs, Agents or Deep Networks Yet

The project excludes LLM diagnosis, autonomous agents, vector-search reasoning and agent memory because the current requirement is deterministic, traceable, testable evidence handling over a small dataset. This does not mean LLMs or agents are inherently unsuitable; it means no current requirement needs unstructured reasoning or adaptive tool orchestration.

Deep temporal networks are technically possible, but not justified by current evidence. There is not enough verified labeled temporal data, the simpler detectors already expose the main observable phenomena, and interpretability/reproducibility matter more than model capacity at this stage.

## Most Valuable Future Physical Measurements

The highest-value validation measurements are synchronized external battery/charging voltage, starter/engine-running or alternator charging state, known accessory electrical load state, repeat captures for low-voltage candidate sessions, and bench spot checks for TPS/RPM/battery calibration during context transitions.

## Integration Status

H9 analyzer side ready: `True`. Analyzer emits additive diagnostic evidence: `True`. DriveSafe Web App repo available: `True`. H9B DriveSafe integration completed: `True`.

DriveSafe tolerantly ingests optional evidence, persists valid evidence, isolates malformed optional evidence from production analysis, exposes raw evidence, builds display-only `diagnostic_evidence_view`, and renders the collapsed research-evidence section. Existing anomaly/status/health fields remain authoritative.

## Final Freeze Decision

Decision after RA1.1 reconciliation: `freeze_current_research_architecture`.

This freeze does **not** mean every component is scientifically validated, research evidence is production-certified, RCA root causes are established, or no future work is possible. It means no additional architectural capability is currently justified without new evidence.

Final question: Is the implemented H1-H9B system internally consistent with the research claims and production boundaries documented by RA1?

Answer: yes. Finalize the architecture freeze.

## Verification To Record

Run the analyzer test suite and Pi handoff verifier after generating this RA1 bundle. Record results in the final user-facing response; do not regenerate H1-H9 historical outputs unless provenance requires it.
