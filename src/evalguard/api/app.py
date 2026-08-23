"""FastAPI application factory and process-level setup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from evalguard.api.health import router as health_router
from evalguard.api.reports import router as report_router
from evalguard.api.routes import router as api_router
from evalguard.core.config import get_settings
from evalguard.core.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(api_router)
    application.include_router(report_router)
    return application


app = create_app()
