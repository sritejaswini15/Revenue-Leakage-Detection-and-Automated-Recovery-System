"""Controlled vocabularies and defaults shared across the system.

Values are ``StrEnum`` members so they compare equal to the plain strings
stored in the database and returned by the API.
"""

from dataclasses import dataclass
from enum import StrEnum


class CaseStatus(StrEnum):
    NEW = "NEW"
    ANALYZING = "ANALYZING"
    ACTIONABLE = "ACTIONABLE"
    ACTION_PENDING = "ACTION_PENDING"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    WAITING_FOR_PAYMENT = "WAITING_FOR_PAYMENT"
    PROMISE_TO_PAY = "PROMISE_TO_PAY"
    ESCALATED = "ESCALATED"
    RECOVERED = "RECOVERED"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"


TERMINAL_CASE_STATUSES = frozenset({CaseStatus.RECOVERED, CaseStatus.STOPPED, CaseStatus.EXPIRED})

# Cases a successful payment can still resolve.
OPEN_CASE_STATUSES = frozenset(
    {
        CaseStatus.NEW,
        CaseStatus.ANALYZING,
        CaseStatus.ACTIONABLE,
        CaseStatus.ACTION_PENDING,
        CaseStatus.ACTION_EXECUTED,
        CaseStatus.WAITING_FOR_PAYMENT,
        CaseStatus.ESCALATED,
        CaseStatus.PROMISE_TO_PAY,
    }
)

# Cases still in automated contact; an opt-out stops them. Escalated cases stay
# with the human reviewer.
CONTACTABLE_CASE_STATUSES = OPEN_CASE_STATUSES - {CaseStatus.ESCALATED}


class PolicyDecision(StrEnum):
    PENDING = "PENDING"
    AUTO_APPROVE = "AUTO_APPROVE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    STOP = "STOP"


class ActionType(StrEnum):
    """Recovery actions. The AI may only recommend values from this list."""

    NO_ACTION = "NO_ACTION"
    MONITOR = "MONITOR"
    PAYMENT_LINK = "PAYMENT_LINK"
    PAYMENT_RETRY = "PAYMENT_RETRY"
    REMINDER = "REMINDER"
    PERSONALIZED_OUTREACH = "PERSONALIZED_OUTREACH"
    CHECKOUT_RECOVERY = "CHECKOUT_RECOVERY"
    FOLLOW_UP = "FOLLOW_UP"
    PROMISE_TO_PAY = "PROMISE_TO_PAY"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    STOP = "STOP"


# Actions whose customer message carries a provider-created payment link.
LINK_ACTIONS = frozenset(
    {
        ActionType.PAYMENT_LINK,
        ActionType.PERSONALIZED_OUTREACH,
        ActionType.CHECKOUT_RECOVERY,
        ActionType.REMINDER,
        ActionType.FOLLOW_UP,
    }
)

# Link-based variants that are permitted whenever PAYMENT_LINK is permitted.
# What an operator can choose to run on a case: a retry, or a payment link sent with a message.
EXECUTABLE_ACTIONS = (ActionType.PAYMENT_RETRY, *sorted(LINK_ACTIONS))

# Recommendations to wait: nothing is executed and the case stays open until paid or expired.
WAIT_ACTIONS = frozenset({ActionType.MONITOR, ActionType.NO_ACTION})

PAYMENT_LINK_VARIANTS = frozenset(
    {ActionType.PERSONALIZED_OUTREACH, ActionType.FOLLOW_UP, ActionType.CHECKOUT_RECOVERY}
)


class Resolution(StrEnum):
    """A person's decision on a case awaiting review."""

    APPROVE = "APPROVE"  # run the recommended action
    EDIT = "EDIT"  # run a different action the reviewer chose
    CONTACT = "CONTACT"  # send personalised outreach with a payment link
    REJECT = "REJECT"  # the recommendation is wrong: stop the case
    CLOSE = "CLOSE"  # nothing more to do: stop the case


EXECUTING_RESOLUTIONS = frozenset({Resolution.APPROVE, Resolution.EDIT, Resolution.CONTACT})


class ActionStatus(StrEnum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class PaymentLinkStatus(StrEnum):
    CREATED = "CREATED"
    OPEN = "OPEN"
    PAID = "PAID"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


CLOSED_LINK_STATUSES = frozenset({PaymentLinkStatus.PAID, PaymentLinkStatus.CANCELLED, PaymentLinkStatus.EXPIRED})


class PromiseStatus(StrEnum):
    PENDING = "PENDING"
    FULFILLED = "FULFILLED"
    BROKEN = "BROKEN"
    SUPERSEDED = "SUPERSEDED"  # the customer made a newer promise for the same case


class Role(StrEnum):
    ADMIN = "ADMIN"
    FINANCE_MANAGER = "FINANCE_MANAGER"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


MANAGER_ROLES = (Role.ADMIN, Role.FINANCE_MANAGER)
OPERATOR_ROLES = (Role.ADMIN, Role.FINANCE_MANAGER, Role.OPERATOR)


@dataclass(frozen=True)
class PolicyDefaults:
    max_auto_action_amount: int = 25000
    human_approval_above_amount: int = 25000
    max_contact_attempts: int = 3
    escalate_after_days: int = 7
    # Open cases with no payment are closed as EXPIRED this many days after they were opened.
    case_expiry_days: int = 7
    min_ai_confidence: float = 0.70
    preferred_channels: str = "whatsapp,email"
    supported_languages: str = "English,Hinglish"
    allowed_actions: str = "PAYMENT_LINK,PAYMENT_RETRY,REMINDER,PERSONALIZED_OUTREACH,FOLLOW_UP,CHECKOUT_RECOVERY"


DEFAULT_POLICY = PolicyDefaults()
