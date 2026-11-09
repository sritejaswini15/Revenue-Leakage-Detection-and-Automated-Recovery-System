from tests.helpers import first_contactable_customer


def _new_payment_page(client, headers, amount):
    customer = first_contactable_customer(client, headers)
    created = client.post(
        "/simulation/event",
        json={"event_type": "INVOICE_OVERDUE", "customer_id": customer["id"], "amount": amount},
        headers=headers,
    ).json()
    case = client.get(f"/recovery-cases/{created['case_id']}", headers=headers).json()
    return case, case["payment_link"]["payment_url"].removeprefix("/pay/")


def test_customer_pays_through_the_public_page(client, admin_headers):
    case, token = _new_payment_page(client, admin_headers, 6100)

    page = client.get(f"/pay/{token}")
    assert page.status_code == 200, page.text
    assert page.json()["case_id"] == case["id"]
    assert "customer_email" not in page.json()  # public page exposes no contact details

    paid = client.post(f"/pay/{token}", json={"action": "pay"})
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "RECOVERED"
    assert paid.json()["recovered_amount"] == 6100

    assert client.get(f"/pay/{token}").status_code == 404  # link is closed once paid


def test_customer_can_promise_to_pay(client, admin_headers):
    case, token = _new_payment_page(client, admin_headers, 4200)

    response = client.post(f"/pay/{token}", json={"action": "promise", "promise_date": "2031-01-15"})

    assert response.status_code == 200, response.text
    assert response.json()["promised_date"] == "2031-01-15"
    detail = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert detail["status"] == "PROMISE_TO_PAY"
    assert detail["promises_to_pay"][0]["amount"] == 4200


def test_invalid_promise_date_is_rejected(client, admin_headers):
    _, token = _new_payment_page(client, admin_headers, 4300)
    assert client.post(f"/pay/{token}", json={"action": "promise", "promise_date": "next week"}).status_code == 400


def test_unknown_token_is_not_found(client):
    assert client.get("/pay/not-a-real-token").status_code == 404
