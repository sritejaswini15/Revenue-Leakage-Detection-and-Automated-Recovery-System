"""Process-wide logging configuration.

``LOG_FORMAT=json`` (the production default) writes one JSON object per line, ready for a log
pipeline; ``text`` is easier to read locally. Every record carries the current request id, and
any ``extra={...}`` fields a caller passes (case_id, customer_id, workflow_id, action, status,
latency_ms, error...) become top-level keys, so one request or case can be followed end to end.
"""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_TEXT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(request_id)s %(message)s"

# Set per HTTP request by RequestContextMiddleware; "-" outside a request (startup, scripts).
request_id: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes every LogRecord has; anything else on a record came from ``extra=``.
_STANDARD_ATTRIBUTES = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "request_id",
    "taskName",
}


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id.get()),
        }
        entry |= {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES}
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str, log_format: str = "text") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(JsonFormatter() if log_format == "json" else logging.Formatter(_TEXT_FORMAT))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    # Uvicorn installs its own handlers; route them through ours. Its access log is replaced by
    # RequestContextMiddleware's, which carries the request id and latency.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
