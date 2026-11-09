"""ASGI middleware: request ids and access logs, response security headers, request body size limits."""

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id

# Interactive API docs load Swagger UI / ReDoc assets from a CDN, so they get no CSP.
_DOCS_PATHS = ("/docs", "/redoc")

KIB = 1024
DEFAULT_BODY_LIMIT = 1024 * KIB
BODY_LIMITS = {
    "/webhooks/": 256 * KIB,  # Razorpay events are a few KiB
    "/customers/import": 4096 * KIB,  # a 2 MB CSV, JSON-escaped
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, hsts: bool) -> None:
        self.app = app
        self.headers = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"cache-control", b"no-store"),
        ]
        if hsts:
            self.headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = list(self.headers)
        if not scope["path"].startswith(_DOCS_PATHS):
            extra.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = [*message.get("headers", []), *(h for h in extra if h[0] not in present)]
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Refuse oversized request bodies with 413, whether or not Content-Length is declared."""

    def __init__(
        self, app: ASGIApp, default_limit: int = DEFAULT_BODY_LIMIT, limits: Mapping[str, int] = BODY_LIMITS
    ) -> None:
        self.app = app
        self.default_limit = default_limit
        self.limits = limits

    def limit_for(self, path: str) -> int:
        return next((limit for prefix, limit in self.limits.items() if path.startswith(prefix)), self.default_limit)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope["path"])
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _too_large(send, limit)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises HTTPException from body parsing, so this becomes a 413 response.
                    raise HTTPException(status_code=413, detail=_detail(limit))
            return message

        await self.app(scope, limited_receive, send)


def _detail(limit: int) -> str:
    return f"Request body too large (limit {limit // KIB} KiB)"


async def _too_large(send: Callable[[Message], Awaitable[None]], limit: int) -> None:
    body = json.dumps({"detail": _detail(limit)}).encode()
    headers: list[Any] = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": 413, "headers": headers})
    await send({"type": "http.response.body", "body": body})


_REQUEST_ID_HEADER = b"x-request-id"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
# Paths whose last segment is a bearer credential (the customer payment link token).
_SECRET_PATH = re.compile(r"^(/pay/)[^/]+")
_access_logger = logging.getLogger("recoverx.access")


def redact_path(path: str) -> str:
    """A request path that is safe to log: the customer payment token is a credential."""
    return _SECRET_PATH.sub(r"\1[redacted]", path)


class RequestContextMiddleware:
    """Gives every request an id (the caller's X-Request-ID if well-formed, otherwise a new one),
    returns it in the response, puts it on every log line, and writes one access log entry."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(_REQUEST_ID_HEADER, b"").decode("latin-1")
        current = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid4().hex
        token = request_id.set(current)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (_REQUEST_ID_HEADER, current.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = redact_path(scope["path"])
            _access_logger.log(
                logging.DEBUG if path == "/health" else logging.INFO,
                "%s %s %s",
                scope["method"],
                path,
                status,
                extra={
                    "method": scope["method"],
                    "path": path,
                    "status": status,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id.reset(token)
