"""Read-only campaign verification; never makes a human review decision."""
from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from app.context_annotation.annotation_snapshot import annotation_snapshot
from app.context_annotation.attestation import ATTESTED_POLICY, LONG_IDS, LONG_METADATA, verify_attestation
from app.context_annotation.freeze_dataset import FreezeValidationError
from app.context_annotation.raw_campaign import DEFAULT_CAMPAIGN, EXPECTED_IDS, validate_campaign_split, verify_snapshot
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso


def main():
    campaign = DEFAULT_CAMPAIGN
    manifest_path = campaign / "corpus_manifest.json"
    browser = json.loads((campaign / "exports/ctx23_browser_verification.json").read_text())
    assert browser["accept_correct_reject_uncertain_tested"] and browser["accepted_annotations_preserved_after_reload_and_regeneration"]
    assert not browser["errors"] and browser["campaign_suggestions_accepted"] == 0
    assert {s["session_id"] for s in browser["sessions"]} == {f"session_{n:06d}" for n in (2, 4, 5, 7, 18)}
    tests = json.loads((campaign / "exports/ctx23_tests.json").read_text())
    assert tests["passed"] == tests["tests"] and tests["failures"] == tests["errors"] == tests["skipped"] == 0
    assert all(file_sha256(path) == digest for path, digest in tests["tested_code_sha256"].items())
    manifest, record, overlay = verify_attestation(manifest_path)
    baseline = json.loads((campaign / "provenance/ctx23_read_only_baseline.json").read_text())
    invariance = verify_snapshot(baseline["sha256"])
    assert invariance["unchanged"]
    assert manifest.source_count == manifest.operator_attested_independent_captures == 18
    assert manifest.machine_verified_independent_captures is None
    assert len({s.source_sha256 for s in manifest.sessions}) == 18
    assert {s.session_id for s in manifest.sessions} == EXPECTED_IDS
    for entry in manifest.sessions:
        assert entry.origin["confirmed_session_metadata"] == (LONG_METADATA if entry.session_id in LONG_IDS else {})
        assert not entry.origin["evaluation_eligible"] and not entry.origin["technical_capture_identity_verified"]
    statuses = Counter()
    for path in (campaign / "suggestions").glob("*.json"):
        proposals = json.loads(path.read_text())
        for item in proposals["segments"] + proposals["events"]:
            statuses[item["review_status"]] += 1
            assert not item["human_confirmation"] and not item["review_history"]
    assert not list((campaign / "annotations").iterdir())
    assert statuses == {"pending": 4643}
    entries = overlay["historical_entries"]
    linked = [s for s in entries if s["physical_capture_group_id"]]
    assert len(entries) == 57 and len(linked) == 43
    assert sum(s["operator_evaluation_eligible"] for s in entries) == 41
    assert all(not s["operator_evaluation_eligible"] for s in entries if s["classification"] == "synthetic/demo")
    train, test = sorted(EXPECTED_IDS)[:9], sorted(EXPECTED_IDS)[9:]
    split = {"train": train, "test": test}
    try:
        validate_campaign_split(manifest_path, split)
    except ValueError as exc:
        assert "quarantined" in str(exc)
        strict_blocked = True
    else:
        raise AssertionError("strict policy was silently enabled")
    allowed = validate_campaign_split(manifest_path, split, policy=ATTESTED_POLICY)
    parent = linked[0]["parent_session_id"]
    try:
        validate_campaign_split(manifest_path, {"train": [parent], "test": [linked[0]["session_id"]]}, policy=ATTESTED_POLICY)
    except ValueError as exc:
        assert "crosses partitions" in str(exc)
    else:
        raise AssertionError("alias/split crossed a physical capture group")
    with tempfile.TemporaryDirectory(prefix="ctx23-snapshot-gate-") as tmp:
        output = Path(tmp) / "must-not-exist"
        try:
            annotation_snapshot(corpus_manifest=manifest_path, output=output)
        except FreezeValidationError as exc:
            assert "18 sessions remaining" in str(exc)
            refusal = str(exc)
        else:
            raise AssertionError("incomplete campaign was snapshotted")
        assert not output.exists()
    result = {"schema_version": "ctx23-final-verification-v1", "generated_at": utc_now_iso(),
              "decision": "attestation_recorded_review_ready", "source_count": 18,
              "operator_attested_independent_captures": 18, "machine_verified_independent_captures": None,
              "source_hashes_preserved": True, "unique_physical_capture_groups": 18,
              "historical_group_inheritance_count": len(linked), "historical_operator_evaluation_eligible": 41,
              "decoder_conflicted_replays_excluded": 2, "synthetic_old_entries_excluded": 7,
              "unlinked_outside_selected_corpus_excluded": 7, "strict_policy_still_blocked": strict_blocked,
              "operator_policy_split_validation_only": allowed, "cross_partition_alias_split_rejected": True,
              "source_and_old_accepted_annotations_and_model_and_rule_invariance": invariance,
              "corpus_manifest_sha256": file_sha256(manifest_path), "attestation_sha256": manifest.operator_attestation["sha256"],
              "campaign_proposal_statuses": dict(statuses), "remaining_human_review_sessions": 18,
              "human_review_time_s": None, "snapshot_refused_while_incomplete": refusal,
              "snapshots_created": 0, "models_trained": 0, "detector_evaluations_run": 0,
              "browser_workflow_verified": True, "test_result": tests,
              "evidence_limitations": record["evidence_limitations"]}
    output = campaign / "exports/ctx23_verification.json"
    atomic_write_json(output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
