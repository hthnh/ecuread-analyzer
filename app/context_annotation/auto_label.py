"""Preview context suggestions for a pinned corpus; never write annotations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.context_annotation.auto_labeler import RuleConfig
from app.context_annotation.service import ContextAnnotationService
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso


def batch_preview(corpus_manifest: Path, output: Path, *, raw_source_map: Path | None = None,
                  config: RuleConfig | None = None) -> dict:
    service = ContextAnnotationService(corpus_manifest=corpus_manifest, suggestions_dir=output, raw_source_map=raw_source_map)
    records = []
    for source in service._sources():
        try:
            payload = service.review.generate(source.source_id, config)
            record = {"session_id": source.source_id, "status": "successful", "source_sha256": source.sha256,
                      "suggestion_path": str(service.storage.suggestion_path(source.source_id)),
                      "speed_availability": payload["signal_provenance"]["status"],
                      "partial_evidence": payload["metrics"]["partial_evidence"],
                      "pending_human_review": payload["pending_count"], "metrics": payload["metrics"]}
        except (OSError, ValueError, KeyError) as exc:
            record = {"session_id": source.source_id, "status": "failed", "error": str(exc), "pending_human_review": 0}
        records.append(record)
        print(f"{source.source_id}: {record['status']}", flush=True)
    payload = {"schema_version": "context-batch-preview-v2", "generated_at": utc_now_iso(),
               "rule_version": "ctx2-rules-v1", "corpus_manifest_sha256": file_sha256(corpus_manifest),
               "total_corpus_entries": len(records), "successful_processing": sum(r["status"] == "successful" for r in records),
               "failed_processing": sum(r["status"] == "failed" for r in records),
               "unavailable_speed": sum(r.get("speed_availability") == "unavailable" for r in records),
               "partial_evidence": sum(r.get("partial_evidence", False) for r in records),
               "pending_human_review": sum(r["pending_human_review"] for r in records),
               "verified_coverage_added_s": 0.0, "sessions": records}
    atomic_write_json(output / "batch_preview.json", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-source-map", type=Path)
    parser.add_argument("--thresholds", type=Path, help="JSON object of RuleConfig overrides")
    args = parser.parse_args()
    thresholds = json.loads(args.thresholds.read_text()) if args.thresholds else {}
    payload = batch_preview(args.corpus_manifest, args.output, raw_source_map=args.raw_source_map, config=RuleConfig(**thresholds))
    print(json.dumps({key: value for key, value in payload.items() if key != "sessions"}, indent=2))
    if payload["failed_processing"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
