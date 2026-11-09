"""Payments: what Razorpay reported, what RecoverX captured, and the cases they belong to."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, DbSession
from app.api.schemas.payments import (
    LinkedCaseOut,
    PaymentAttemptOut,
    TransactionDetailOut,
    TransactionList,
    TransactionOut,
)
from app.application import transactions

router = APIRouter(tags=["transactions"])


@router.get("/transactions")
def list_transactions(
    session: DbSession,
    user: CurrentUser,
    search: Annotated[str | None, Query(max_length=100)] = None,
    status: Literal["CREATED", "AUTHORIZED", "CAPTURED", "FAILED"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    case_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionList:
    """Newest first. Search matches the payment id, customer name or email, or case id; dates are UTC days."""
    views, total = transactions.list_transactions(
        session,
        user.organization_id,
        search=search,
        status=status,
        date_from=date_from,
        date_to=date_to,
        case_id=case_id,
        limit=limit,
        offset=offset,
    )
    return TransactionList(items=[TransactionOut.of(view) for view in views], total=total, limit=limit, offset=offset)


@router.get("/transactions/{payment_id}", responses={404: {"description": "Transaction not found"}})
def get_transaction(payment_id: str, session: DbSession, user: CurrentUser) -> TransactionDetailOut:
    """By RecoverX id or Razorpay payment id, with every attempt and the linked recovery case."""
    detail = transactions.get_transaction(session, user.organization_id, payment_id)
    return TransactionDetailOut(
        **TransactionOut.of(detail.view).model_dump(),
        attempts=[PaymentAttemptOut.model_validate(attempt) for attempt in detail.attempts],
        case=LinkedCaseOut.model_validate(detail.case) if detail.case else None,
    )
