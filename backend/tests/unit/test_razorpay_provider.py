"""RazorpayProvider request/response handling against a fake HTTP layer (no network)."""

import io
import json

import pytest

from app.domain.exceptions import ProviderError
from app.infrastructure.payments import razorpay as module
from app.infrastructure.payments.razorpay import RazorpayProvider


class FakeRazorpay:
    def __init__(self, responses: dict[tuple[str, str], dict]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict | None]] = []

    def __call__(self, request, timeout):  # noqa: ARG002 - mirrors urllib.request.urlopen
        path = request.full_url.removeprefix("https://api.razorpay.com/v1")
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), path, body))
        return io.BytesIO(json.dumps(self.responses[(request.get_method(), path)]).encode())


@pytest.fixture
def fake(monkeypatch):
    def install(responses):
        api = FakeRazorpay(responses)
        monkeypatch.setattr(module, "urlopen", api)
        return api

    return install


@pytest.mark.parametrize(("rupees", "paise"), [(500, 50_000), (1200, 120_000), (120_000, 12_000_000)])
def test_payment_links_are_requested_in_paise(fake, rupees, paise):
    api = fake({("POST", "/payment_links"): {"id": "plink_1", "status": "created", "short_url": "https://rzp.io/i/x"}})
    result = RazorpayProvider("rzp_test_key", "secret").create_payment_link("RC-1", rupees, "Recovery")

    method, path, body = api.requests[0]
    assert (method, path, body["amount"], body["currency"]) == ("POST", "/payment_links", paise, "INR")
    assert body["notes"] == {"recoverx_case_id": "RC-1"}
    assert body["accept_partial"] is False
    assert (result.provider_link_id, result.status, result.short_url) == ("plink_1", "CREATED", "https://rzp.io/i/x")


def test_link_reference_ids_are_unique_per_link(fake):
    """Razorpay rejects a reused reference_id, e.g. after a demo reset or a link refresh."""
    api = fake({("POST", "/payment_links"): {"id": "plink_1", "status": "created"}})
    provider = RazorpayProvider("rzp_test_key", "secret")
    provider.create_payment_link("RC-1", 500, "")
    provider.create_payment_link("RC-1", 500, "")
    first, second = (body["reference_id"] for _, _, body in api.requests)
    assert first != second and first.startswith("RC-1") and len(first) <= 40


def test_fetched_payment_amounts_are_converted_to_rupees(fake):
    fake({("GET", "/payments/pay_1"): {"id": "pay_1", "amount": 120_000, "currency": "INR", "status": "authorized"}})
    payment = RazorpayProvider("rzp_test_key", "secret").fetch_payment("pay_1")
    assert (payment.amount, payment.status) == (1200, "AUTHORIZED")


def test_missing_credentials_never_reach_the_network(fake):
    api = fake({})
    with pytest.raises(ProviderError, match="not configured"):
        RazorpayProvider("", "").create_payment_link("RC-1", 500, "")
    assert api.requests == []
