"""Translate business exceptions into HTTP responses (``{"detail": ...}``)."""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.middleware import redact_path
from app.core.crypto import CredentialKeyError
from app.domain.exceptions import (
    AuthenticationError,
    ConflictError,
    ExpiredError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    RateLimitedError,
    RecoverXError,
)

logger = logging.getLogger(__name__)

_STATUS_CODES: dict[type[RecoverXError], int] = {
    InvalidRequestError: status.HTTP_400_BAD_REQUEST,
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    ExpiredError: status.HTTP_410_GONE,
    RateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    ProviderError: status.HTTP_502_BAD_GATEWAY,
}


def status_code_for(exc: RecoverXError) -> int:
    for cls in type(exc).__mro__:
        if cls in _STATUS_CODES:
            return _STATUS_CODES[cls]
    return status.HTTP_400_BAD_REQUEST


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RecoverXError)
    async def handle_business_error(request: Request, exc: RecoverXError) -> JSONResponse:
        code = status_code_for(exc)
        if code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.warning("%s %s failed upstream: %s", request.method, redact_path(request.url.path), exc)
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    @app.exception_handler(CredentialKeyError)
    async def handle_credential_key_error(request: Request, exc: CredentialKeyError) -> JSONResponse:
        # Misconfiguration, not a client error: say what to set, never the key material.
        logger.error("%s %s: %s", request.method, redact_path(request.url.path), exc)
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo submitted values back: they can be passwords or API secrets.
        errors = [{key: error[key] for key in ("type", "loc", "msg") if key in error} for error in exc.errors()]
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": errors})

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        if _is_unique_violation(exc):
            # Two requests raced past an idempotency check; the database kept only one.
            logger.info("%s %s lost a race on a unique key", request.method, redact_path(request.url.path))
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": "Another request changed this at the same time. Reload and try again."},
            )
        logger.exception("Integrity error on %s %s", request.method, redact_path(request.url.path), exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, redact_path(request.url.path), exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def _is_unique_violation(exc: IntegrityError) -> bool:
    """PostgreSQL reports SQLSTATE 23505; SQLite says so in the message."""
    original = exc.orig
    return getattr(original, "sqlstate", None) == "23505" or "UNIQUE constraint failed" in str(original)
