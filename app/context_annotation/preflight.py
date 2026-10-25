"""Rebuild the explicit RAW provenance audit without modifying source telemetry."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.context_annotation.corpus import manifest_sources
from app.context_annotation.session_loader import load_source
from app.context_annotation.speed import aligned_timestamp_field, read_raw_capture, sample_signature
from app.context_annotation.storage import atomic_write_json, file_sha256, utc_now_iso


def audit_raw_sources(corpus_manifest: Path, raw_root: Path, output: Path) -> dict:
    originals = []
    index = defaultdict(list)
    for path in sorted(raw_root.rglob("*.jsonl")):
        if "real_run" not in path.parts and path.name != "raw_frames.jsonl":
            continue
        sha = file_sha256(path)
        capture = read_raw_capture(str(path), sha)
        number = len(originals)
        originals.append((path, sha, capture))
        for offset, signature in enumerate(capture.signatures):
            if signature is not None:
                index[signature].append((number, offset))
    entries = []
    for source in manifest_sources(corpus_manifest):
        loaded = load_source(source)
        if loaded.metadata.status != "ok":
            raise ValueError(f"preflight source error: {source.source_id}: {loaded.metadata.error}")
        signatures = [sample_signature(sample) for sample in loaded.samples]
        matches = []
        for number, offset in index.get(signatures[0], []) if signatures else []:
            path, sha, capture = originals[number]
            if capture.signatures[offset:offset + len(signatures)] == signatures:
                field = aligned_timestamp_field(capture, offset, loaded.samples)
                if field:
                    matches.append({"raw_source_path": str(path), "raw_source_sha256": sha,
                                    "sample_offset": offset, "timestamp_field": field})
        entry = {"session_id": source.source_id, "canonical_sha256": source.sha256,
                 "sample_count": len(signatures), "matches_found": len(matches),
                 "raw_source_path": None, "status": "unavailable"}
        if len(matches) == 1:
            entry.update(matches[0])
            entry["status"] = "verified"
        entry["reason"] = ("Unique full production-signal/reserved-byte sequence and timestamp-interval match."
                           if len(matches) == 1 else "No unique verified accessible original RAW range; speed unavailable.")
        entries.append(entry)
    payload = {"schema_version": "context-raw-source-map-v1", "generated_at": utc_now_iso(),
               "corpus_manifest_sha256": file_sha256(corpus_manifest),
               "corpus_entries": len(entries), "verified_raw_entries": sum(e["status"] == "verified" for e in entries),
               "accessible_raw_files": len(originals), "sessions": entries,
               "independent_captured_sessions_verified": None,
               "independence_caveat": "Pinned entries include aliases, capture slices, and synthetic records."}
    atomic_write_json(output, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-manifest", type=Path, default=Path("data/context_annotation/curated_corpus.json"))
    parser.add_argument("--raw-root", type=Path, default=Path("real_data"))
    parser.add_argument("--output", type=Path, default=Path("data/context_annotation/raw_signal_sources.json"))
    args = parser.parse_args()
    payload = audit_raw_sources(args.corpus_manifest, args.raw_root, args.output)
    print(json.dumps({key: value for key, value in payload.items() if key != "sessions"}, indent=2))


if __name__ == "__main__":
    main()
