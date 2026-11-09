"""Append-only audit trail for consequential activity."""

from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import AuditEvent


def record_audit(
    session: Session,
    organization_id: str,
    event_name: str,
    payload: dict[str, Any],
    actor: str = "system",
    case_id: str | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=organization_id,
        case_id=case_id,
        actor=actor,
        event_name=event_name,
        payload=payload,
        correlation_id=correlation_id,
    )
    session.add(event)
    return event


def list_audit_events(
    session: Session,
    organization_id: str,
    *,
    case_id: str | None = None,
    event_prefix: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditEvent], int]:
    """Newest first. ``event_prefix`` filters by event family, e.g. ``"case."`` or ``"user."``."""
    query = select(AuditEvent).where(AuditEvent.organization_id == organization_id)
    if case_id:
        query = query.where(AuditEvent.case_id == case_id)
    if event_prefix:
        query = query.where(AuditEvent.event_name.startswith(event_prefix, autoescape=True))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    events = session.scalars(query.order_by(desc(AuditEvent.created_at), AuditEvent.id).limit(limit).offset(offset))
    return list(events), total
