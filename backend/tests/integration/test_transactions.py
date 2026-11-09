"""Transactions: every payment RecoverX knows about, filterable and linked to its recovery case."""

from datetime import timedelta
from uuid import uuid4

from app.core.time import utc_now
from tests.helpers import first_contactable_customer
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment, link_paid


def _recovered_through_a_link(client, headers, rupees: int = 1300) -> tuple[dict, str]:
    body = failed_payment(first_contactable_customer(client, headers), rupees)
    case_id = deliver(client, body).json()["case_id"]
    case = client.get(f"/recovery-cases/{case_id}", headers=headers).json()
    assert deliver(client, link_paid(case, rupees * 100)).status_code == 200
    return case, body["payload"]["payment"]["entity"]["id"]


def test_a_recovery_shows_the_failed_and_the_paying_payment_on_its_case(client, admin_headers):
    case, failed_id = _recovered_through_a_link(client, admin_headers)

    listed = client.get("/transactions", params={"case_id": case["id"]}, headers=admin_headers).json()

    assert listed["total"] == 2
    by_status = {item["status"]: item for item in listed["items"]}
    assert by_status["FAILED"]["provider_payment_id"] == failed_id
    assert by_status["CAPTURED"]["amount"] == 1300
    assert all(item["recovery_case_id"] == case["id"] and item["origin"] == "webhook" for item in listed["items"])
    assert all(item["customer_name"] for item in listed["items"])


def test_detail_has_attempts_and_the_linked_case(client, admin_headers):
    case, failed_id = _recovered_through_a_link(client, admin_headers, 1400)

    detail = client.get(f"/transactions/{failed_id}", headers=admin_headers).json()

    assert detail["status"] == "FAILED"
    assert [attempt["status"] for attempt in detail["attempts"]] == ["FAILED"]
    assert detail["case"] == {"id": case["id"], "status": "RECOVERED", "amount": 1400, "recovered_amount": 1400}
    assert client.get(f"/transactions/{detail['id']}", headers=admin_headers).json()["provider_payment_id"] == failed_id


def test_status_search_and_date_filters(client, admin_headers):
    _, failed_id = _recovered_through_a_link(client, admin_headers, 1500)
    today = utc_now().date()

    failed = client.get("/transactions", params={"status": "FAILED", "limit": 200}, headers=admin_headers).json()
    assert failed["items"] and all(item["status"] == "FAILED" for item in failed["items"])
    found = client.get("/transactions", params={"search": failed_id.upper()}, headers=admin_headers).json()
    assert [item["provider_payment_id"] for item in found["items"]] == [failed_id]

    in_range = {"search": failed_id, "date_from": today.isoformat(), "date_to": today.isoformat()}
    assert client.get("/transactions", params=in_range, headers=admin_headers).json()["total"] == 1
    tomorrow = (today + timedelta(days=1)).isoformat()
    assert client.get("/transactions", params={"date_from": tomorrow}, headers=admin_headers).json()["total"] == 0


def test_pages_are_newest_first_and_disjoint(client, admin_headers):
    first = client.get("/transactions", params={"limit": 5}, headers=admin_headers).json()
    second = client.get("/transactions", params={"limit": 5, "offset": 5}, headers=admin_headers).json()
    assert first["total"] == second["total"] > 10
    assert not {item["id"] for item in first["items"]} & {item["id"] for item in second["items"]}
    stamps = [item["created_at"] for item in first["items"] + second["items"]]
    assert stamps == sorted(stamps, reverse=True)


def test_invalid_filters_and_unknown_payments(client, admin_headers):
    assert client.get("/transactions", params={"status": "REFUNDED"}, headers=admin_headers).status_code == 422
    assert client.get("/transactions", params={"date_from": "06-11-2026"}, headers=admin_headers).status_code == 422
    assert client.get(f"/transactions/pay_{uuid4().hex[:10]}", headers=admin_headers).status_code == 404
    assert client.get("/transactions").status_code == 401
