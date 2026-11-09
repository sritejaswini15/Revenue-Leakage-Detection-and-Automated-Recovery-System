"""SQLAlchemy ORM models: the persistent source of truth for business and financial state.

Schema changes must ship with an Alembic revision in ``backend/alembic/versions``.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import expression

from app.core.time import utc_now
from app.domain.constants import DEFAULT_POLICY

ORGANIZATION_FK = "organizations.id"
CUSTOMER_FK = "customers.id"
CASE_FK = "recovery_cases.id"
PAYMENT_FK = "payments.id"
EVENT_FK = "revenue_events.id"
RISK_FK = "risk_assessments.id"


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    razorpay_account_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True, index=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    customers: Mapped[list["Customer"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    users: Mapped[list["User"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    policy: Mapped["Policy"] = relationship(back_populates="organization", uselist=False, cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(32), default="OPERATOR")  # ADMIN, FINANCE_MANAGER, OPERATOR, VIEWER
    auth_provider_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    organization: Mapped["Organization"] = relationship(back_populates="users")


class LoginFailure(Base):
    """Failed sign-in attempts, shared by every API instance for rate limiting."""

    __tablename__ = "login_failures"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class PaymentIntegration(Base):
    """A merchant's own payment-provider credentials. Secrets are stored encrypted."""

    __tablename__ = "payment_integrations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="razorpay")
    key_id: Mapped[str] = mapped_column(String(64))
    key_secret_encrypted: Mapped[str] = mapped_column(Text)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    razorpay_customer_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str] = mapped_column(String(32))
    lifetime_value: Mapped[int] = mapped_column(Integer, default=0)
    payment_reliability_pct: Mapped[float] = mapped_column(Float, default=85.0)
    successful_payments: Mapped[int] = mapped_column(Integer, default=0)
    failed_payments: Mapped[int] = mapped_column(Integer, default=0)
    average_payment_amount: Mapped[int] = mapped_column(Integer, default=0)
    preferred_channel: Mapped[str] = mapped_column(String(32), default="whatsapp")  # whatsapp, email
    preferred_language: Mapped[str] = mapped_column(String(32), default="English")  # English, Hinglish
    historical_recovery_rate: Mapped[float] = mapped_column(Float, default=0.0)
    previous_recovery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    best_historical_recovery_action: Mapped[str | None] = mapped_column(String(64), default="PAYMENT_LINK")
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    organization: Mapped["Organization"] = relationship(back_populates="customers")
    cases: Mapped[list["RecoveryCase"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="customer", cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey(CUSTOMER_FK), index=True)
    provider_payment_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(32), index=True)  # CREATED, AUTHORIZED, CAPTURED, FAILED
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    method: Mapped[str | None] = mapped_column(String(64), default="upi")
    # "webhook" (observed through a provider event) or "seed" (demo history). Only observed
    # payments update customer statistics; seeded history is already in the customer profile.
    origin: Mapped[str] = mapped_column(String(32), default="webhook", server_default="webhook", index=True)
    # The recovery case this payment opened or settled, if any.
    recovery_case_id: Mapped[str | None] = mapped_column(
        ForeignKey(CASE_FK, ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    customer: Mapped["Customer"] = relationship(back_populates="payments")
    recovery_case: Mapped["RecoveryCase | None"] = relationship()
    attempts: Mapped[list["PaymentAttempt"]] = relationship(back_populates="payment", cascade="all, delete-orphan")


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    payment_id: Mapped[str] = mapped_column(ForeignKey(PAYMENT_FK), index=True)
    provider_attempt_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(32))  # SUCCESS, FAILED
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    payment: Mapped["Payment"] = relationship(back_populates="attempts")


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey(CUSTOMER_FK), index=True)
    provider_invoice_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    paid_amount: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(32), index=True)  # CREATED, PARTIALLY_PAID, PAID, EXPIRED, OVERDUE
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    customer: Mapped["Customer"] = relationship(back_populates="invoices")


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey(CUSTOMER_FK), index=True)
    provider_subscription_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    plan_name: Mapped[str] = mapped_column(String(160), default="Pro Monthly")
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(32), index=True)  # ACTIVE, PENDING, HALTED, CANCELLED
    current_cycle: Mapped[int] = mapped_column(Integer, default=1)
    next_charge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    customer: Mapped["Customer"] = relationship(back_populates="subscriptions")


class CheckoutAttempt(Base):
    __tablename__ = "checkout_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey(CUSTOMER_FK), nullable=True, index=True)
    session_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(32), default="ABANDONED")  # STARTED, ABANDONED, RECOVERED
    abandoned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RevenueEvent(Base):
    __tablename__ = "revenue_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64), default="simulated_razorpay")
    source_entity: Mapped[str] = mapped_column(String(64), default="payment")
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey(CUSTOMER_FK), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    customer: Mapped["Customer"] = relationship()


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipts"
    __table_args__ = (UniqueConstraint("organization_id", "provider_event_id", name="uq_webhook_receipt_org_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    provider: Mapped[str] = mapped_column(String(64), default="razorpay")
    provider_event_id: Mapped[str] = mapped_column(String(160), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="RECEIVED", index=True)
    revenue_event_id: Mapped[str | None] = mapped_column(ForeignKey(EVENT_FK), nullable=True, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    risk_score: Mapped[int] = mapped_column(Integer)  # 0 - 100
    recovery_probability: Mapped[float] = mapped_column(Float)  # 0.0 - 1.0
    expected_recovery_value: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(64), default="deterministic-v1")
    factors: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # RC-XXXXXX
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    revenue_event_id: Mapped[str] = mapped_column(ForeignKey(EVENT_FK), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey(CUSTOMER_FK), index=True)
    risk_assessment_id: Mapped[str] = mapped_column(ForeignKey(RISK_FK))
    # Explicit state machine: NEW -> ANALYZING -> ACTIONABLE -> ACTION_PENDING -> ACTION_EXECUTED -> WAITING_FOR_PAYMENT
    # Branch/Terminal states: RECOVERED, PROMISE_TO_PAY, RETRY_REQUIRED, EXPIRED, ESCALATED, STOPPED
    status: Mapped[str] = mapped_column(String(32), default="NEW", index=True)
    contact_attempts: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    recommended_action: Mapped[str] = mapped_column(String(64))
    policy_decision: Mapped[str] = mapped_column(String(32), default="PENDING")  # AUTO_APPROVE, HUMAN_REVIEW, STOP
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.85)
    recovered_amount: Mapped[int] = mapped_column(Integer, default=0)
    action_executed: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    customer: Mapped["Customer"] = relationship(back_populates="cases")
    revenue_event: Mapped["RevenueEvent"] = relationship()
    risk_assessment: Mapped["RiskAssessment"] = relationship()
    decisions: Mapped[list["AgentDecision"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    actions: Mapped[list["RecoveryAction"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    payment_link: Mapped["PaymentLink"] = relationship(
        back_populates="case", uselist=False, cascade="all, delete-orphan"
    )
    communications: Mapped[list["Communication"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    promises: Mapped[list["PromiseToPay"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    escalation: Mapped["Escalation"] = relationship(back_populates="case", uselist=False, cascade="all, delete-orphan")
    experiment_results: Mapped[list["ExperimentResult"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class AgentDecision(Base):
    __tablename__ = "agent_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    recovery_case_id: Mapped[str] = mapped_column(ForeignKey(CASE_FK), index=True)
    diagnosis: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(String(160))
    recommended_action: Mapped[str] = mapped_column(String(64))
    recovery_probability: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    reasoning: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(80), default="langgraph-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    case: Mapped["RecoveryCase"] = relationship(back_populates="decisions")


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    recovery_case_id: Mapped[str] = mapped_column(ForeignKey(CASE_FK), index=True)
    action_type: Mapped[str] = mapped_column(String(64))  # PAYMENT_LINK, PAYMENT_RETRY, REMINDER, WHATSAPP, EMAIL
    status: Mapped[str] = mapped_column(
        String(32), default="PENDING", index=True
    )  # PENDING, EXECUTED, FAILED, SKIPPED_BY_POLICY
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    case: Mapped["RecoveryCase"] = relationship(back_populates="actions")


class PaymentLink(Base):
    __tablename__ = "payment_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # pl_XXXXXXXXXXXX
    case_id: Mapped[str] = mapped_column(ForeignKey(CASE_FK), unique=True, index=True)
    provider_link_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(32), default="OPEN", index=True
    )  # CREATED, OPEN, PAID, EXPIRED, CANCELLED
    short_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    public_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    case: Mapped["RecoveryCase"] = relationship(back_populates="payment_link")


class Communication(Base):
    __tablename__ = "communications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str] = mapped_column(ForeignKey(CASE_FK), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey(CUSTOMER_FK), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(32))  # WHATSAPP, EMAIL
    direction: Mapped[str] = mapped_column(String(16), default="OUTBOUND")  # OUTBOUND, INBOUND
    content: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(64), default="simulated_provider")
    provider_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="QUEUED", index=True
    )  # QUEUED, SENT, DELIVERED, OPENED, FAILED
    error_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    case: Mapped["RecoveryCase"] = relationship(back_populates="communications")


class PromiseToPay(Base):
    __tablename__ = "promises_to_pay"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str] = mapped_column(ForeignKey(CASE_FK), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey(CUSTOMER_FK), nullable=True)
    amount: Mapped[int] = mapped_column(Integer)
    promised_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fulfilled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(
        String(32), default="PENDING", index=True
    )  # PENDING, FULFILLED, BROKEN, CANCELLED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    case: Mapped["RecoveryCase"] = relationship(back_populates="promises")


class Escalation(Base):
    __tablename__ = "escalations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str] = mapped_column(ForeignKey(CASE_FK), unique=True, index=True)
    reason: Mapped[str] = mapped_column(
        String(64), index=True
    )  # HIGH_VALUE, LOW_CONFIDENCE, MAX_ATTEMPTS, SPECIAL_CASE, CUSTOMER_REQUEST, POLICY_RULE
    assigned_to_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(64), nullable=True)  # APPROVE, REJECT, EDIT, CONTACT, CLOSE
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    case: Mapped["RecoveryCase"] = relationship(back_populates="escalation")


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), unique=True, index=True)
    max_auto_action_amount: Mapped[int] = mapped_column(Integer, default=DEFAULT_POLICY.max_auto_action_amount)
    human_approval_above_amount: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_POLICY.human_approval_above_amount
    )
    max_contact_attempts: Mapped[int] = mapped_column(Integer, default=DEFAULT_POLICY.max_contact_attempts)
    escalate_after_days: Mapped[int] = mapped_column(Integer, default=DEFAULT_POLICY.escalate_after_days)
    case_expiry_days: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_POLICY.case_expiry_days, server_default=str(DEFAULT_POLICY.case_expiry_days)
    )
    min_ai_confidence: Mapped[float] = mapped_column(Float, default=DEFAULT_POLICY.min_ai_confidence)
    preferred_channels: Mapped[str] = mapped_column(String(128), default=DEFAULT_POLICY.preferred_channels)
    supported_languages: Mapped[str] = mapped_column(String(128), default=DEFAULT_POLICY.supported_languages)
    allowed_actions: Mapped[str] = mapped_column(String(255), default=DEFAULT_POLICY.allowed_actions)
    updated_by: Mapped[str | None] = mapped_column(String(160), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    organization: Mapped["Organization"] = relationship(back_populates="policy")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey(CASE_FK), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(160), default="system")
    event_name: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(ForeignKey(ORGANIZATION_FK), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    segment: Mapped[str] = mapped_column(String(100), default="Failed payments ₹5K–₹20K")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ExperimentResult(Base):
    __tablename__ = "experiment_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey(CASE_FK), index=True)
    variant: Mapped[str] = mapped_column(String(64))  # Variant A, Variant B
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)  # RECOVERED, EXPIRED, STOPPED
    revenue: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    case: Mapped["RecoveryCase"] = relationship(back_populates="experiment_results")
