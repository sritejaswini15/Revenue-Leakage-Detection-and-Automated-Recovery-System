"""Mark payment links past their expiry as EXPIRED. Scheduled daily in render.yaml.

Usage (from backend/): python -m scripts.expire_payment_links
"""

from sqlalchemy import update

from app.core.observability import setup_process
from app.core.time import utc_now
from app.domain.constants import CLOSED_LINK_STATUSES, PaymentLinkStatus
from app.infrastructure.database.models import PaymentLink
from app.infrastructure.database.session import SessionLocal


def main() -> None:
    setup_process()
    with SessionLocal() as session:
        result = session.execute(
            update(PaymentLink)
            .where(PaymentLink.expires_at <= utc_now(), PaymentLink.status.not_in(CLOSED_LINK_STATUSES))
            .values(status=PaymentLinkStatus.EXPIRED)
        )
        session.commit()
    print(f"Marked {result.rowcount} payment links as expired.")


if __name__ == "__main__":
    main()
