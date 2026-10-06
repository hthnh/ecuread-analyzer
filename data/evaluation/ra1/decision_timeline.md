# RA1 Decision Timeline

## H1

Hypothesis: A minimal multi-detector harness can be introduced without changing Isolation Forest behavior.

Implementation: `DetectorResult`, `DetectorIdentity`, `ModelHarness`, and an Isolation Forest detector wrapper.

Observed result: Production inference still uses the existing artifacts and behavior through the harness.

Decision: Advance.

Consequence: Evaluation could register additional detectors without modifying the Isolation Forest implementation.

## H2

Hypothesis: H1 preserved pre-harness behavior.

Implementation: Independent parity validation against the checked-in Pi handoff source snapshot and a reusable benchmark manifest.

Observed result: Parity passed for 2 handoff cases; public API compatibility passed. Git history checkout was unavailable, so the handoff snapshot is the historical reference.

Decision: Advance with documented limitation.

Consequence: Later detector research could run without changing production inference.

## H3

Hypothesis: Verified telemetry supports a contextual statistical detector.

Implementation: Contextual Battery detector using verified RPM/TPS/battery features and nominal/reference sessions.

Observed result: Feasible: True; reason: Reference split has enough windows for idle, low-load and mid-load running contexts..

Decision: advance_for_offline_shadow_revision_not_production.

Consequence: Core 2 entered offline shadow evaluation only.

## H3.1

Hypothesis: Core 2 adds stable, interpretable information beyond Isolation Forest.

Implementation: Disagreement classification, manual review manifest, reference leave-one-session-out stability, and context-boundary review.

Observed result: 23 contextual events, 623 disagreement windows, no reference/boundary artifact dominance.

Decision: advance.

Consequence: Contextual Battery remains the only active research detector beyond Isolation Forest.

## H4

Hypothesis: Temporal battery shifts add complementary evidence.

Implementation: EWMA within-session Temporal Battery Shift detector.

Observed result: 1 temporal event, 0 Core3-only events.

Decision: revise.

Consequence: A revised Core 3 target was explored rather than promoting battery temporal shifts.

## H4.1

Hypothesis: Temporal RPM stability might be a better Core 3 target.

Implementation: Closed-throttle RPM stability candidate plus candidate selection audit.

Observed result: Candidate selection: `both_reject`. Rationale: Neither temporal candidate currently demonstrates sufficient distinct event-level value..

Decision: Reject both temporal candidates.

Consequence: The active detector set stayed at two.

## H5

Hypothesis: Heterogeneous detector evidence can be aggregated without voting or score fusion.

Implementation: Evidence Aggregator with states for no evidence, single/multiple detector evidence, disagreement and insufficient coverage.

Observed result: 205 aggregate events; evidence states: detector_disagreement=623, insufficient_coverage=2916, multiple_detector_evidence=255, no_evidence=8776, single_detector_evidence=289.

Decision: advance.

Consequence: Aggregated evidence became the input to RCA v2 and history.

## H6

Hypothesis: Active detectors have enough evidence to remain in the research path.

Implementation: Controlled-condition validation and blind-review infrastructure.

Observed result: IF controlled recall was 3/3; Contextual Battery controlled recall was 1/1. Precision, false-alert rate and event-level recall were not computed.

Decision: Retain both active detectors with limitations.

Consequence: Validation guarded against overclaiming while allowing research to continue.

## H7

Hypothesis: Evidence-based deterministic RCA can generate useful diagnostic hypotheses.

Implementation: RCA v1 rules and expert review packet.

Observed result: 20 events with hypotheses and 5 insufficient-evidence events; all hypotheses required expert or physical validation.

Decision: advance_to_expert_validation.

Consequence: Expert validation was required before any semantic promotion.

## H7.1

Hypothesis: Expert validation can validate RCA rules.

Implementation: Expert validation dataset and judgment schema.

Observed result: 0 completed judgments and 0 independently confirmed physical root causes.

Decision: insufficient_expert_evidence.

Consequence: RCA claims could not be expert-validated yet.

## H7.2

Hypothesis: Literature-grounded surrogate review can identify unsupported RCA semantics.

Implementation: Rule grounding, counterexample analysis and structural verification.

Observed result: grounded_with_limitations=1, provisionally_grounded=3, revise=3 with semantic boundary: Literature-grounded and internally consistent does not mean physically confirmed..

Decision: revise.

Consequence: RCA v1 needed semantic revision.

## H7.3

Hypothesis: RCA v2 can preserve useful interpretation while removing unsupported causes.

Implementation: RCA v2 observation/symptom/check engine.

Observed result: 52 unsupported v1 claims removed; possible causes: 0; checks: 109.

Decision: promote_v2_for_research.

Consequence: RCA v2 became the current research interpretation layer.

## H8

Hypothesis: Structured history adds information unavailable from one session.

Implementation: Historical observation index, recurrence/trend comparisons and check-priority augmentation.

Observed result: 716 records, 27 usable-history cases, 22 check-priority changes, 0 causal changes.

Decision: advance.

Consequence: History remained a research evidence layer, not baseline training or diagnosis.

## H9

Hypothesis: Analyzer evidence can be packaged for DriveSafe without changing APIs.

Implementation: Additive `diagnostic_evidence` contract and retrospective payload examples.

Observed result: 33 payloads; DriveSafe repo available: True; analyzer side ready: True.

Decision: revise.

Consequence: Analyzer-side integration became ready for H9B DriveSafe consumption.

## H9B

Hypothesis: DriveSafe can consume, persist, serialize and render versioned diagnostic research evidence without changing production analysis semantics.

Implementation: Backward-compatible optional `diagnostic_evidence` ingestion in analyzer and Pi import paths, JSON persistence in existing `AnalysisResult.result_summary`, additive API/session serialization, display-only `diagnostic_evidence_view`, and a collapsed "Additional diagnostic evidence" UI section.

Observed result: Analyzer emits additive diagnostic evidence: True; DriveSafe integration completed: True; DriveSafe checks: display_view_is_snapshot_only=True, legacy_behavior_tested=True, malformed_evidence_isolated=True, optional_analyzer_evidence_tolerated=True, optional_pi_evidence_tolerated=True, production_status_health_unchanged=True, raw_evidence_exposed=True, recommended_check_priority_preserved=True, repo_available=True, session_detail_collapsed_section=True, valid_evidence_persisted_in_result_summary=True.

Decision: Complete integration as an engineering boundary only.

Consequence: H9/H9B is an integration contribution, not scientific validation of detector accuracy, RCA causes or historical evidence.
