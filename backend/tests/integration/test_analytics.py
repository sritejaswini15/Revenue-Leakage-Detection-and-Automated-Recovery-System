"""Analytics: the cards and the trend chart agree for the same window."""

import pytest


@pytest.mark.parametrize("days", [1, 7, 30, 90])
def test_cards_and_trend_agree_for_the_same_window(client, admin_headers, days):
    overview = client.get("/analytics/recovery", params={"days": days}, headers=admin_headers).json()
    trend = client.get("/analytics/trend", params={"days": days}, headers=admin_headers).json()

    assert overview["window_days"] == days and len(trend) == days
    assert overview["window_start"].startswith(trend[0]["date"])
    assert sum(point["cases"] for point in trend) == overview["cases"]
    assert sum(point["detected"] for point in trend) == overview["revenue_at_risk"]
    assert sum(point["recovered"] for point in trend) == overview["revenue_recovered"]


def test_all_time_contains_every_window(client, admin_headers):
    all_time = client.get("/analytics/recovery", headers=admin_headers).json()
    month = client.get("/analytics/recovery", params={"days": 30}, headers=admin_headers).json()
    assert all_time["window_days"] is None and all_time["window_start"] is None
    assert month["cases"] <= all_time["cases"]
    assert month["revenue_recovered"] <= all_time["revenue_recovered"]
    strategies = client.get("/analytics/strategy-performance", params={"days": 30}, headers=admin_headers).json()
    assert sum(row["cases"] for row in strategies) == month["cases"]


def test_payment_metrics_are_consistent(client, admin_headers):
    payments = client.get("/analytics/recovery", headers=admin_headers).json()["payments"]
    attempts = payments["captured"] + payments["failed"]
    assert attempts > 0
    assert payments["success_rate"] == round(payments["captured"] / attempts, 4)
    captured = client.get("/transactions", params={"status": "CAPTURED", "limit": 1}, headers=admin_headers).json()
    assert captured["total"] == payments["captured"]


def test_window_bounds_are_validated(client, admin_headers):
    assert client.get("/analytics/recovery", params={"days": 0}, headers=admin_headers).status_code == 422
    assert client.get("/analytics/recovery", params={"days": 366}, headers=admin_headers).status_code == 422
