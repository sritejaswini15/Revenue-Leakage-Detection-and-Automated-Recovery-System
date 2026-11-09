from tests.helpers import login


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "recoverx-control-plane"


def test_analytics_overview_is_consistent(client, admin_headers):
    data = client.get("/analytics/recovery", headers=admin_headers).json()
    assert data["cases"] > 0
    assert data["funnel"]["risk_cases"] == data["cases"]
    assert data["revenue_recovered"] <= data["revenue_at_risk"]
    assert data["recovery_rate"] == round(data["revenue_recovered"] / data["revenue_at_risk"], 4)
    assert 0 < data["average_ai_confidence"] <= 1
    assert data["mode"] == "simulation"


def test_strategy_performance_attributes_every_case(client, admin_headers):
    strategies = client.get("/analytics/strategy-performance", headers=admin_headers).json()
    overview = client.get("/analytics/recovery", headers=admin_headers).json()
    assert sum(row["cases"] for row in strategies) == overview["cases"]
    assert sum(row["success"] for row in strategies) == overview["successful_recoveries"]
    assert all(row["success"] <= row["attempts"] for row in strategies)


def test_trend_covers_the_requested_window(client, admin_headers):
    trend = client.get("/analytics/trend?days=14", headers=admin_headers).json()
    assert len(trend) == 14
    assert sum(point["cases"] for point in trend) > 0
    assert client.get("/analytics/trend?days=0", headers=admin_headers).status_code == 422


def test_experiments_report_variants(client, admin_headers):
    experiments = client.get("/analytics/experiments", headers=admin_headers).json()
    assert experiments and {variant["variant"] for variant in experiments[0]["variants"]} == {"Variant A", "Variant B"}


def test_integrations_report_simulated_providers_truthfully(client, admin_headers):
    data = client.get("/integrations/status", headers=admin_headers).json()
    by_name = {entry["name"]: entry for entry in data["integrations"]}
    assert by_name["Razorpay"]["status"] == "SIMULATED"
    assert by_name["WhatsApp"]["status"] == "SIMULATED"
    assert data["counts"]["total"] == len(data["integrations"])


def test_mcp_tools_respect_tenant_and_policy(client, admin_headers):
    tools = client.get("/mcp/tools", headers=admin_headers).json()
    assert {"get_recovery_case", "create_payment_link"} <= {tool["name"] for tool in tools}

    read = client.post(
        "/mcp", json={"tool": "get_risk_assessment", "arguments": {"case_id": "RC-RAHUL-18K"}}, headers=admin_headers
    )
    assert read.status_code == 200 and read.json()["result"]["case_id"] == "RC-RAHUL-18K"

    assert client.post("/mcp", json={"tool": "no_such_tool"}, headers=admin_headers).status_code == 400
    missing = client.post(
        "/mcp", json={"tool": "get_recovery_case", "arguments": {"case_id": "RC-OTHER"}}, headers=admin_headers
    )
    assert missing.status_code == 400


def test_batch_evaluation_passes(client, admin_headers):
    response = client.post("/demo/run-batch", headers=admin_headers)
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["cases_processed"] > 0
    assert summary["invalid_ai_decisions"] == 0
    events = client.get("/audit-events?limit=1", headers=admin_headers).json()["items"]
    assert events[0]["event_name"] == "evaluation.completed"


def test_demo_reset_keeps_the_current_session_valid(client, admin_headers):
    response = client.post("/demo/reset", json={"customers": 200, "cases": 60, "seed": 1}, headers=admin_headers)
    assert response.status_code == 200, response.text
    cases = client.get("/recovery-cases?limit=500", headers=admin_headers)
    assert cases.status_code == 200
    assert cases.json()["total"] == 60


def test_api_responses_carry_security_headers(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert response.headers["cache-control"] == "no-store"
    assert "content-security-policy" not in client.get("/docs").headers


def test_oversized_bodies_are_refused_before_processing(client):
    declared = client.post(
        "/webhooks/razorpay", content=b"x" * (300 * 1024), headers={"content-type": "application/json"}
    )
    assert declared.status_code == 413

    def chunks():
        for _ in range(300):
            yield b"x" * 1024

    streamed = client.post("/webhooks/razorpay", content=chunks(), headers={"content-type": "application/json"})
    assert streamed.status_code == 413
    assert client.post("/auth/login", content=b"{" + b" " * (1100 * 1024) + b"}").status_code == 413


def test_the_hourly_job_can_be_run_on_demand_in_demo_deployments(client, admin_headers):
    result = client.post("/demo/run-due-workflows", headers=admin_headers)
    assert result.status_code == 200, result.text
    assert set(result.json()) == {"expired", "advanced", "promises_checked", "failed"}
    operator = login(client, "operator@recoverx.local")
    assert client.post("/demo/run-due-workflows", headers=operator).status_code == 403


def test_the_latest_evaluation_is_available_with_every_metric(client, admin_headers):
    ran = client.post("/demo/run-batch", headers=admin_headers)
    assert ran.status_code == 200, ran.text
    latest = client.get("/demo/evaluation", headers=admin_headers)
    assert latest.status_code == 200
    body = latest.json()
    assert body["summary"] == ran.json() and body["run_by"] == "admin@recoverx.local"
    assert body["summary"]["advisor_model"] == "langgraph-v1" and body["summary"]["cases_processed"] > 0
