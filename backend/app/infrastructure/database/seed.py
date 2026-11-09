"""Deterministic synthetic merchant dataset for demos, evaluation and tests.

Re-seeding replaces all business data (customers, payments, cases, audit
trail) but never deletes user accounts or their sessions. Demo logins
(password ``recoverx-demo``) are only created outside production.
"""

import random
from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.core.time import utc_now
from app.domain.constants import DEFAULT_POLICY, CaseStatus, PolicyDecision, Role
from app.domain.policy import PolicyEngine
from app.domain.risk import RiskEngine
from app.infrastructure.database.models import (
    AgentDecision,
    AuditEvent,
    CheckoutAttempt,
    Communication,
    Customer,
    Escalation,
    Experiment,
    ExperimentResult,
    Invoice,
    Organization,
    Payment,
    PaymentAttempt,
    PaymentLink,
    Policy,
    PromiseToPay,
    RecoveryAction,
    RecoveryCase,
    RevenueEvent,
    RiskAssessment,
    Subscription,
    User,
    WebhookReceipt,
)

# Seeded decisions are written by this script, not produced by the advisor.
SEED_DECISION_MODEL = "synthetic-seed"
DEMO_PASSWORD = "recoverx-demo"  # noqa: S105 - demo logins exist only outside production
DEMO_USERS = (
    (Role.ADMIN, "admin@recoverx.local", "RecoverX Admin"),
    (Role.FINANCE_MANAGER, "finance@recoverx.local", "Finance Manager"),
    (Role.OPERATOR, "operator@recoverx.local", "Recovery Operator"),
    (Role.VIEWER, "viewer@recoverx.local", "Recovery Viewer"),
)

NAMES = [
    "Rahul Sharma", "Priya Nair", "Arjun Kapoor", "Meera Shah", "Kavya Iyer",
    "Vikram Rao", "Neha Gupta", "Aarav Mehta", "Ananya Singh", "Rohan Verma",
    "Siddharth Joshi", "Aditi Deshmukh", "Karan Malhotra", "Ritu Patel", "Tarun Saxena",
    "Pooja Bhatia", "Devendra Sen", "Sneha Kulkarni", "Amitabh Das", "Shreya Reddy",
]  # fmt: skip

FAILURE_REASONS = [
    "insufficient_funds",
    "card_expired",
    "do_not_honor",
    "temporary_technical_error",
    "mandate_decline",
    "payment_timeout",
    "authentication_failed",
]

CASE_AMOUNTS = [2400, 4999, 7200, 12000, 18000, 25000, 42500, 68000, 120000]
CASE_EVENT_TYPES = ["PAYMENT_FAILURE", "SUBSCRIPTION_HALTED", "INVOICE_OVERDUE", "CHECKOUT_ABANDONED"]

# Children before parents so foreign keys hold on PostgreSQL.
_BUSINESS_TABLES_IN_DELETE_ORDER = (
    AuditEvent,
    ExperimentResult,
    Experiment,
    Communication,
    PaymentLink,
    RecoveryAction,
    PromiseToPay,
    Escalation,
    AgentDecision,
    WebhookReceipt,
    CheckoutAttempt,
    PaymentAttempt,
    Payment,
    RecoveryCase,
    RiskAssessment,
    RevenueEvent,
    Invoice,
    Subscription,
    Customer,
    Policy,
)


def generate_synthetic_dataset(
    session: Session,
    customer_count: int = 1000,
    case_count: int = 500,
    seed: int = 20260902,
) -> dict[str, int]:
    rng = random.Random(seed)  # noqa: S311 - reproducible demo data, not security
    now = utc_now()

    for model in _BUSINESS_TABLES_IN_DELETE_ORDER:
        session.execute(delete(model))
    session.flush()

    organization = _ensure_organization(session)
    policy = Policy(organization_id=organization.id, updated_by="RecoverX Setup", **asdict(DEFAULT_POLICY))
    session.add(policy)
    if settings.demo_users_enabled:
        _ensure_demo_users(session, organization)
    session.flush()

    rahul, acme = _golden_customers(organization)
    customers = [rahul, acme]
    session.add_all(customers)
    customers += _synthetic_customers(rng, organization, start=len(customers), count=customer_count)
    session.add_all(customers[2:])
    session.flush()

    experiment = Experiment(
        organization_id=organization.id,
        name="Personalized Hinglish vs Generic Reminder",
        description=(
            "Testing recovery lift on failed payments ₹5K–₹20K between generic reminder "
            "and personalized Hinglish Payment Link"
        ),
        segment="Failed payments ₹5K–₹20K",
        status="ACTIVE",
    )
    session.add(experiment)
    session.flush()

    _payment_history(session, rng, organization, customers[:200], now)
    session.flush()

    _golden_cases(session, organization, rahul, acme)
    experiment_results = _synthetic_cases(
        session, rng, organization, policy, experiment, customers, case_count, seed, now
    )
    # Cases must exist before the experiment results that reference them.
    session.flush()
    session.add_all(experiment_results)
    session.flush()

    session.add(
        AuditEvent(
            organization_id=organization.id,
            actor="system_seeder",
            event_name="demo.dataset.seeded",
            payload={"customer_count": customer_count, "case_count": case_count, "seed": seed},
        )
    )
    session.commit()
    return {"customers": customer_count, "cases": case_count, "seed": seed}


def _ensure_organization(session: Session) -> Organization:
    organization = session.scalar(select(Organization).order_by(Organization.created_at).limit(1))
    if organization is None:
        organization = Organization(name="RecoverX Demo Merchant", currency="INR", timezone="Asia/Kolkata")
        session.add(organization)
        session.flush()
    return organization


def _ensure_demo_users(session: Session, organization: Organization) -> None:
    """Create missing demo logins; existing accounts (and their passwords) are left alone."""
    for role, email, name in DEMO_USERS:
        if session.scalar(select(User).where(func.lower(User.email) == email)):
            continue
        session.add(
            User(
                organization_id=organization.id,
                email=email,
                name=name,
                role=role,
                password_hash=hash_password(DEMO_PASSWORD),
            )
        )


def _golden_customers(organization: Organization) -> tuple[Customer, Customer]:
    """Two fixed profiles the demo script and tests rely on."""
    rahul = Customer(
        id="cus_rahul_golden_demo",
        organization_id=organization.id,
        razorpay_customer_id="cus_rahul_18000",
        name="Rahul Sharma",
        email="rahul.sharma@example.test",
        phone="+919820018000",
        lifetime_value=324000,
        payment_reliability_pct=91.0,
        successful_payments=18,
        failed_payments=1,
        average_payment_amount=18000,
        preferred_channel="whatsapp",
        preferred_language="English",
        historical_recovery_rate=0.88,
        previous_recovery_attempts=1,
        best_historical_recovery_action="PAYMENT_LINK",
        opted_out=False,
    )
    acme = Customer(
        id="cus_acme_pvt_ltd",
        organization_id=organization.id,
        razorpay_customer_id="cus_acme_12500",
        name="Acme Pvt Ltd",
        email="billing@acmepvtltd.test",
        phone="+919810012500",
        lifetime_value=450000,
        payment_reliability_pct=88.0,
        successful_payments=24,
        failed_payments=2,
        average_payment_amount=12500,
        preferred_channel="email",
        preferred_language="English",
        historical_recovery_rate=0.85,
        previous_recovery_attempts=2,
        best_historical_recovery_action="REMINDER",
        opted_out=False,
    )
    return rahul, acme


def _customer_archetype(rng: random.Random, archetype: str) -> tuple[float, int, int, int, bool]:
    """Return (reliability %, successful payments, failed payments, average amount, opted out)."""
    if archetype == "enterprise":
        return (
            rng.uniform(92.0, 99.0),
            rng.randint(25, 60),
            rng.randint(0, 2),
            rng.choice([25000, 45000, 75000, 120000]),
            False,
        )
    if archetype == "high_rel":
        return (
            rng.uniform(80.0, 92.0),
            rng.randint(10, 30),
            rng.randint(1, 3),
            rng.choice([5000, 12000, 18000, 24000]),
            False,
        )
    if archetype == "mid_rel":
        return (
            rng.uniform(65.0, 80.0),
            rng.randint(4, 15),
            rng.randint(2, 5),
            rng.choice([2500, 5000, 8500, 15000]),
            False,
        )
    if archetype == "low_rel":
        return rng.uniform(45.0, 65.0), rng.randint(1, 6), rng.randint(3, 8), rng.choice([1500, 3500, 7000]), False
    if archetype == "opted_out":
        return rng.uniform(60.0, 85.0), rng.randint(3, 10), rng.randint(1, 3), rng.choice([3000, 6000]), True
    return 75.0, rng.randint(1, 3), rng.randint(0, 1), rng.choice([2000, 4000, 9000]), False  # new customer


def _synthetic_customers(rng: random.Random, organization: Organization, start: int, count: int) -> list[Customer]:
    customers = []
    for index in range(start, count):
        archetype = rng.choice(["enterprise", "high_rel", "mid_rel", "low_rel", "opted_out", "new"])
        name = f"{rng.choice(NAMES)} {index}"
        reliability, successful, failed, average_amount, opted_out = _customer_archetype(rng, archetype)
        customers.append(
            Customer(
                organization_id=organization.id,
                razorpay_customer_id=f"cus_{index + 1:05d}",
                name=name,
                email=f"customer{index + 1}@example.test",
                phone=f"+9199{rng.randint(10000000, 99999999)}",
                lifetime_value=int(average_amount * successful),
                payment_reliability_pct=round(reliability, 1),
                successful_payments=successful,
                failed_payments=failed,
                average_payment_amount=average_amount,
                preferred_channel=rng.choice(["whatsapp", "email"]),
                preferred_language=rng.choice(["English", "Hinglish"]),
                historical_recovery_rate=round(rng.uniform(0.35, 0.92), 2),
                previous_recovery_attempts=rng.randint(0, 4),
                best_historical_recovery_action=rng.choice(["PAYMENT_LINK", "PAYMENT_RETRY", "REMINDER"]),
                opted_out=opted_out,
            )
        )
    return customers


def _payment_history(
    session: Session, rng: random.Random, organization: Organization, customers: list[Customer], now: datetime
) -> None:
    for customer in customers:
        session.add(
            Payment(
                organization_id=organization.id,
                customer_id=customer.id,
                provider_payment_id=f"pay_hist_{uuid4().hex[:10]}",
                amount=customer.average_payment_amount,
                status="CAPTURED",
                origin="seed",
                created_at=now - timedelta(days=rng.randint(5, 60)),
            )
        )
        session.add(
            Subscription(
                organization_id=organization.id,
                customer_id=customer.id,
                provider_subscription_id=f"sub_{uuid4().hex[:10]}",
                plan_name="RecoverX Premium Monthly",
                amount=customer.average_payment_amount,
                status=rng.choice(["ACTIVE", "HALTED", "ACTIVE", "ACTIVE"]),
                next_charge_at=now + timedelta(days=rng.randint(1, 20)),
            )
        )
        session.add(
            Invoice(
                organization_id=organization.id,
                customer_id=customer.id,
                provider_invoice_id=f"inv_{uuid4().hex[:10]}",
                amount=customer.average_payment_amount * 2,
                paid_amount=0,
                status=rng.choice(["PAID", "OVERDUE", "PARTIALLY_PAID"]),
                due_at=now - timedelta(days=rng.randint(2, 14)),
            )
        )


def _mirrored_payment(
    organization: Organization,
    customer: Customer,
    provider_payment_id: str,
    amount: int,
    recovered: bool,
    case: RecoveryCase,
) -> Payment:
    """Local copy of the provider payment behind a revenue event, so PAYMENT_RETRY has a record to act on."""
    return Payment(
        organization_id=organization.id,
        customer_id=customer.id,
        provider_payment_id=provider_payment_id,
        amount=amount,
        status="CAPTURED" if recovered else "FAILED",
        failure_reason=None if recovered else "payment_failed",
        origin="seed",
        recovery_case=case,
    )


def _golden_cases(session: Session, organization: Organization, rahul: Customer, acme: Customer) -> None:
    """RC-RAHUL-18K and RC-ACME-12500: actionable, auto-approvable demo cases."""
    golden = [
        {
            "customer": rahul,
            "case_id": "RC-RAHUL-18K",
            "amount": 18000,
            "source_id": "pay_rahul_18000",
            "dedupe_key": "golden-demo-rahul-18000",
            "risk": (
                82,
                0.84,
                15120,
                {"amount": 18000, "reliability_pct": 91.0, "repeat_failures": 0, "overdue_days": 0},
            ),
            "action": "PAYMENT_LINK",
            "confidence": 0.91,
            "root_cause": "ISOLATED_PAYMENT_METHOD_ISSUE",
            "diagnosis": (
                "Customer has successfully completed 18 of their previous 19 payments. The current failure "
                "appears isolated rather than behavioral. Recovery probability is high."
            ),
            "reasoning": (
                "18 successful historical payments, 1 previous failure, 91% payment reliability -> likely isolated "
                "payment method issue. Recommended Payment Link + WhatsApp outreach."
            ),
        },
        {
            "customer": acme,
            "case_id": "RC-ACME-12500",
            "amount": 12500,
            "source_id": "pay_acme_12500",
            "dedupe_key": "golden-demo-acme-12500",
            "risk": (
                78,
                0.82,
                10250,
                {"amount": 12500, "reliability_pct": 88.0, "repeat_failures": 0, "overdue_days": 1},
            ),
            "action": "PAYMENT_RETRY",
            "confidence": 0.89,
            "root_cause": "INSUFFICIENT_FUNDS_OR_LIMIT",
            "diagnosis": (
                "B2B corporate account with recurring billing. Payment degraded due to credit line limit exhaustion."
            ),
            "reasoning": "24 successful corporate transactions. Retry scheduled for next morning banking hours.",
        },
    ]
    for spec in golden:
        customer, amount = spec["customer"], spec["amount"]
        risk_score, probability, expected_value, factors = spec["risk"]
        event = RevenueEvent(
            id=str(uuid4()),
            organization_id=organization.id,
            event_type="PAYMENT_FAILURE",
            source="simulated_razorpay",
            source_entity="payment",
            source_id=spec["source_id"],
            customer_id=customer.id,
            amount=amount,
            currency="INR",
            raw_payload={"event": "payment.failed", "amount": amount, "customer_id": customer.razorpay_customer_id},
            dedupe_key=spec["dedupe_key"],
        )
        risk = RiskAssessment(
            id=str(uuid4()),
            risk_score=risk_score,
            recovery_probability=probability,
            expected_recovery_value=expected_value,
            model_version=RiskEngine.MODEL_VERSION,
            factors=factors,
        )
        case = RecoveryCase(
            id=spec["case_id"],
            organization_id=organization.id,
            revenue_event_id=event.id,
            customer_id=customer.id,
            risk_assessment_id=risk.id,
            status=CaseStatus.ACTIONABLE,
            contact_attempts=0,
            amount=amount,
            currency="INR",
            recommended_action=spec["action"],
            policy_decision=PolicyDecision.AUTO_APPROVE,
            ai_confidence=spec["confidence"],
            recovered_amount=0,
            action_executed=False,
            failure_reason=spec["root_cause"],
        )
        decision = AgentDecision(
            recovery_case_id=case.id,
            diagnosis=spec["diagnosis"],
            root_cause=spec["root_cause"],
            recommended_action=spec["action"],
            recovery_probability=probability,
            confidence=spec["confidence"],
            reasoning=spec["reasoning"],
            model=SEED_DECISION_MODEL,
        )
        payment = _mirrored_payment(organization, customer, spec["source_id"], amount, recovered=False, case=case)
        session.add_all([event, risk, case, decision, payment])


def _synthetic_cases(
    session: Session,
    rng: random.Random,
    organization: Organization,
    policy: Policy,
    experiment: Experiment,
    customers: list[Customer],
    case_count: int,
    seed: int,
    now: datetime,
) -> list[ExperimentResult]:
    """Generate cases with a realistic policy outcome mix; returns their A/B experiment results."""
    experiment_results = []
    for index in range(2, case_count):
        customer = customers[index % len(customers)]
        amount = rng.choice(CASE_AMOUNTS)
        event_type = rng.choice(CASE_EVENT_TYPES)
        overdue_days = rng.randint(0, 18)
        repeat_failures = rng.randint(0, 3)
        risk_eval = RiskEngine.evaluate(amount, customer, overdue_days, repeat_failures, event_type)

        case_id = f"RC-{1000 + index}"
        event = RevenueEvent(
            id=str(uuid4()),
            organization_id=organization.id,
            event_type=event_type,
            source="simulated_razorpay",
            source_entity=(
                "payment"
                if event_type == "PAYMENT_FAILURE"
                else "invoice"
                if "INVOICE" in event_type
                else "subscription"
            ),
            source_id=f"pay_{seed}_{index}",
            customer_id=customer.id,
            amount=amount,
            currency="INR",
            raw_payload={"simulated": True, "event_type": event_type, "amount": amount},
            dedupe_key=f"demo-{seed}-{index}",
            occurred_at=now - timedelta(days=overdue_days),
        )
        risk = RiskAssessment(
            id=str(uuid4()),
            risk_score=risk_eval.risk_score,
            recovery_probability=risk_eval.recovery_probability,
            expected_recovery_value=risk_eval.expected_recovery_value,
            model_version=risk_eval.model_version,
            factors=risk_eval.factors,
        )
        draft = RecoveryCase(
            amount=amount,
            contact_attempts=rng.randint(0, 2),
            status=CaseStatus.ACTIONABLE,
            ai_confidence=risk_eval.recovery_probability,
        )
        decision = PolicyEngine.evaluate(
            policy, draft, customer, ai_confidence=risk_eval.recovery_probability, overdue_days=overdue_days
        ).decision

        status = {
            PolicyDecision.HUMAN_REVIEW: CaseStatus.ESCALATED,
            PolicyDecision.STOP: CaseStatus.STOPPED,
        }.get(decision, CaseStatus.WAITING_FOR_PAYMENT)
        # About 60-65% of auto-approved cases have historically been recovered.
        is_recovered = status == CaseStatus.WAITING_FOR_PAYMENT and rng.random() < risk_eval.recovery_probability * 0.75
        if is_recovered:
            status = CaseStatus.RECOVERED
        executed = status in (CaseStatus.WAITING_FOR_PAYMENT, CaseStatus.RECOVERED)
        recovered_amount = amount if is_recovered else 0
        recommended_action = (
            "PAYMENT_LINK"
            if event_type in ("PAYMENT_FAILURE", "CHECKOUT_ABANDONED")
            else "REMINDER"
            if "INVOICE" in event_type
            else "PAYMENT_RETRY"
        )

        case = RecoveryCase(
            id=case_id,
            organization_id=organization.id,
            revenue_event_id=event.id,
            customer_id=customer.id,
            risk_assessment_id=risk.id,
            status=status,
            contact_attempts=1 if executed else 0,
            amount=amount,
            currency="INR",
            recommended_action=recommended_action,
            policy_decision=decision,
            ai_confidence=risk_eval.recovery_probability,
            recovered_amount=recovered_amount,
            action_executed=executed,
            failure_reason=rng.choice(FAILURE_REASONS),
            created_at=now - timedelta(days=overdue_days),
        )
        reliability = customer.payment_reliability_pct
        event_label = event_type.replace("_", " ").title()
        if event.source_entity in ("payment", "subscription"):
            session.add(
                _mirrored_payment(organization, customer, event.source_id, amount, recovered=is_recovered, case=case)
            )
        session.add_all(
            [
                event,
                risk,
                case,
                AgentDecision(
                    recovery_case_id=case.id,
                    diagnosis=f"{event_label} diagnosed with {int(reliability)}% reliability track record.",
                    root_cause="ISOLATED_PAYMENT_METHOD_ISSUE" if reliability > 80 else "INSUFFICIENT_FUNDS_OR_LIMIT",
                    recommended_action=recommended_action,
                    recovery_probability=risk_eval.recovery_probability,
                    confidence=risk_eval.recovery_probability,
                    reasoning=(
                        f"Selected {recommended_action} via {customer.preferred_channel} based on customer preference."
                    ),
                    model=SEED_DECISION_MODEL,
                ),
            ]
        )

        if executed:
            session.add_all(
                [
                    PaymentLink(
                        id=f"pl_{uuid4().hex[:12]}",
                        case_id=case.id,
                        provider_link_id=f"plink_{uuid4().hex[:10]}",
                        amount=amount,
                        status="PAID" if is_recovered else "OPEN",
                        short_url=f"https://rzp.io/i/pl_{case.id}",
                        created_at=case.created_at,
                    ),
                    Communication(
                        case_id=case.id,
                        customer_id=customer.id,
                        channel=customer.preferred_channel.upper(),
                        direction="OUTBOUND",
                        content=f"Payment link for {amount} INR",
                        status="DELIVERED" if is_recovered else "SENT",
                        created_at=case.created_at,
                    ),
                ]
            )

        experiment_results.append(
            ExperimentResult(
                experiment_id=experiment.id,
                case_id=case.id,
                variant=rng.choice(["Variant A", "Variant B"]),
                outcome=status if status in (CaseStatus.RECOVERED, CaseStatus.STOPPED, CaseStatus.ESCALATED) else None,
                revenue=recovered_amount,
            )
        )
    return experiment_results
