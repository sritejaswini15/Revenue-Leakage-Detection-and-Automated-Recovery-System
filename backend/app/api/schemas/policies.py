from pydantic import Field, field_validator

from app.api.schemas.common import ApiModel
from app.domain.constants import DEFAULT_POLICY, EXECUTABLE_ACTIONS
from app.domain.policy import CHANNELS, LANGUAGES, parse_list


def _normalized(value: str, allowed: tuple[str, ...], what: str, *, key=str.lower) -> str:  # noqa: ANN001
    """Keep the merchant's order, drop duplicates, and refuse unknown or empty lists."""
    by_key = {key(item): item for item in allowed}
    chosen: list[str] = []
    for item in parse_list(value):
        canonical = by_key.get(key(item))
        if canonical is None:
            raise ValueError(f"Unknown {what} {item!r}; choose from {', '.join(allowed)}")
        if canonical not in chosen:
            chosen.append(canonical)
    if not chosen:
        raise ValueError(f"Choose at least one {what}")
    return ",".join(chosen)


class PolicySettings(ApiModel):
    max_auto_action_amount: int = Field(DEFAULT_POLICY.max_auto_action_amount, ge=0)
    human_approval_above_amount: int = Field(DEFAULT_POLICY.human_approval_above_amount, ge=0)
    max_contact_attempts: int = Field(DEFAULT_POLICY.max_contact_attempts, ge=0)
    escalate_after_days: int = Field(DEFAULT_POLICY.escalate_after_days, ge=0)
    case_expiry_days: int = Field(DEFAULT_POLICY.case_expiry_days, ge=1, le=365)
    min_ai_confidence: float = Field(DEFAULT_POLICY.min_ai_confidence, ge=0.0, le=1.0)
    preferred_channels: str = Field(
        DEFAULT_POLICY.preferred_channels,
        description="Channels RecoverX may message on, most preferred first: whatsapp, email",
    )
    supported_languages: str = Field(
        DEFAULT_POLICY.supported_languages, description="Message languages, most preferred first: English, Hinglish"
    )
    allowed_actions: str = Field(
        DEFAULT_POLICY.allowed_actions, description="Recovery actions the Tool Gateway may execute"
    )


class PolicyUpdate(PolicySettings):
    """What a merchant may save. Stored values are read back as-is, so older settings never fail to load."""

    @field_validator("preferred_channels")
    @classmethod
    def _channels(cls, value: str) -> str:
        return _normalized(value, CHANNELS, "channel")

    @field_validator("supported_languages")
    @classmethod
    def _languages(cls, value: str) -> str:
        return _normalized(value, LANGUAGES, "language")

    @field_validator("allowed_actions")
    @classmethod
    def _actions(cls, value: str) -> str:
        return _normalized(value, tuple(EXECUTABLE_ACTIONS), "action", key=str.upper)
