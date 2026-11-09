from typing import Any

from pydantic import BaseModel

from app.api.schemas.common import ApiModel, UtcDatetime


class AuditEventOut(ApiModel):
    id: str
    event_name: str
    case_id: str | None
    actor: str
    payload: dict[str, Any]
    correlation_id: str | None
    created_at: UtcDatetime


class AuditEventList(BaseModel):
    items: list[AuditEventOut]
    total: int
