"""HTTP routes, grouped by resource."""

from fastapi import APIRouter

from app.api.routes import (
    auth,
    cases,
    customers,
    insights,
    integrations,
    mcp,
    payments,
    simulation,
    system,
    transactions,
    users,
    webhooks,
)

api_router = APIRouter()
for module in (
    system,
    auth,
    webhooks,
    cases,
    customers,
    transactions,
    insights,
    integrations,
    users,
    simulation,
    payments,
    mcp,
):
    api_router.include_router(module.router)
