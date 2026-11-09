"""Customer directory, statistics from observed payments, and CSV import."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.infrastructure.database.models import Organization, User
from app.infrastructure.database.session import SessionLocal
from tests.conftest import ACCOUNT_ID
from tests.helpers import login
from tests.integration.test_razorpay_lifecycle import deliver, link_paid, razorpay_event


def _new_buyer_failure(rupees: int = 900, payment_id: str | None = None, email: str | None = None) -> dict:
    """A failed payment from someone RecoverX has never seen: their profile is created from it."""
    return razorpay_event(
        "payment.failed",
        "payment",
        {
            "id": payment_id or f"pay_{uuid4().hex[:14]}",
            "amount": rupees * 100,
            "currency": "INR",
            "status": "failed",
            "email": email or f"buyer.{uuid4().hex[:8]}@example.test",
            "contact": "+919811111111",
            "notes": {},
        },
    )


def _csv(*lines: str) -> str:
    return "\n".join(lines) + "\n"


@pytest.fixture
def viewer_headers(client):
    email = f"viewer.{uuid4().hex[:6]}@recoverx.test"
    with SessionLocal() as session:
        organization_id = session.scalar(select(Organization.id).where(Organization.razorpay_account_id == ACCOUNT_ID))
        session.add(
            User(
                organization_id=organization_id,
                email=email,
                name="Viewer",
                role="VIEWER",
                password_hash=hash_password("viewer-password-1"),
            )
        )
        session.commit()
    return login(client, email, "viewer-password-1")


# --- directory -----------------------------------------------------------------------------


def test_search_matches_name_email_phone_or_razorpay_id(client, admin_headers):
    for term in ("Rahul Sharma", "RAHUL.SHARMA@example.test", "9820018000", "cus_rahul_18000"):
        found = client.get("/customers", params={"search": term, "limit": 500}, headers=admin_headers).json()
        assert "cus_rahul_golden_demo" in [item["id"] for item in found["items"]], term


def test_search_wildcards_are_matched_literally(client, admin_headers):
    assert client.get("/customers", params={"search": "r%l"}, headers=admin_headers).json()["total"] == 0
    assert client.get("/customers", params={"search": "rahu_"}, headers=admin_headers).json()["total"] == 0
    assert client.get("/customers", params={"search": "rahul_"}, headers=admin_headers).json()["total"] >= 1


def test_pages_do_not_overlap_and_report_the_total(client, admin_headers):
    first = client.get("/customers?limit=10&offset=0", headers=admin_headers).json()
    second = client.get("/customers?limit=10&offset=10", headers=admin_headers).json()
    assert first["total"] == second["total"] > 20
    assert (first["limit"], second["offset"]) == (10, 10)
    assert not {item["id"] for item in first["items"]} & {item["id"] for item in second["items"]}
    values = [item["lifetime_value"] for item in first["items"] + second["items"]]
    assert values == sorted(values, reverse=True)


def test_contact_filter(client, admin_headers):
    opted_out = client.get("/customers?contact=opted_out&limit=500", headers=admin_headers).json()
    contactable = client.get("/customers?contact=contactable&limit=500", headers=admin_headers).json()
    everyone = client.get("/customers?limit=1", headers=admin_headers).json()
    assert all(item["opted_out"] for item in opted_out["items"])
    assert not any(item["opted_out"] for item in contactable["items"])
    assert opted_out["total"] + contactable["total"] == everyone["total"]


def test_customer_detail_lists_cases_and_payments(client, admin_headers):
    detail = client.get("/customers/cus_rahul_golden_demo", headers=admin_headers).json()
    assert "RC-RAHUL-18K" in [case["id"] for case in detail["cases"]]
    assert detail["payments"], "seeded payment history is shown"
    assert (
        detail["stats"]["profile_successful"] == detail["successful_payments"] - detail["stats"]["observed_successful"]
    )


# --- statistics ----------------------------------------------------------------------------


def _customer_of_case(client, headers, case_id: str) -> dict:
    case = client.get(f"/recovery-cases/{case_id}", headers=headers).json()
    return client.get(f"/customers/{case['customer_id']}", headers=headers).json()


def test_observed_payments_update_statistics_once(client, admin_headers):
    failure = _new_buyer_failure(900)
    opened = deliver(client, failure, event_id=f"evt_{uuid4().hex[:12]}")
    case_id = opened.json()["case_id"]
    customer = _customer_of_case(client, admin_headers, case_id)
    stats = customer["stats"]
    assert (stats["observed_successful"], stats["observed_failed"]) == (0, 1)
    # A new profile is a neutral prior worth four payments at 85%: (3.4 + 0) / 5.
    assert customer["payment_reliability_pct"] == 68.0

    # The same webhook delivered again is a duplicate and changes nothing.
    deliver(client, failure, event_id=f"evt_{uuid4().hex[:12]}")
    assert _customer_of_case(client, admin_headers, case_id)["stats"]["observed_failed"] == 1

    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert deliver(client, link_paid(case, 90_000)).status_code == 200
    after = _customer_of_case(client, admin_headers, case_id)
    assert (after["stats"]["observed_successful"], after["stats"]["observed_failed"]) == (1, 1)
    assert after["stats"]["observed_captured_amount"] == 900
    assert after["payment_reliability_pct"] == round((3.4 + 1) / 6 * 100, 1)
    assert after["successful_payments"] == after["stats"]["profile_successful"] + 1


def test_a_failed_payment_captured_later_counts_once_as_a_success(client, admin_headers):
    payment_id = f"pay_{uuid4().hex[:14]}"
    failure = _new_buyer_failure(700, payment_id=payment_id)
    case_id = deliver(client, failure).json()["case_id"]
    entity = failure["payload"]["payment"]["entity"]
    deliver(client, razorpay_event("payment.captured", "payment", {**entity, "status": "captured"}))

    stats = _customer_of_case(client, admin_headers, case_id)["stats"]
    assert (stats["observed_successful"], stats["observed_failed"]) == (1, 0)


def test_seeded_history_is_not_counted_as_observed(client, admin_headers):
    checked = 0
    for item in client.get("/customers?sort=name&limit=25", headers=admin_headers).json()["items"]:
        detail = client.get(f"/customers/{item['id']}", headers=admin_headers).json()
        if not detail["payments"] or len(detail["payments"]) == 25:
            continue
        observed = [p for p in detail["payments"] if p["origin"] != "seed" and p["status"] in ("CAPTURED", "FAILED")]
        stats = detail["stats"]
        assert stats["observed_successful"] + stats["observed_failed"] == len(observed)
        checked += any(p["origin"] == "seed" for p in detail["payments"])
    assert checked, "expected customers with seeded payment history"


# --- CSV import ----------------------------------------------------------------------------


def test_preview_validates_and_normalizes_without_writing(client, admin_headers):
    tag = uuid4().hex[:6]
    text = _csv(
        "Name,Email,Phone,Preferred Channel,Lifetime Value,Successful Payments,Failed Payments,Opted Out",
        f'Asha {tag},ASHA.{tag}@Example.test, 98765 43210 ,whatsapp,"12,500",9,1,no',
        f"Bad Email {tag},not-an-email,,email,,,,",
        f"Bad Phone {tag},bad.{tag}@example.test,12345,,,,,",
        f"Asha Again {tag},asha.{tag}@example.test,,,,,,",
        f",noname.{tag}@example.test,,,,,,",
        f"Rahul Update {tag},rahul.sharma@example.test,,,,,,",
    )
    preview = client.post("/customers/import", json={"csv": text}, headers=admin_headers)
    assert preview.status_code == 200, preview.text
    summary = preview.json()
    assert summary["committed"] is False
    assert (summary["total_rows"], summary["invalid"], summary["duplicates"]) == (6, 3, 1)
    rows = {row["line"]: row for row in summary["rows"]}
    assert rows[2]["action"] == "create"
    assert (rows[2]["email"], rows[2]["phone"]) == (f"asha.{tag}@example.test", "+919876543210")
    assert rows[3]["action"] == rows[4]["action"] == rows[6]["action"] == "invalid"
    assert "not a valid address" in rows[3]["errors"][0]
    assert "10-digit Indian mobile" in rows[4]["errors"][0]
    assert rows[5]["action"] == "duplicate"
    assert client.get(f"/customers?search={tag}", headers=admin_headers).json()["total"] == 0


def test_commit_creates_and_updates_and_reports_counts(client, admin_headers):
    tag = uuid4().hex[:6]
    text = _csv(
        "name,email,phone,lifetime_value,successful_payments,failed_payments",
        f"Meera {tag},meera.{tag}@example.test,9123456780,4000,3,1",
        f"Kabir {tag},,+447700900123,,,",
    )
    committed = client.post("/customers/import", json={"csv": text, "commit": True}, headers=admin_headers).json()
    assert (committed["committed"], committed["created"], committed["updated"]) == (True, 2, 0)

    meera = client.get(f"/customers?search=meera.{tag}", headers=admin_headers).json()["items"][0]
    assert (meera["phone"], meera["lifetime_value"], meera["preferred_channel"]) == ("+919123456780", 4000, "whatsapp")
    assert meera["stats"]["profile_reliability_pct"] == 75.0
    kabir = client.get(f"/customers?search=Kabir {tag}", headers=admin_headers).json()["items"][0]
    assert kabir["phone"] == "+447700900123" and kabir["email"] == ""

    again = _csv("name,email,lifetime_value", f"Meera Rao {tag},MEERA.{tag}@example.test,5000")
    updated = client.post("/customers/import", json={"csv": again, "commit": True}, headers=admin_headers).json()
    assert (updated["created"], updated["updated"]) == (0, 1)
    meera = client.get(f"/customers/{meera['id']}", headers=admin_headers).json()
    assert (meera["name"], meera["lifetime_value"], meera["phone"]) == (f"Meera Rao {tag}", 5000, "+919123456780")


def test_an_import_can_opt_a_customer_out_but_never_back_in(client, admin_headers):
    tag = uuid4().hex[:6]
    email = f"consent.{tag}@example.test"
    client.post(
        "/customers/import",
        json={"csv": _csv("name,email,opted_out", f"Consent {tag},{email},yes"), "commit": True},
        headers=admin_headers,
    )
    client.post(
        "/customers/import",
        json={"csv": _csv("name,email,opted_out", f"Consent {tag},{email},no"), "commit": True},
        headers=admin_headers,
    )
    customer = client.get(f"/customers?search={email}", headers=admin_headers).json()["items"][0]
    assert customer["opted_out"] is True


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "header row"),
        ("email,phone\nx@example.test,\n", "'name' column is required"),
        ("name,city\nAsha,Pune\n", "identify customers"),
    ],
)
def test_unusable_files_are_rejected(client, admin_headers, text, message):
    response = client.post("/customers/import", json={"csv": text or " "}, headers=admin_headers)
    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_viewers_cannot_import(client, viewer_headers):
    response = client.post(
        "/customers/import", json={"csv": _csv("name,email", "A,a@example.test")}, headers=viewer_headers
    )
    assert response.status_code == 403


def test_customer_sync_needs_the_merchants_own_razorpay_keys(client, admin_headers):
    response = client.post("/customers/sync", json={"commit": False}, headers=admin_headers)
    assert response.status_code == 400
    assert "Razorpay test keys" in response.json()["detail"]
