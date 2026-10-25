from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.context_annotation.freeze_dataset import freeze_dataset
from app.context_annotation.service import AnnotationOverlapError, AnnotationValidationError, ContextAnnotationService
from app.context_annotation.storage import AnnotationStorage, atomic_write_json, file_sha256, source_tree_sha256
from app.context_annotation.web import create_app
from app.ecu.profiles.honda_keihin_71_17 import expected_checksum_byte


def native_frame(*, rpm: int = 1500, tps_raw: int = 2, battery_raw: int = 0x7F) -> list[int]:
    frame = [
        0x02,
        0x18,
        0x71,
        0x17,
        (rpm >> 8) & 0xFF,
        rpm & 0xFF,
        0x1A,
        tps_raw,
        0x00,
        0x00,
        battery_raw,
        0x4D,
        0x50,
        0x6A,
        0x7F,
        0x01,
        0x23,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
    ]
    frame[-1] = expected_checksum_byte(frame[:-1])
    return frame


def raw_hex(frame: list[int]) -> str:
    return "".join(f"{byte:02X}" for byte in frame)


def write_native_jsonl(path: Path, *, frames: int = 5) -> None:
    rows = []
    for index in range(frames):
        rows.append(
            {
                "elapsed_ms": index * 250,
                "raw_hex": raw_hex(native_frame(rpm=1500 + index * 100, tps_raw=2 + index)),
            }
        )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def make_service(tmp_path: Path) -> tuple[ContextAnnotationService, Path, Path, Path]:
    data_dir = tmp_path / "data"
    input_dir = data_dir / "context_annotation" / "input"
    annotations_dir = data_dir / "context_annotation" / "annotations"
    exports_dir = data_dir / "context_annotation" / "exports"
    input_dir.mkdir(parents=True)
    settings = Settings(data_dir=data_dir, model_dir=data_dir / "models")
    storage = AnnotationStorage(
        data_dir=data_dir,
        input_dir=input_dir,
        annotations_dir=annotations_dir,
        exports_dir=exports_dir,
    )
    return ContextAnnotationService(settings=settings, storage=storage), input_dir, annotations_dir, exports_dir


def segment(start: float, end: float, *, engine: str = "running") -> dict:
    return {
        "start_s": start,
        "end_s": end,
        "labels": {
            "engine": engine,
            "throttle": "low",
            "dynamics": "steady",
            "thermal": "warming",
        },
        "notes": "",
    }


def test_session_discovery_parses_native_input_and_omits_map(tmp_path: Path) -> None:
    service, input_dir, _, _ = make_service(tmp_path)
    write_native_jsonl(input_dir / "session_001.jsonl")

    listing = service.list_sessions()

    assert listing["summary"]["total_sessions"] == 1
    session = listing["sessions"][0]
    assert session["status"] == "unstarted"
    assert session["parse_status"] == "ok"
    assert session["sample_count"] == 5
    assert session["duration_s"] == pytest.approx(1.0)
    assert "map" not in json.dumps(session["available_signals"]).lower()
    assert session["available_signals"]["rpm"] == 5
    assert session["available_signals"]["tps_voltage"] == 5
    assert session["available_signals"]["tps_raw"] == 5


def test_malformed_and_unsupported_sessions_are_visible(tmp_path: Path) -> None:
    service, input_dir, _, _ = make_service(tmp_path)
    (input_dir / "bad.jsonl").write_text("{not json}\n", encoding="utf-8")
    (input_dir / "capture.bin").write_bytes(b"\x00\x01")

    sessions = {item["filename"]: item for item in service.list_sessions()["sessions"]}

    assert sessions["bad.jsonl"]["status"] == "parse_error"
    assert sessions["capture.bin"]["status"] == "unsupported"


def test_annotation_crud_overlap_progress_and_source_changed(tmp_path: Path) -> None:
    service, input_dir, annotations_dir, _ = make_service(tmp_path)
    source_path = input_dir / "session_001.jsonl"
    write_native_jsonl(source_path)

    document = service.add_segment("session_001", segment(0.0, 0.5, engine="unknown"))
    assert document.segments[0].labels.engine == "unknown"
    assert service.list_sessions()["sessions"][0]["annotation_coverage"] == pytest.approx(0.5)

    with pytest.raises(AnnotationOverlapError):
        service.add_segment("session_001", segment(0.25, 0.75), overlap_strategy="reject")

    document = service.add_segment("session_001", segment(0.25, 0.75), overlap_strategy="replace")
    assert [(item.start_s, item.end_s) for item in document.segments] == [(0.0, 0.25), (0.25, 0.75)]

    document = service.update_segment("session_001", "ctx_002", segment(0.25, 1.0), overlap_strategy="replace")
    assert service.list_sessions()["sessions"][0]["status"] == "complete"

    document = service.delete_segment("session_001", "ctx_002")
    assert len(document.segments) == 1

    sidecar = annotations_dir / "session_001.context.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "context-annotation-v1"
    assert "samples" not in payload
    assert not list(annotations_dir.glob("*.tmp"))

    source_path.write_text(source_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert service.list_sessions()["sessions"][0]["status"] == "source_changed"
    with pytest.raises(AnnotationValidationError, match="SOURCE CHANGED"):
        service.save_document("session_001", document)


def test_validation_manifest_and_unknown_vs_unannotated_export(tmp_path: Path) -> None:
    service, input_dir, _, exports_dir = make_service(tmp_path)
    write_native_jsonl(input_dir / "session_001.jsonl")

    with pytest.raises(AnnotationValidationError):
        service.add_segment("session_001", segment(0.0, 2.0))

    service.add_segment(
        "session_001",
        {
            "start_s": 0.0,
            "end_s": 0.5,
            "labels": {
                "engine": "unknown",
                "throttle": "unknown",
                "dynamics": "unknown",
                "thermal": "unknown",
            },
            "notes": "explicit unknown",
        },
    )
    manifest = service.generate_manifest()
    assert manifest["corpus_summary"]["partially_annotated"] == 1
    assert (exports_dir / "context_dataset_manifest.json").exists()

    result = service.export_context_records()
    rows = [
        json.loads(line)
        for line in Path(result["path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[0]["context_engine"] == "unknown"
    assert rows[-1]["context_engine"] is None


def test_web_smoke_saves_and_reloads_without_modifying_source(tmp_path: Path) -> None:
    service, input_dir, _, _ = make_service(tmp_path)
    source_path = input_dir / "session_001.jsonl"
    write_native_jsonl(source_path)
    before = file_sha256(source_path)
    client = TestClient(create_app(service))

    assert client.get("/context-annotation").status_code == 200
    assert client.get("/context-annotation/session/session_001").status_code == 200
    telemetry = client.get("/context-annotation/api/sessions/session_001/telemetry").json()
    assert telemetry["signals"][0]["points"]

    response = client.post(
        "/context-annotation/api/sessions/session_001/segments",
        json=segment(0.0, 0.5),
    )

    assert response.status_code == 200
    assert file_sha256(source_path) == before
    reloaded = client.get("/context-annotation/api/sessions/session_001").json()
    assert reloaded["annotation"]["segments"][0]["labels"]["engine"] == "running"


def write_curated_manifest(path: Path, source_path: Path, *, session_id: str = "session_001") -> None:
    atomic_write_json(
        path,
        {
            "schema_version": "context-curated-corpus-v1",
            "dataset_id": "ecu-context-v1",
            "independent_captured_sessions": 1,
            "source_root": str(source_path.parent),
            "created_at": "2026-10-09T00:00:00+00:00",
            "selection_rule": "test fixture",
            "excluded_sessions": [],
            "sessions": [
                {
                    "session_id": session_id,
                    "source_path": str(source_path),
                    "source_sha256": source_tree_sha256(source_path),
                    "reason_included": "test curated source",
                    "origin": {"source_type": "test"},
                }
            ],
        },
    )


def test_manifest_mode_pins_sessions_and_reports_missing_or_mismatched_sources(tmp_path: Path) -> None:
    service, input_dir, _, _ = make_service(tmp_path)
    curated = input_dir / "curated.jsonl"
    extra = input_dir / "extra.jsonl"
    write_native_jsonl(curated)
    write_native_jsonl(extra)
    manifest_path = tmp_path / "curated_corpus.json"
    write_curated_manifest(manifest_path, curated)

    manifest_service = ContextAnnotationService(
        settings=service.settings,
        storage=service.storage,
        corpus_manifest=manifest_path,
    )
    listing = manifest_service.list_sessions()
    assert [item["source_id"] for item in listing["sessions"]] == ["session_001"]
    assert listing["sessions"][0]["parse_status"] == "ok"

    curated.write_text(curated.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    listing = manifest_service.list_sessions()
    assert listing["sessions"][0]["status"] == "source_hash_mismatch"

    curated.unlink()
    listing = manifest_service.list_sessions()
    assert listing["sessions"][0]["status"] == "source_missing"


def test_campaign_status_separates_explicit_unknown_from_unannotated(tmp_path: Path) -> None:
    service, input_dir, _, exports_dir = make_service(tmp_path)
    source = input_dir / "session_001.jsonl"
    write_native_jsonl(source)
    manifest_path = tmp_path / "curated_corpus.json"
    write_curated_manifest(manifest_path, source)
    service = ContextAnnotationService(settings=service.settings, storage=service.storage, corpus_manifest=manifest_path)

    service.add_segment(
        "session_001",
        {
            "start_s": 0.0,
            "end_s": 0.5,
            "labels": {
                "engine": "unknown",
                "throttle": "unknown",
                "dynamics": "unknown",
                "thermal": "unknown",
            },
            "notes": "",
        },
    )
    status = service.generate_campaign_status()

    assert status["total_curated_sessions"] == 1
    assert status["partial"] == 1
    assert status["explicitly_unknown_duration_s"] == pytest.approx(0.5)
    assert status["truly_unannotated_duration_s"] == pytest.approx(0.5)
    assert (exports_dir / "annotation_campaign_status.json").exists()


def test_freeze_dataset_writes_reproducible_fingerprint(tmp_path: Path) -> None:
    service, input_dir, annotations_dir, _ = make_service(tmp_path)
    source = input_dir / "session_001.jsonl"
    write_native_jsonl(source)
    manifest_path = tmp_path / "curated_corpus.json"
    write_curated_manifest(manifest_path, source)
    service = ContextAnnotationService(settings=service.settings, storage=service.storage, corpus_manifest=manifest_path)
    service.add_segment(
        "session_001",
        {
            "start_s": 0.0,
            "end_s": 1.0,
            "labels": {
                "engine": "running",
                "throttle": "low",
                "dynamics": "steady",
                "thermal": "warming",
            },
            "notes": "",
        },
    )

    first = freeze_dataset(
        corpus_manifest=manifest_path,
        output=tmp_path / "freeze-a",
        data_dir=service.settings.data_dir,
        annotations_dir=annotations_dir,
        created_at="2026-10-09T00:00:00+00:00",
    )
    second = freeze_dataset(
        corpus_manifest=manifest_path,
        output=tmp_path / "freeze-b",
        data_dir=service.settings.data_dir,
        annotations_dir=annotations_dir,
        created_at="2026-10-09T00:00:00+00:00",
    )

    assert first["content_fingerprint_sha256"] == second["content_fingerprint_sha256"]
    assert first["independent_captured_sessions"] == 1
    assert first["freeze_complete"] is True
    assert (tmp_path / "freeze-a" / "dataset_manifest.json").exists()
