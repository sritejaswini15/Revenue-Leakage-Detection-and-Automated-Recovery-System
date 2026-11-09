from typing import Annotated

from fastapi import APIRouter, Header, Request

from app.api.dependencies import DbSession
from app.api.schemas.misc import WebhookAck
from app.application.webhooks import receive_razorpay_webhook

router = APIRouter(tags=["webhooks"])


@router.post(
    "/webhooks/razorpay",
    responses={
        400: {"description": "Malformed body or missing account/event id"},
        401: {"description": "Missing or invalid signature"},
        404: {"description": "Unknown Razorpay merchant account"},
    },
)
async def razorpay_webhook(
    request: Request,
    session: DbSession,
    x_razorpay_signature: Annotated[str | None, Header()] = None,
    x_razorpay_account_id: Annotated[str | None, Header()] = None,
    x_razorpay_event_id: Annotated[str | None, Header()] = None,
) -> WebhookAck:
    """Signed Razorpay webhook.

    Replays of the same event id return the original outcome. Authentic events that cannot
    be used (unsupported type, unknown customer) return 200 with ``accepted: false``.
    """
    outcome = receive_razorpay_webhook(
        session, await request.body(), x_razorpay_signature, x_razorpay_account_id, x_razorpay_event_id
    )
    case = outcome.case
    return WebhookAck(
        accepted=outcome.accepted,
        reason=outcome.reason,
        duplicate=outcome.duplicate,
        dedupe_key=outcome.dedupe_key,
        normalized=outcome.event is not None,
        event_id=outcome.event.id if outcome.event else None,
        case_id=case.id if case else None,
        case_status=case.status if case else None,
        recovered_amount=case.recovered_amount if case else 0,
    )
