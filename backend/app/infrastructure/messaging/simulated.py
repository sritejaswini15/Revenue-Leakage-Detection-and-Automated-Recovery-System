"""Simulated WhatsApp and email delivery: persists the outbound message as sent."""

from uuid import uuid4

from sqlalchemy.orm import Session

from app.application.ports import MessageResult
from app.infrastructure.database.models import Communication


class _SimulatedChannel:
    channel: str
    provider_name: str
    message_id_prefix: str

    def send_message(
        self, session: Session, case_id: str, customer_id: str, recipient: str, content: str
    ) -> MessageResult:
        communication = Communication(
            case_id=case_id,
            customer_id=customer_id,
            channel=self.channel,
            direction="OUTBOUND",
            content=content,
            provider=self.provider_name,
            provider_message_id=f"{self.message_id_prefix}_{uuid4().hex[:14]}",
            status="SENT",
        )
        session.add(communication)
        session.flush()
        return MessageResult(communication.id, self.channel, recipient, communication.status)


class SimulatedWhatsAppProvider(_SimulatedChannel):
    channel = "WHATSAPP"
    provider_name = "simulated_whatsapp"
    message_id_prefix = "wamid"


class SimulatedEmailProvider(_SimulatedChannel):
    channel = "EMAIL"
    provider_name = "simulated_email"
    message_id_prefix = "msg"
