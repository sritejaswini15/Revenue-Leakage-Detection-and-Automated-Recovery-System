"""RecoverX API: revenue recovery control plane for Razorpay merchants.

Run locally with ``uvicorn app.main:app --reload`` from the ``backend`` directory.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from app.api.routes import api_router
from app.application.bootstrap import run_startup_tasks
from app.core.config import settings
from app.core.observability import setup_process
from app.infrastructure.database.session import SessionLocal, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings.validate_production()
    if not settings.is_production:
        init_db()  # production schema is managed by `alembic upgrade head`
    with SessionLocal() as session:
        run_startup_tasks(session)
    logger.info("RecoverX API ready (environment=%s, payments=%s)", settings.ENVIRONMENT, settings.PAYMENT_PROVIDER)
    yield


def create_app() -> FastAPI:
    setup_process()
    application = FastAPI(
        title="RecoverX API",
        description="Revenue recovery control plane for Razorpay merchants.",
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )
    application.add_middleware(BodySizeLimitMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    application.add_middleware(RequestContextMiddleware)  # outermost: every response gets an id
    register_error_handlers(application)
    application.include_router(api_router)
    return application


app = create_app()
