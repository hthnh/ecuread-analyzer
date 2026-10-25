from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.context_annotation.models import (
    ContextAnnotationDocument, ContextLabels, OperatingLabels, SessionLabels,
)
from app.context_annotation.auto_labeler import RuleConfig
from app.context_annotation.service import (
    AnnotationOverlapError,
    AnnotationValidationError,
    ContextAnnotationService,
    SessionNotFoundError,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "templates" / "context_annotation"
STATIC_DIR = REPO_ROOT / "static" / "context_annotation"


class SegmentRequest(BaseModel):
    id: str | None = None
    start_s: float
    end_s: float
    labels: ContextLabels | OperatingLabels = Field(default_factory=ContextLabels)
    taxonomy_version: str = "context-taxonomy-v1"
    notes: str = ""
    overlap_strategy: str = "reject"
    reviewer: str | None = None


class AutoLabelRequest(BaseModel):
    thresholds: dict[str, float | int] = Field(default_factory=dict)


class SuggestionEditRequest(BaseModel):
    changes: dict[str, Any]
    reviewer: str = Field(min_length=1)
    review_time_s: float | None = Field(default=None, ge=0, le=3600, allow_inf_nan=False)


class SuggestionReviewRequest(BaseModel):
    suggestion_ids: list[str] = Field(default_factory=list)
    action: str = Field(pattern="^(accept|reject|uncertain)$")
    reviewer: str = Field(min_length=1)
    start_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    notes: str = ""
    review_time_s: float | None = Field(default=None, ge=0, le=3600, allow_inf_nan=False)


class EventRequest(BaseModel):
    event: str
    start_s: float
    end_s: float
    notes: str = ""
    reviewer: str | None = None


class MacroReviewRequest(BaseModel):
    action: str = Field(pattern="^(accept|reject|uncertain|correct)$")
    reviewer: str = Field(min_length=1)
    changes: dict[str, Any] = Field(default_factory=dict)
    review_time_s: float | None = Field(default=None, ge=0, le=3600, allow_inf_nan=False)


def _render_template(name: str, context: dict[str, Any]) -> HTMLResponse:
    text = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
    for key, value in context.items():
        if key.endswith("_json"):
            replacement = json.dumps(value)
        else:
            replacement = html.escape(str(value), quote=True)
        text = text.replace("{{ " + key + " }}", replacement)
    return HTMLResponse(text)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SessionNotFoundError):
        return HTTPException(status_code=404, detail=f"session {exc.args[0]} was not found")
    if isinstance(exc, AnnotationOverlapError):
        return HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "overlaps": [item.__dict__ for item in exc.overlaps],
            },
        )
    if isinstance(exc, AnnotationValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=json.loads(exc.json()))
    if isinstance(exc, ValueError | TypeError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def create_app(service: ContextAnnotationService | None = None) -> FastAPI:
    app = FastAPI(title="ECU Context Annotation Studio", version="context-annotation-v2")
    resolved_service = service or ContextAnnotationService()
    app.state.context_annotation_service = resolved_service
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/context-annotation/static", StaticFiles(directory=STATIC_DIR), name="context_annotation_static")

    def get_service(request: Request) -> ContextAnnotationService:
        return request.app.state.context_annotation_service

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/context-annotation")

    @app.get("/context-annotation", response_class=HTMLResponse, include_in_schema=False)
    def index_page() -> HTMLResponse:
        return _render_template("index.html", {"page_title": "ECU Context Annotation Studio"})

    @app.get("/context-annotation/session/{source_id}", response_class=HTMLResponse, include_in_schema=False)
    def session_page(source_id: str) -> HTMLResponse:
        return _render_template(
            "session.html",
            {
                "page_title": "Context Annotation Session",
                "source_id_json": source_id,
            },
        )

    @app.get("/context-annotation/api/sessions")
    def api_sessions(request: Request) -> dict[str, Any]:
        try:
            return get_service(request).list_sessions()
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.get("/context-annotation/api/sessions/{source_id}")
    def api_session_detail(request: Request, source_id: str) -> dict[str, Any]:
        try:
            return get_service(request).session_detail(source_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.get("/context-annotation/api/sessions/{source_id}/telemetry")
    def api_session_telemetry(
        request: Request,
        source_id: str,
        start_s: float | None = None,
        end_s: float | None = None,
        max_points: int = Query(default=1200, ge=100, le=20000),
    ) -> dict[str, Any]:
        try:
            return get_service(request).telemetry(source_id, start_s=start_s, end_s=end_s, max_points=max_points)
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.put("/context-annotation/api/sessions/{source_id}/annotation")
    def api_save_annotation(
        request: Request,
        source_id: str,
        payload: ContextAnnotationDocument,
    ) -> dict[str, Any]:
        try:
            document = get_service(request).save_document(source_id, payload)
            return document.model_dump(mode="json")
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.put("/context-annotation/api/sessions/{source_id}/session-labels")
    def api_session_labels(
        request: Request,
        source_id: str,
        payload: SessionLabels,
    ) -> dict[str, Any]:
        try:
            return get_service(request).update_session_labels(source_id, payload).model_dump(mode="json")
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/context-annotation/api/sessions/{source_id}/segments")
    def api_add_segment(
        request: Request,
        source_id: str,
        payload: SegmentRequest,
    ) -> dict[str, Any]:
        try:
            document = get_service(request).add_segment(
                source_id,
                payload.model_dump(mode="json", exclude={"overlap_strategy", "reviewer"}),
                overlap_strategy=payload.overlap_strategy,
                reviewer=payload.reviewer,
            )
            return document.model_dump(mode="json")
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.put("/context-annotation/api/sessions/{source_id}/segments/{segment_id}")
    def api_update_segment(
        request: Request,
        source_id: str,
        segment_id: str,
        payload: SegmentRequest,
    ) -> dict[str, Any]:
        try:
            document = get_service(request).update_segment(
                source_id,
                segment_id,
                payload.model_dump(mode="json", exclude={"id", "overlap_strategy", "reviewer"}),
                overlap_strategy=payload.overlap_strategy,
                reviewer=payload.reviewer,
            )
            return document.model_dump(mode="json")
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.delete("/context-annotation/api/sessions/{source_id}/segments/{segment_id}")
    def api_delete_segment(request: Request, source_id: str, segment_id: str) -> dict[str, Any]:
        try:
            return get_service(request).delete_segment(source_id, segment_id).model_dump(mode="json")
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/context-annotation/api/export/manifest")
    def api_export_manifest(request: Request) -> dict[str, Any]:
        try:
            return get_service(request).generate_manifest()
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/context-annotation/api/sessions/{source_id}/auto-label")
    def api_auto_label(request: Request, source_id: str, payload: AutoLabelRequest) -> dict[str, Any]:
        try:
            service = get_service(request)
            result = service.review.generate(source_id, RuleConfig(**payload.thresholds))
            service.macro_review.generate(source_id)
            return result
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/context-annotation/api/sessions/{source_id}/macro-generate")
    def api_macro_generate(request: Request, source_id: str) -> dict[str, Any]:
        try:
            return get_service(request).macro_review.generate(source_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.get("/context-annotation/api/sessions/{source_id}/macro/{episode_id}")
    def api_macro_inspect(request: Request, source_id: str, episode_id: str) -> dict[str, Any]:
        try:
            return get_service(request).macro_review.inspect(source_id, episode_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/context-annotation/api/sessions/{source_id}/macro/{episode_id}/review")
    def api_macro_review(request: Request, source_id: str, episode_id: str, payload: MacroReviewRequest) -> dict[str, Any]:
        try:
            return get_service(request).macro_review.decide(source_id, episode_id, **payload.model_dump())
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.get("/context-annotation/api/sessions/{source_id}/suggestions")
    def api_suggestions(request: Request, source_id: str) -> dict[str, Any]:
        try:
            service = get_service(request)
            loaded = service.load_session(source_id)
            return {"suggestions": service.review.read(source_id, loaded=loaded)}
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.patch("/context-annotation/api/sessions/{source_id}/suggestions/{suggestion_id}")
    def api_edit_suggestion(request: Request, source_id: str, suggestion_id: str,
                            payload: SuggestionEditRequest) -> dict[str, Any]:
        try:
            return get_service(request).review.edit(source_id, suggestion_id, payload.changes, payload.reviewer, review_time_s=payload.review_time_s)
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/context-annotation/api/sessions/{source_id}/suggestion-review")
    def api_review_suggestions(request: Request, source_id: str, payload: SuggestionReviewRequest) -> dict[str, Any]:
        try:
            return get_service(request).review.decide(source_id, payload.suggestion_ids, payload.action, payload.reviewer,
                                                      start_s=payload.start_s, end_s=payload.end_s, notes=payload.notes, review_time_s=payload.review_time_s)
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/context-annotation/api/sessions/{source_id}/events")
    def api_add_event(request: Request, source_id: str, payload: EventRequest) -> dict[str, Any]:
        try:
            return get_service(request).save_event(source_id, payload.model_dump(exclude={"reviewer"}), reviewer=payload.reviewer).model_dump(mode="json")
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.put("/context-annotation/api/sessions/{source_id}/events/{event_id}")
    def api_update_event(request: Request, source_id: str, event_id: str, payload: EventRequest) -> dict[str, Any]:
        try:
            return get_service(request).save_event(source_id, payload.model_dump(exclude={"reviewer"}), event_id=event_id,
                                                   reviewer=payload.reviewer).model_dump(mode="json")
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.delete("/context-annotation/api/sessions/{source_id}/events/{event_id}")
    def api_delete_event(request: Request, source_id: str, event_id: str) -> dict[str, Any]:
        try:
            return get_service(request).delete_event(source_id, event_id).model_dump(mode="json")
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/context-annotation/api/export/context-records")
    def api_export_context_records(request: Request) -> dict[str, Any]:
        try:
            return get_service(request).export_context_records()
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/context-annotation/api/export/campaign-status")
    def api_export_campaign_status(request: Request) -> dict[str, Any]:
        try:
            return get_service(request).generate_campaign_status()
        except Exception as exc:
            raise _http_error(exc) from exc

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the offline ECU context annotation studio.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--annotations-dir", default=None)
    parser.add_argument("--exports-dir", default=None)
    parser.add_argument("--corpus-manifest", default=None)
    parser.add_argument("--suggestions-dir", default=None)
    parser.add_argument("--raw-source-map", default=None)
    args = parser.parse_args()

    settings = Settings(data_dir=Path(args.data_dir), model_dir=Path(args.data_dir) / "models")
    service = ContextAnnotationService(
        settings=settings,
        input_dir=Path(args.input_dir) if args.input_dir else None,
        annotations_dir=Path(args.annotations_dir) if args.annotations_dir else None,
        exports_dir=Path(args.exports_dir) if args.exports_dir else None,
        corpus_manifest=Path(args.corpus_manifest) if args.corpus_manifest else None,
        suggestions_dir=Path(args.suggestions_dir) if args.suggestions_dir else None,
        raw_source_map=Path(args.raw_source_map) if args.raw_source_map else None,
    )
    import uvicorn

    uvicorn.run(create_app(service), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
