"""Public customer payment page. Access is by opaque link token; no login."""

from fastapi import APIRouter

from app.api.dependencies import DbSession
from app.api.schemas.payments import (
    CustomerPaymentAction,
    CustomerPaymentResult,
    PublicPaymentLink,
    PublicPaymentRequest,
)
from app.application import payment_portal

router = APIRouter(prefix="/pay", tags=["customer payments"])

INVALID_LINK = {404: {"description": "Link invalid, closed or expired"}}


@router.get("/{token}", responses=INVALID_LINK)
def get_payment_request(token: str, session: DbSession) -> PublicPaymentRequest:
    request = payment_portal.get_payment_request(session, token)
    case, link = request.case, request.link
    return PublicPaymentRequest(
        case_id=case.id,
        customer_name=request.customer.name if request.customer else "Customer",
        amount=case.amount,
        currency=case.currency,
        status=case.status,
        recovered_amount=case.recovered_amount,
        event_type=case.revenue_event.event_type,
        failure_reason=case.failure_reason or "Payment could not be processed",
        recommended_action=case.recommended_action,
        payment_link=PublicPaymentLink(id=link.id, short_url=link.short_url, amount=link.amount, status=link.status),
    )


@router.post("/{token}", responses={**INVALID_LINK, 409: {"description": "Case already closed"}})
def submit_customer_payment(token: str, body: CustomerPaymentAction, session: DbSession) -> CustomerPaymentResult:
    """The customer pays now (simulated capture) or promises to pay by a date."""
    if body.action == "promise":
        promise = payment_portal.promise_to_pay(session, token, body.promise_date)
        return CustomerPaymentResult(
            case_id=promise.case_id,
            status="PROMISE_TO_PAY",
            promise_id=promise.id,
            promised_date=promise.promised_date.date().isoformat(),
        )
    case = payment_portal.pay(session, token)
    return CustomerPaymentResult(
        case_id=case.id,
        status=case.status,
        recovered_amount=case.recovered_amount,
        message=f"Payment captured. Case status: {case.status}",
    )
