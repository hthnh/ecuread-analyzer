from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import analysis, health, model, raw, sessions, uploads
from app.config import Settings, get_settings
from app.repositories.file_repository import FileBackedRepository
from app.services.storage import StorageService


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    resolved_settings = settings or get_settings()
    storage = StorageService(resolved_settings)
    repository = FileBackedRepository(resolved_settings)
    storage.repository = repository

    app = FastAPI(title="ECU Read Analyzer", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.storage = storage
    app.state.repository = repository

    if resolved_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(uploads.router)
    app.include_router(sessions.router)
    app.include_router(analysis.router)
    app.include_router(raw.router)
    app.include_router(model.router)
    return app


app = create_app()
