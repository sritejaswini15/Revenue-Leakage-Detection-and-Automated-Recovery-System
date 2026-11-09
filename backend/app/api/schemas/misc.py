"""Schemas for health, webhooks, demo data and MCP."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.api.schemas.common import UtcDatetime


class Health(BaseModel):
    status: str
    service: str
    mode: str
    timestamp: UtcDatetime


class WebhookAck(BaseModel):
    accepted: bool = True
    reason: str | None = None
    duplicate: bool
    dedupe_key: str
    normalized: bool
    event_id: str | None
    case_id: str | None
    case_status: str | None
    recovered_amount: int


class SeedRequest(BaseModel):
    customers: int = Field(1000, ge=1, le=10000)
    cases: int = Field(500, ge=1, le=10000)
    seed: int = 20260902


class SeedResult(BaseModel):
    customers: int
    cases: int
    seed: int


class DueWorkflowRun(BaseModel):
    expired: int
    advanced: int
    promises_checked: int
    failed: int


class MCPTool(BaseModel):
    name: str
    type: Literal["read", "write"]
    description: str
    arguments: list[str]


class MCPRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class MCPResponse(BaseModel):
    tool: str
    result: dict[str, Any]
