"""Request ids, structured logs and token redaction."""

import json
import logging

from app.core.logging import JsonFormatter, request_id


def test_every_response_carries_a_request_id(client):
    generated = client.get("/health")
    assert len(generated.headers["x-request-id"]) == 32
    echoed = client.get("/health", headers={"X-Request-ID": "trace-abc-12345"})
    assert echoed.headers["x-request-id"] == "trace-abc-12345"
    unsafe = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert unsafe.headers["x-request-id"] != "bad id\nwith newline"


def test_access_logs_never_contain_payment_tokens(client, caplog):
    with caplog.at_level(logging.INFO, logger="recoverx.access"):
        client.get("/pay/secret-customer-token-123")
    entry = next(record for record in caplog.records if record.name == "recoverx.access")
    assert "secret-customer-token-123" not in entry.getMessage()
    assert entry.path == "/pay/[redacted]" and entry.status == 404 and entry.latency_ms >= 0


def test_json_logs_carry_the_request_id_and_extra_fields():
    token = request_id.set("req-123")
    try:
        record = logging.LogRecord("recoverx", logging.WARNING, __file__, 1, "Action %s failed", ("REMINDER",), None)
        record.case_id = "RC-1"
        record.request_id = request_id.get()
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id.reset(token)
    assert line["message"] == "Action REMINDER failed" and line["level"] == "WARNING"
    assert line["request_id"] == "req-123" and line["case_id"] == "RC-1"


def test_sentry_never_receives_bodies_locals_or_personal_data(monkeypatch):
    import sys
    import types

    from app.core import observability

    captured: dict = {}
    monkeypatch.setitem(sys.modules, "sentry_sdk", types.SimpleNamespace(init=lambda **kwargs: captured.update(kwargs)))
    monkeypatch.setattr(observability.settings, "SENTRY_DSN", "https://key@example.ingest.sentry.io/1")
    observability.init_sentry()
    assert captured["send_default_pii"] is False
    assert captured["include_local_variables"] is False
    assert captured["max_request_body_size"] == "never"


def test_sentry_is_off_without_a_dsn(monkeypatch):
    from app.core import observability

    monkeypatch.setattr(observability.settings, "SENTRY_DSN", None)
    observability.init_sentry()  # must not import or call sentry_sdk
