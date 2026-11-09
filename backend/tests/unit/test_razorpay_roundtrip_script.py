"""The test-mode round-trip script, against a fake Razorpay API (no network)."""

import io
import json
import sys

import pytest

from app.infrastructure.payments import razorpay as module
from scripts import razorpay_test_roundtrip as script


class FakeLinks:
    def __init__(self) -> None:
        self.status = "created"
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request, timeout):  # noqa: ARG002 - mirrors urllib.request.urlopen
        method, path = request.get_method(), request.full_url.removeprefix("https://api.razorpay.com/v1")
        self.calls.append((method, path))
        if path == "/payment_links/plink_rt/cancel":
            self.status = "cancelled"
        body = {"id": "plink_rt", "status": self.status, "short_url": "https://rzp.io/i/rt", "items": []}
        if method == "POST" and path == "/payment_links":
            assert json.loads(request.data)["amount"] == 100  # Rs 1 in paise
        return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def run(monkeypatch, capsys):
    def invoke(key_id: str = "rzp_test_RoundTrip1", *args: str) -> tuple[int, str]:
        monkeypatch.setenv("RAZORPAY_KEY_ID", key_id)
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", "round-trip-secret")
        monkeypatch.setattr(sys, "argv", ["razorpay_test_roundtrip", *args])
        code = script.main()
        return code, capsys.readouterr().out

    return invoke


def test_a_full_automated_round_trip_passes(run, monkeypatch):
    api = FakeLinks()
    monkeypatch.setattr(module, "urlopen", api)
    code, out = run()
    assert code == 0 and "All automated checks passed." in out
    assert ("POST", "/payment_links/plink_rt/cancel") in api.calls
    assert "round-trip-secret" not in out


def test_keep_link_prints_the_manual_payment_steps(run, monkeypatch):
    monkeypatch.setattr(module, "urlopen", FakeLinks())
    code, out = run("rzp_test_RoundTrip1", "--keep-link")
    assert code == 0 and "https://rzp.io/i/rt" in out and "webhooks/razorpay" in out


def test_live_keys_are_refused_without_calling_razorpay(run, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("no request may be made with live keys")

    monkeypatch.setattr(module, "urlopen", forbidden)
    code, out = run("rzp_live_Real01")
    assert code == 2 and "Refusing to run" in out
