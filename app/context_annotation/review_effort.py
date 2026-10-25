"""Read-only pilot effort measurements; descriptions are review priorities, not labels."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from app.context_annotation.attestation import verify_attestation
from app.context_annotation.pilot_quality import coalesce, distribution, oscillations
from app.context_annotation.raw_campaign import DEFAULT_CAMPAIGN
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso


PILOT = {"session_000002": "thermal transition", "session_000004": "urban stop-and-go",
         "session_000005": "thermally stable operation", "session_000007": "intercity riding",
         "session_000018": "long uninterrupted capture"}


def review_statistics(items: list[dict], annotations: list[dict]) -> dict:
    authoritative = {s["suggestion_id"]: s for s in annotations if s.get("suggestion_id")}
    items = [{**item, **{key: authoritative[item["id"]][key] for key in ("start_s", "end_s", "human_confirmation")}}
             if item["id"] in authoritative else item for item in items]
    counts = Counter(item["review_status"] for item in items)
    corrected = [item for item in items if any(h["action"] == "corrected" and h.get("changes") for h in item["review_history"])
                 or (item.get("human_confirmation") or {}).get("corrections")]
    corrections = []
    for item in items:
        original = item["original_proposal"]
        start = item["start_s"] - original["start_s"]
        end = item["end_s"] - original["end_s"]
        if abs(start) > 1e-9 or abs(end) > 1e-9:
            corrections.append({"suggestion_id": item["id"], "start_delta_s": start, "end_delta_s": end,
                                "absolute_boundary_change_s": abs(start) + abs(end)})
    measurements, unmeasured = {}, 0
    for item in items:
        for action in item["review_history"]:
            if action.get("review_time_s") is None:
                unmeasured += 1
            else:
                # One multi-item decision has one action ID, not N independent time measurements.
                key = action.get("action_id") or (item["id"], action["at"], action["action"])
                measurements[key] = action["review_time_s"]
    return {**{status: counts[status] for status in ("pending", "accepted", "rejected", "uncertain")},
            "corrected": len(corrected), "corrected_is_not_a_final_decision": True,
            "accepted_annotation_items": len(annotations),
            "manual_review_time_s": sum(measurements.values()) if measurements else None,
            "manual_review_time_basis": "client-visible dialog elapsed; self-reported, not independently verified" if measurements else "not_measured" if unmeasured else "not_measured_no_human_review_recorded",
            "timed_actions": len(measurements), "unmeasured_history_entries": unmeasured,
            "boundary_corrections": corrections,
            "boundary_correction_magnitude": distribution([c["absolute_boundary_change_s"] for c in corrections])}


def audit_review_effort(manifest_path: Path) -> dict:
    manifest, record, _ = verify_attestation(manifest_path)
    service = ContextAnnotationService(corpus_manifest=manifest_path)
    sessions = []
    for sid, priority in PILOT.items():
        loaded = service.load_session(sid)
        proposals = service.review.read(sid, loaded=loaded)
        if not proposals or proposals["source_changed"] or proposals["raw_evidence_changed"]:
            raise ValueError(f"pilot suggestions unavailable or stale: {sid}")
        original = [{**s, **s["original_proposal"]} for s in proposals["segments"]]
        operating = coalesce(original, "operating_context")
        thermal = coalesce(original, "thermal_phase")
        speeds = np.asarray([s.get("vehicle_speed_kmh") if s.get("vehicle_speed_kmh") is not None else np.nan for s in loaded.samples])
        ect = np.asarray([s.get("ect_c") if s.get("ect_c") is not None else np.nan for s in loaded.samples])
        times = np.asarray(loaded.elapsed_s)
        repeated = oscillations(operating, times, speeds, 8, 40, 3)
        thermal_repeated = oscillations(thermal, times, ect, 8, 40, 2)
        document = service.annotation_for(loaded)
        items = proposals["segments"] + proposals["events"]
        samples = []
        # Include the first proposal, a thermal change, a long operating run and a throttle burst for inspection.
        choices = [original[0]] if original else []
        choices += [s for i, s in enumerate(original) if i and s["thermal_phase"] != original[i-1]["thermal_phase"]][:2]
        choices += sorted(original, key=lambda s: s["end_s"]-s["start_s"], reverse=True)[:1]
        choices += [s for s in proposals["events"] if s["event"] == "throttle_burst"][:1]
        for item in {s["id"]: s for s in choices}.values():
            samples.append({key: item.get(key) for key in ("id", "start_s", "end_s", "operating_context", "thermal_phase", "event", "evidence", "evidence_quality", "reason")})
        sessions.append({"session_id": sid, "review_priority_only": priority, "source_sha256": loaded.metadata.source.sha256,
                         "sample_count": loaded.metadata.sample_count, "duration_s": loaded.metadata.duration_s,
                         "operating_proposal_count": len(original), "operating_axis_run_count": len(operating),
                         "joint_proposal_duration": distribution([s["end_s"]-s["start_s"] for s in original]),
                         "operating_segment_duration": distribution([s["end_s"]-s["start_s"] for s in operating]),
                         "thermal_axis_run_count": len(thermal), "event_marker_count": len(proposals["events"]),
                         "event_markers_by_type": dict(Counter(e["event"] for e in proposals["events"])),
                         "speed_evidence": loaded.metadata.research_signal_provenance,
                         "review": review_statistics(items, [s.model_dump(mode="json") for s in document.segments + document.events]),
                         "repeated_operating_oscillations": repeated, "repeated_thermal_oscillations": thermal_repeated,
                         "repeated_operating_oscillation_count": len(repeated),
                         "low_amplitude_operating_oscillation_count": sum(o["low_amplitude"] for o in repeated),
                         "repeated_thermal_oscillation_count": len(thermal_repeated),
                         "representative_review_items": samples})
    return {"schema_version": "ctx23-review-effort-v1", "generated_at": utc_now_iso(), "dataset_id": manifest.dataset_id,
            "corpus_manifest_sha256": file_sha256(manifest_path), "attestation_id": record["attestation_id"],
            "human_context_review_completed": all(s["review"]["pending"] == s["review"]["uncertain"] == 0 for s in sessions),
            "limitations": ["Pilot priority descriptions are not time-specific ground truth or accepted labels.",
                            "Operating proposals intersect thermal boundaries; coalesced operating runs are reported separately.",
                            "Manual review duration is unknown until measured human actions; browser automation is excluded from campaign measurements.",
                            "Oscillation: contiguous A-B-A, middle <=8s, full span <=40s; low-amplitude speed range <=3 km/h, ECT <=2C."],
            "sessions": sessions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, default=DEFAULT_CAMPAIGN / "corpus_manifest.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.corpus_manifest.parent / "exports/ctx23_review_effort.json"
    payload = audit_review_effort(args.corpus_manifest)
    atomic_write_json(output, payload)
    print(output)


if __name__ == "__main__":
    main()
