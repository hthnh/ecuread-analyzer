from __future__ import annotations

import copy
import hashlib
import logging
import re

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.ingestion.raw_ecu.adapter import import_raw_file
from app.services.session_processor import SessionProcessor
from app.services.session_processor import validate_raw_import_for_processing
from app.utils.ids import new_session_id


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["sessions"])
CLIENT_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_positive_optional(value: float | None, name: str) -> None:
    if value is not None and value <= 0:
        raise HTTPException(status_code=422, detail=f"{name} must be positive")


def _validated_client_session_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if CLIENT_SESSION_ID_RE.fullmatch(normalized) is None:
        raise HTTPException(
            status_code=422,
            detail="session_id must be 1-128 chars and contain only letters, numbers, '.', '_' or '-'",
        )
    return normalized


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _reused_upload_response(existing_result: dict, *, client_session_id: str, upload_sha256: str) -> dict:
    response = copy.deepcopy(existing_result)
    response["duplicate_reused"] = True
    response["idempotency"] = {
        "reused": True,
        "client_session_id": client_session_id,
        "upload_sha256": upload_sha256,
    }
    return response


async def _process_raw_upload(
    request: Request,
    file: UploadFile,
    device_id: str | None,
    vehicle_id: str | None,
    sample_interval_ms: float | None,
    sampling_rate_hz: float | None,
    session_note: str | None,
    firmware_version: str | None,
    client_session_id: str | None,
    process_with_model: bool,
) -> dict:
    settings = request.app.state.settings
    storage = request.app.state.storage
    repository = request.app.state.repository
    _validate_positive_optional(sample_interval_ms, "sample_interval_ms")
    _validate_positive_optional(sampling_rate_hz, "sampling_rate_hz")
    client_session_id = _validated_client_session_id(client_session_id)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"upload exceeds {settings.max_upload_mb} MB")

    upload_sha256 = _sha256_bytes(content)
    if client_session_id is not None:
        if device_id is None or not device_id.strip():
            raise HTTPException(status_code=422, detail="device_id is required when session_id is provided")
        existing = repository.read_upload_idempotency(device_id.strip(), client_session_id)
        if existing is not None:
            if existing.get("upload_sha256") != upload_sha256:
                raise HTTPException(
                    status_code=409,
                    detail="upload conflicts with an existing session for this device_id and session_id",
                )
            existing_session_id = existing["session_id"]
            existing_result = storage.read_result(existing_session_id)
            if existing_result is None:
                raise HTTPException(
                    status_code=409,
                    detail="idempotency record exists but the stored session result is missing",
                )
            return _reused_upload_response(
                existing_result,
                client_session_id=client_session_id,
                upload_sha256=upload_sha256,
            )

    session_id = new_session_id()
    staged_path = None
    try:
        staged_path = storage.stage_raw_upload(file.filename, content)
        raw_import = import_raw_file(
            staged_path,
            settings,
            session_id=session_id,
            device_id=device_id,
            vehicle_id=vehicle_id,
            sample_interval_ms=sample_interval_ms,
            sampling_rate_hz=sampling_rate_hz,
            session_note=session_note,
            firmware_version=firmware_version,
        )
        validate_raw_import_for_processing(raw_import)
        raw_path = storage.commit_staged_upload(session_id, file.filename, staged_path, sha256=upload_sha256)
        staged_path = None
        logger.info("upload saved", extra={"session_id": session_id, "original_filename": file.filename})
        processor = SessionProcessor(settings, storage)
        result = processor.process_import(
            session_id,
            raw_path,
            file.filename or "upload.txt",
            raw_import,
            device_id=device_id,
            vehicle_id=vehicle_id,
            sample_interval_ms=sample_interval_ms,
            sampling_rate_hz=sampling_rate_hz,
            session_note=session_note,
            firmware_version=firmware_version,
            client_session_id=client_session_id,
            upload_sha256=upload_sha256,
            process_with_model=process_with_model,
        )
        if client_session_id is not None:
            repository.write_upload_idempotency(
                device_id=device_id.strip() if device_id is not None else "",
                client_session_id=client_session_id,
                session_id=session_id,
                upload_sha256=upload_sha256,
                original_filename=file.filename,
            )
            result["duplicate_reused"] = False
            result["idempotency"] = {
                "reused": False,
                "client_session_id": client_session_id,
                "upload_sha256": upload_sha256,
            }
            storage.write_result(session_id, result)
        return result
    except ValueError as exc:
        if staged_path is not None:
            storage.cleanup_staged_upload(staged_path)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sessions")
async def upload_session(
    request: Request,
    file: UploadFile = File(...),
    device_id: str | None = Form(default=None),
    vehicle_id: str | None = Form(default=None),
    sample_interval_ms: float | None = Form(default=None),
    sampling_rate_hz: float | None = Form(default=None),
    session_note: str | None = Form(default=None),
    firmware_version: str | None = Form(default=None),
    session_id: str | None = Form(default=None),
    process_with_model: bool = Form(default=True),
) -> dict:
    return await _process_raw_upload(
        request,
        file,
        device_id,
        vehicle_id,
        sample_interval_ms,
        sampling_rate_hz,
        session_note,
        firmware_version,
        session_id,
        process_with_model,
    )


@router.post("/import/raw-session")
async def import_raw_session(
    request: Request,
    file: UploadFile = File(...),
    device_id: str | None = Form(default=None),
    vehicle_id: str | None = Form(default=None),
    sample_interval_ms: float | None = Form(default=None),
    sampling_rate_hz: float | None = Form(default=None),
    session_note: str | None = Form(default=None),
    firmware_version: str | None = Form(default=None),
    session_id: str | None = Form(default=None),
    process_with_model: bool = Form(default=True),
) -> dict:
    return await _process_raw_upload(
        request,
        file,
        device_id,
        vehicle_id,
        sample_interval_ms,
        sampling_rate_hz,
        session_note,
        firmware_version,
        session_id,
        process_with_model,
    )
