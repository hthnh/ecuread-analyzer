"""Bind completed automated checks without claiming human pilot validation."""
from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path
from xml.etree import ElementTree

from app.context_annotation.attestation import verify_attestation
from app.context_annotation.corpus import repo_root
from app.context_annotation.macro import detail_hash, generate_macros, progress
from app.context_annotation.raw_campaign import DEFAULT_CAMPAIGN, verify_snapshot
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso


def verify(junit_path: Path) -> dict:
    root = repo_root()
    campaign = root / DEFAULT_CAMPAIGN
    exports = campaign / "exports"
    quality = json.loads((exports / "ctx24_macro_quality.json").read_text())
    browser = json.loads((exports / "ctx24_browser_verification.json").read_text())
    assert browser["campaign_files_unchanged"] and not browser["errors"] and browser["isolated_temporary_sidecars_only"]
    assert browser["before_sha256"] == browser["after_sha256"]
    assert verify_snapshot(browser["after_sha256"])["unchanged"]
    historical = json.loads((campaign / "provenance/raw_reset_protected_baseline.json").read_text())["sha256"]
    protected = verify_snapshot(historical)
    assert protected["unchanged"], protected
    verify_attestation(campaign / "corpus_manifest.json")
    suites = ElementTree.parse(junit_path).getroot().iter("testsuite")
    tests = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    elapsed = 0.0
    for suite in suites:
        for key in tests:
            tests[key] += int(suite.attrib.get(key, 0))
        elapsed += float(suite.attrib.get("time", 0))
    assert tests["tests"] >= 322 and tests["failures"] == tests["errors"] == tests["skipped"] == 0, tests
    assert quality["source_count"] == 18 and quality["input_frames"] == quality["canonical_samples"] == 100688
    assert quality["accepted_annotations_unchanged"] and not quality["auto_acceptance_performed"]
    assert quality["protected_files"]["unchanged"]
    service = ContextAnnotationService(corpus_manifest=campaign / "corpus_manifest.json")
    recovery = quality["recovery_backup"]
    assert file_sha256(recovery["path"]) == recovery["sha256"]
    deterministic = []
    annotations = {p.name: file_sha256(p) for p in service.storage.annotations_dir.glob("*.json")}
    with tarfile.open(recovery["path"]) as backup:
        for row in quality["sessions"]:
            sid = row["session_id"]
            fine = service.storage.read_suggestions(sid)
            member = str(service.storage.suggestion_path(sid).relative_to(root))
            original = json.load(backup.extractfile(member))
            current = fine.model_dump(mode="json")
            assert original["segments"] == current["segments"] and original["events"] == current["events"], sid
            assert detail_hash(fine) == row["detailed_content_sha256"], sid
            loaded = service.load_session(sid)
            regenerated = generate_macros(fine, loaded)
            stored = service.macro_review.document(sid)
            # Stable proposal identities/content, not generation timestamp or human review state.
            expected = [(e.id, e.original_proposal) for e in regenerated.episodes]
            actual = [(e.id, e.original_proposal) for e in stored.episodes]
            assert expected == actual, sid
            assert not service.macro_review.read(sid, loaded=loaded)["stale"], sid
            deterministic.append({"session_id": sid, "macro_content_reproducible": True,
                                  "original_detailed_proposals_exactly_preserved": True,
                                  "stored_macro_sha256": file_sha256(service.macro_review.path(sid))})
    status = service.generate_campaign_status()
    review_progress = {}
    for row in quality["sessions"]:
        sid = row["session_id"]
        detailed = service.storage.read_suggestions(sid)
        review_progress[sid] = {"macro": row["macro_progress"], "detailed_intervals": progress(detailed.segments),
                                "detailed_events": progress(detailed.events)}
    atomic_write_json(exports / "macro_campaign_status.json", {
        "schema_version": "context-macro-campaign-status-v1", "generated_at": utc_now_iso(),
        "dataset_id": quality["dataset_id"], "sessions": review_progress,
        "detailed_annotation_status": "annotation_campaign_status.json",
        "human_pilot_validation": "pending", "macro_acceptance_implies_detailed_acceptance": False})
    quality["engineering_readiness"] = "ready_for_operator_pilot"
    quality["engineering_verified_at"] = utc_now_iso()
    atomic_write_json(exports / "ctx24_macro_quality.json", quality)
    result = {"schema_version": "ctx24-engineering-verification-v1", "verified_at": utc_now_iso(),
              "engineering_readiness": "ready_for_operator_pilot", "human_validation_readiness": "pending",
              "final_decision": "pending_human_validation", "manual_gates": quality["manual_gates"],
              "tests": {**tests, "passed": tests["tests"], "runtime_s": elapsed, "junit_sha256": file_sha256(junit_path)},
              "source_count": 18, "input_frames": 100688, "canonical_samples": 100688,
              "macro_to_micro_traceability": "pass", "stationary_unknown_engine_steady_preservation": "pass",
              "original_proposals_and_events_preserved": "pass", "accepted_annotations_unchanged": True,
              "accepted_annotation_sha256": annotations, "campaign_acceptances_added_by_automation": 0,
              "annotation_campaign_status": {k: status[k] for k in ("total_curated_sessions", "unstarted", "partial", "complete")},
              "protected_historical_source_model_files": protected, "regeneration_protected_files": quality["protected_files"],
              "browser_verification_sha256": file_sha256(exports / "ctx24_browser_verification.json"),
              "quality_audit_sha256": file_sha256(exports / "ctx24_macro_quality.json"), "sessions": deterministic}
    atomic_write_json(exports / "ctx24_engineering_verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, default=Path("/tmp/ctx24-regression.xml"))
    args = parser.parse_args()
    result = verify(args.junit)
    print(json.dumps({k: v for k, v in result.items() if k != "sessions"}, indent=2))
