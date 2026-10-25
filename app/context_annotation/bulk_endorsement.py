"""Offline, hash-pinned operator endorsement; never impersonates interval review."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import shutil

from app.context_annotation.attestation import verify_attestation
from app.context_annotation.auto_labeler import stable_hash
from app.context_annotation.macro import MacroDocument, detail_hash
from app.context_annotation.models import (
    ANNOTATION_SCHEMA_VERSION_V2, TAXONOMY_VERSION_V2, BulkEndorsement,
    ContextAnnotationDocument, ContextEvent, ContextSegment, OperatingLabels,
    SuggestionDocument,
)
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso
from app.context_annotation.validation import AnnotationValidationError, validate_document


def json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def json_sha(payload: dict) -> str:
    return hashlib.sha256(json_bytes(payload)).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text()) if path.is_file() else None


def digest(path: Path):
    return file_sha256(path) if path.is_file() else None


def provenance_context(fine: dict, macro: dict) -> dict:
    items = fine["segments"] + fine["events"] + macro["episodes"]
    actions = [next((h for h in reversed(i.get("review_history", [])) if h.get("action") == "accept" and h.get("reviewer")), {}) for i in items]
    return {"action_counts": Counter(h["action_id"] for h in actions if h.get("action_id")),
            "suggestion_lookup": {s["id"]: s for s in fine["segments"] + fine["events"]}}


def provenance(item: dict, *, action_counts=None, suggestion_lookup=None) -> str:
    if item.get("human_confirmation") or any(
        h.get("action") == "accept" and h.get("reviewer") for h in item.get("review_history", [])
    ):
        record = (suggestion_lookup or {}).get(item.get("suggestion_id"), item)
        if (item.get("human_confirmation") or {}).get("corrections") or any(
            h.get("action") == "corrected" or h.get("corrections") for h in record.get("review_history", [])
        ):
            return "manually_corrected"
        action = next((h for h in reversed(record.get("review_history", [])) if h.get("action") == "accept" and h.get("reviewer")), {})
        if action_counts is None or not action.get("action_id"):
            return "named_human_confirmation_method_unresolved"
        return "multi_item_human_confirmation" if action_counts[action["action_id"]] > 1 else "single_item_human_confirmation"
    if item.get("bulk_endorsement"):
        return "bulk_operator_endorsement"
    return "human_authored" if item.get("origin") == "human" else "unresolved"


def overlaps(a: dict, b: dict) -> bool:
    return a["start_s"] < b["end_s"] - 1e-9 and a["end_s"] > b["start_s"] + 1e-9


def check_traceability(fine: dict, macro: dict, duration: float) -> None:
    proposals = SuggestionDocument.model_validate(fine)
    grouped = MacroDocument.model_validate(macro)
    if grouped.source_sha256 != proposals.source.sha256 or grouped.detailed_content_sha256 != detail_hash(proposals):
        raise AnnotationValidationError("stale macro/source identity")
    children = {s.id: s for s in proposals.segments}
    events = {s.id for s in proposals.events}
    for episode in grouped.episodes:
        if episode.end_s > duration + 1e-9 or set(episode.child_ids) - children.keys() or set(episode.event_ids) - events:
            raise AnnotationValidationError("macro has out-of-bounds or missing micro/event references")
    for axis in ("operating", "thermal"):
        if {c for e in grouped.episodes if e.axis == axis for c in e.child_ids} != children.keys():
            raise AnnotationValidationError("macro layer loses original intervals")
        accepted = sorted((e for e in grouped.episodes if e.axis == axis and e.review_status == "accepted"), key=lambda e: e.start_s)
        if any(overlaps(a.model_dump(), b.model_dump()) for a, b in zip(accepted, accepted[1:])):
            raise AnnotationValidationError("accepted macro episodes overlap within an axis")


def plan_session(*, fine: dict, macro: dict, annotation: dict, duration: float,
                 endorsement: BulkEndorsement) -> tuple[dict, dict, dict, list[dict]]:
    """Return staged documents, preserving existing human dictionaries byte-for-byte in JSON meaning."""
    check_traceability(fine, macro, duration)
    before_detail = detail_hash(SuggestionDocument.model_validate(fine))
    fine, macro, annotation = deepcopy(fine), deepcopy(macro), deepcopy(annotation)
    unresolved = []
    bulk = endorsement.model_dump(mode="json")
    history = {"action": "bulk_endorse", **bulk, "time_basis": "not_measured"}
    held_children = set()
    for e in macro["episodes"] + macro.get("retained_reviews", []):
        corrected = any(h.get("action") == "corrected" or h.get("corrections") for h in e["review_history"])
        if e["review_status"] in {"rejected", "uncertain"} or corrected:
            held_children.update(e["child_ids"])
            unresolved.append({"layer": "macro", "id": e["id"], "reason": "preserved human decision/correction"})
    existing = {s.get("suggestion_id"): s for s in annotation["segments"] + annotation.get("events", []) if s.get("suggestion_id")}
    annotation.setdefault("events", [])
    changed = False
    for key in ("segments", "events"):
        for item in fine[key]:
            status = item["review_status"]
            if item["id"] in existing:
                if status != "accepted":
                    unresolved.append({"layer": "micro", "id": item["id"], "reason": "authoritative annotation/status conflict"})
                continue
            if status == "accepted":
                unresolved.append({"layer": "micro", "id": item["id"], "reason": "accepted proposal missing annotation"})
                continue
            if status != "pending" or item["review_history"] or item["id"] in held_children:
                unresolved.append({"layer": "micro", "id": item["id"], "reason": "preserved human decision/correction"})
                continue
            if key == "segments" and any(overlaps(item, s) for s in annotation["segments"]):
                unresolved.append({"layer": "micro", "id": item["id"], "reason": "overlaps human annotation"})
                continue
            common = dict(id="auto_" + item["id"], start_s=item["start_s"], end_s=item["end_s"],
                          origin="auto_suggested", suggestion_id=item["id"], rule_version=item["rule_version"],
                          evidence=item["evidence"], evidence_quality=item["evidence_quality"], reason=item["reason"],
                          original_proposal=item["original_proposal"], notes=item.get("notes", ""), bulk_endorsement=endorsement)
            accepted = (ContextEvent(**common, event=item["event"]) if key == "events" else
                        ContextSegment(**common, taxonomy_version=TAXONOMY_VERSION_V2,
                                       labels=OperatingLabels(operating_context=item["operating_context"], thermal_phase=item["thermal_phase"])))
            annotation[key].append(accepted.model_dump(mode="json"))
            existing[item["id"]] = annotation[key][-1]
            item.update(review_status="accepted", bulk_endorsement=bulk)
            item["review_history"].append(history.copy())
            changed = True
    if changed:
        annotation.update(schema_version=ANNOTATION_SCHEMA_VERSION_V2, updated_at=endorsement.reviewed_at)
        for key in ("segments", "events"):
            annotation[key].sort(key=lambda s: (s["start_s"], s["end_s"], s["id"]))
    fine_by_id = {s["id"]: s for s in fine["segments"]}
    for e in macro["episodes"]:
        if e["review_status"] != "pending" or e["review_history"]:
            continue
        children = [fine_by_id[c] for c in e["child_ids"]]
        compatible = True
        for child in children:
            accepted = existing.get(child["id"])
            field = "operating_context" if e["axis"] == "operating" else "thermal_phase"
            label = accepted.get("labels", {}).get(field) if accepted else None
            projected = "moving_episode" if e["axis"] == "operating" and label in {
                "moving_accelerating", "moving_decelerating", "moving_variable"} else label
            if child["review_status"] != "accepted" or projected != e["label"]:
                compatible = False
        others = [o for o in macro["episodes"] if o["axis"] == e["axis"] and o["review_status"] == "accepted"]
        if not compatible or any(overlaps(e, o) for o in others):
            unresolved.append({"layer": "macro", "id": e["id"], "reason": "conflicts with preserved child/axis decisions"})
            continue
        e.update(review_status="accepted", bulk_endorsement=bulk)
        e["review_history"].append(history.copy())
    validated = ContextAnnotationDocument.model_validate(annotation)
    validate_document(validated, duration_s=duration, source_sha256=fine["source"]["sha256"])
    SuggestionDocument.model_validate(fine)
    check_traceability(fine, macro, duration)
    if detail_hash(SuggestionDocument.model_validate(fine)) != before_detail:
        raise AnnotationValidationError("bulk acceptance changed original proposal content")
    return fine, macro, annotation, unresolved


def apply_transaction(transaction: dict, work: Path) -> dict:
    """Compare-and-swap each atomic file; retries accept only recorded before/after hashes."""
    for target in transaction["targets"]:
        stage = work / target["staged_path"]
        if digest(stage) != target["after_sha256"]:
            raise AnnotationValidationError("staged output hash changed")
        if digest(Path(target["path"])) not in {target["before_sha256"], target["after_sha256"]}:
            raise AnnotationValidationError(f"concurrent/later review change preserved: {target['path']}")
    written = 0
    for target in transaction["targets"]:
        path = Path(target["path"])
        current = digest(path)
        if current == target["after_sha256"]:
            continue
        if current != target["before_sha256"]:
            raise AnnotationValidationError(f"concurrent review change preserved: {path}")
        atomic_write_json(path, read_json(work / target["staged_path"]))
        if digest(path) != target["after_sha256"]:
            raise AnnotationValidationError("persisted output SHA mismatch")
        written += 1
    return {"files_written_this_run": written, "all_targets_verified": True,
            "unresolved": transaction["unresolved"], "approval_id": transaction["approval"]["approval_id"]}


def endorse_campaign(corpus_manifest: Path) -> dict:
    manifest, _, _ = verify_attestation(corpus_manifest)
    service = ContextAnnotationService(corpus_manifest=corpus_manifest)
    campaign = corpus_manifest.resolve().parent
    approval_path = campaign / "provenance/operator_context_approval.v1.json"
    work = campaign / "provenance/bulk_endorsement.v1"
    work.mkdir(parents=True, exist_ok=True)
    with (work / "operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        transaction_path = work / "transaction.json"
        transaction = read_json(transaction_path)
        if transaction is None:
            if approval_path.exists():
                raise AnnotationValidationError("approval exists without recovery transaction; refusing new approval")
            inventory, inputs = {}, {}
            for e in manifest.sessions:
                loaded, fine, annotation = service.review._current(e.session_id)
                macro = service.macro_review.document(e.session_id)
                if macro is None:
                    raise AnnotationValidationError("approved macro layer is missing")
                paths = {"fine": service.storage.suggestion_path(e.session_id), "macro": service.macro_review.path(e.session_id),
                         "annotation": service.storage.annotation_path(e.session_id)}
                inventory[e.session_id] = {"source_sha256": e.source_sha256, "suggestion_sha256": digest(paths["fine"]),
                                          "macro_sha256": digest(paths["macro"]), "annotation_sha256": digest(paths["annotation"]),
                                          "rule_version": fine.rule_version, "rule_config_sha256": fine.config_sha256,
                                          "suggestion_schema": fine.schema_version, "taxonomy_version": fine.taxonomy_version,
                                          "macro_rule_version": macro.rule_version, "macro_schema": macro.schema_version}
                inputs[e.session_id] = (loaded, paths, read_json(paths["fine"]), read_json(paths["macro"]),
                                        read_json(paths["annotation"]) or annotation.model_dump(mode="json"))
            approval = {"schema_version": "context-operator-approval-v1", "dataset_id": manifest.dataset_id,
                        "approval_source": "data_collector", "approval_type": "bulk_operator_endorsement",
                        "approval_scope": "all_18_pinned_sessions", "approval_method": "inspected_auto_label_results",
                        "individual_interval_review": False, "reviewed_at": utc_now_iso(),
                        "input_source_hashes": {sid: row["source_sha256"] for sid, row in inventory.items()},
                        "approved_rule_versions": {sid: {k: v for k, v in row.items() if k not in {
                            "source_sha256", "suggestion_sha256", "macro_sha256", "annotation_sha256"}} for sid, row in inventory.items()},
                        "approved_suggestion_hashes": {sid: {k: row[k] for k in ("suggestion_sha256", "macro_sha256")} for sid, row in inventory.items()},
                        "corpus_manifest_sha256": file_sha256(corpus_manifest),
                        "declaration": "The original data collector inspected Studio auto-label results and explicitly endorsed the current suggestions across all 18 pinned captures as sufficiently plausible for intended context-label research.",
                        "limitations": ["Not individual interval/boundary verification", "Not independent expert validation",
                                        "No measured segmentation accuracy", "Not confirmed fault diagnosis"],
                        "recovery_directory": str(work)}
            approval["approval_id"] = manifest.dataset_id + ":bulk-endorsement:" + stable_hash(approval)
            endorsement = BulkEndorsement(approval_id=approval["approval_id"], approval_record_sha256=json_sha(approval), reviewed_at=approval["reviewed_at"])
            targets, unresolved, backups = [], [], []
            for sid, (loaded, paths, fine, macro, annotation) in inputs.items():
                fine, macro, annotation, issues = plan_session(fine=fine, macro=macro, annotation=annotation,
                                                               duration=loaded.metadata.duration_s, endorsement=endorsement)
                unresolved.extend({"session_id": sid, **issue} for issue in issues)
                for kind, payload in (("annotation", annotation), ("fine", fine), ("macro", macro)):
                    path = paths[kind].resolve()
                    before = digest(path)
                    if before:
                        backup = work / "before" / path.name
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(path, backup)
                        if digest(backup) != before:
                            raise AnnotationValidationError("review inputs changed during backup")
                        backups.append({"path": str(backup.relative_to(work)), "sha256": before})
                    stage = work / "after" / path.name
                    atomic_write_json(stage, payload)
                    targets.append({"path": str(path), "staged_path": str(stage.relative_to(work)),
                                    "before_sha256": before, "after_sha256": digest(stage)})
            transaction = {"schema_version": "context-bulk-transaction-v1", "approval": approval,
                           "targets": targets, "backups": backups, "unresolved": unresolved}
            atomic_write_json(transaction_path, transaction)
        approval = transaction["approval"]
        identity = {k: v for k, v in approval.items() if k != "approval_id"}
        if approval["approval_id"] != manifest.dataset_id + ":bulk-endorsement:" + stable_hash(identity):
            raise AnnotationValidationError("approval content identity mismatch")
        if approval["corpus_manifest_sha256"] != file_sha256(corpus_manifest) or approval["input_source_hashes"] != {
            e.session_id: e.source_sha256 for e in manifest.sessions}:
            raise AnnotationValidationError("approval scope/source hashes changed")
        for backup in transaction["backups"]:
            if digest(work / backup["path"]) != backup["sha256"]:
                raise AnnotationValidationError("recovery/approved input SHA mismatch")
        for sid, approved in approval["approved_suggestion_hashes"].items():
            for suffix, key in (("suggestions.json", "suggestion_sha256"), ("macro.json", "macro_sha256")):
                if digest(work / "before" / f"{sid}.{suffix}") != approved[key]:
                    raise AnnotationValidationError("approved suggestion SHA mismatch")
        if approval_path.exists() and read_json(approval_path) != approval:
            raise AnnotationValidationError("versioned approval record changed")
        if not approval_path.exists():
            atomic_write_json(approval_path, approval)
        if file_sha256(approval_path) != json_sha(approval):
            raise AnnotationValidationError("approval record SHA mismatch")
        result = apply_transaction(transaction, work)
        verify_attestation(corpus_manifest)
        result.update(approval_path=str(approval_path), approval_sha256=file_sha256(approval_path),
                      recovery_directory=str(work))
        atomic_write_json(work / "completion.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--operator-authorized", action="store_true", required=True,
                        help="Explicit collector authorization is required; stop Studio writers first")
    args = parser.parse_args()
    try:
        print(json.dumps(endorse_campaign(args.corpus_manifest), indent=2))
    except (ValueError, BlockingIOError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
