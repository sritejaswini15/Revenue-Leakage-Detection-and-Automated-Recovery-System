"""Centralized application settings.

Every environment variable the backend reads is declared here. Values are read
once at import time; tests override attributes on the ``settings`` instance.
See docs/ENVIRONMENT.md for the meaning of each variable.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Development-only defaults; validate_production() refuses to start with either.
DEV_AUTH_SECRET = "recoverx-local-development-secret"  # noqa: S105
DEV_WEBHOOK_SECRET = "recoverx_webhook_secret_key_2026"  # noqa: S105


def _clean(value: str) -> str:
    """Strip whitespace and one pair of matching surrounding quotes.

    Hosting dashboards sometimes store values with literal quotes
    (e.g. ``"secret"``); normalizing here keeps the rest of the code simple.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def _str(name: str, default: str = "") -> str:
    """Blank values count as unset, so an empty line copied from .env.example keeps the default."""
    return _clean(os.getenv(name) or "") or _clean(default)


def _optional(name: str) -> str | None:
    return _str(name) or None


def _bool(name: str, default: bool) -> bool:
    return _str(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    return int(_str(name, str(default)))


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in _str(name, default).split(",") if item.strip()]


class Settings:
    APP_NAME = "RecoverX"
    APP_VERSION = "1.0.0"

    def __init__(self) -> None:
        self.ENVIRONMENT: str = _str("ENVIRONMENT", "development").lower()
        self.LOG_LEVEL: str = _str("LOG_LEVEL", "INFO").upper()
        self.LOG_FORMAT: str = _str("LOG_FORMAT", "json" if self.ENVIRONMENT == "production" else "text").lower()
        self.SEED_DEMO_DATA: bool = _bool("SEED_DEMO_DATA", True)
        self.CORS_ORIGINS: list[str] = _list("CORS_ORIGINS", "http://localhost:3000")

        # Database: empty means a local SQLite file (development only).
        self.DATABASE_URL: str = _str("DATABASE_URL")

        # Authentication
        self.AUTH_SECRET: str = _str("AUTH_SECRET", DEV_AUTH_SECRET)
        self.AUTH_REQUIRED: bool = _bool("AUTH_REQUIRED", True)
        self.ACCESS_TOKEN_TTL_SECONDS: int = _int("ACCESS_TOKEN_TTL_SECONDS", 28800)
        self.BOOTSTRAP_ADMIN_EMAIL: str | None = _optional("BOOTSTRAP_ADMIN_EMAIL")
        self.BOOTSTRAP_ADMIN_PASSWORD: str | None = _optional("BOOTSTRAP_ADMIN_PASSWORD")
        self.BOOTSTRAP_ORGANIZATION_NAME: str = _str("BOOTSTRAP_ORGANIZATION_NAME", "RecoverX Merchant")
        self.BOOTSTRAP_RAZORPAY_ACCOUNT_ID: str | None = _optional("BOOTSTRAP_RAZORPAY_ACCOUNT_ID")

        # Payments
        self.PAYMENT_PROVIDER: str = _str("PAYMENT_PROVIDER", "simulated").lower()
        self.RAZORPAY_KEY_ID: str = _str("RAZORPAY_KEY_ID")
        self.RAZORPAY_KEY_SECRET: str = _str("RAZORPAY_KEY_SECRET")
        self.RAZORPAY_WEBHOOK_SECRET: str = _str("RAZORPAY_WEBHOOK_SECRET", DEV_WEBHOOK_SECRET)
        self.PAYMENT_LINK_TTL_HOURS: int = _int("PAYMENT_LINK_TTL_HOURS", 72)
        # Merchants may only save rzp_test_ keys unless this is explicitly enabled.
        self.ALLOW_LIVE_PAYMENTS: bool = _bool("ALLOW_LIVE_PAYMENTS", False)
        # Encrypts merchant credentials at rest (any high-entropy string).
        self.CREDENTIALS_ENCRYPTION_KEY: str = _str("CREDENTIALS_ENCRYPTION_KEY")

        # Browser sessions: an HttpOnly cookie on the frontend's origin (see docs/ARCHITECTURE.md).
        self.SESSION_COOKIE_NAME: str = _str("SESSION_COOKIE_NAME", "recoverx_session")
        self.SESSION_COOKIE_SECURE: bool = _bool("SESSION_COOKIE_SECURE", self.ENVIRONMENT == "production")

        # Durable workflows (optional)
        self.TEMPORAL_ENABLED: bool = _bool("TEMPORAL_ENABLED", False)
        self.TEMPORAL_HOST: str = _str("TEMPORAL_HOST", "localhost:7233")
        self.TEMPORAL_NAMESPACE: str = _str("TEMPORAL_NAMESPACE", "default")
        self.TEMPORAL_TASK_QUEUE: str = _str("TEMPORAL_TASK_QUEUE", "recoverx-recovery")
        self.TEMPORAL_API_KEY: str | None = _optional("TEMPORAL_API_KEY")

        # Recovery advisor: "rules" (deterministic, the default) or "anthropic" (Claude). Either way the
        # advisor only recommends; policy authorizes and the Tool Gateway executes.
        self.LLM_PROVIDER: str = _str("LLM_PROVIDER", "rules").lower()
        self.ANTHROPIC_API_KEY: str | None = _optional("ANTHROPIC_API_KEY")  # read by the Anthropic SDK
        self.LLM_MODEL: str = _str("LLM_MODEL", "claude-opus-5-5")
        self.LLM_EFFORT: str = _str("LLM_EFFORT", "low").lower()  # low | medium | high | xhigh | max
        # The advisor runs while a webhook is being processed: keep the wait bounded.
        self.LLM_TIMEOUT_SECONDS: float = float(_str("LLM_TIMEOUT_SECONDS", "20"))
        self.LLM_MAX_RETRIES: int = _int("LLM_MAX_RETRIES", 1)  # SDK retries of 429/5xx/connection errors
        # Server-side fallback when the model declines on safety grounds (Claude API only).
        self.LLM_REFUSAL_FALLBACK: bool = _bool("LLM_REFUSAL_FALLBACK", True)

        # Error reporting (optional)
        self.SENTRY_DSN: str | None = _optional("SENTRY_DSN")
        self.SENTRY_TRACES_SAMPLE_RATE: float = float(_str("SENTRY_TRACES_SAMPLE_RATE", "0"))

    @property
    def uses_llm_advisor(self) -> bool:
        return self.LLM_PROVIDER == "anthropic"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def uses_live_razorpay(self) -> bool:
        return self.PAYMENT_PROVIDER == "razorpay"

    @property
    def demo_users_enabled(self) -> bool:
        """Well-known demo logins are never created in production."""
        return not self.is_production

    @property
    def demo_reset_enabled(self) -> bool:
        return not self.is_production or self.SEED_DEMO_DATA

    def validate_production(self) -> None:
        """Fail closed: production never runs with development credentials."""
        if not self.is_production:
            return
        invalid = []
        if self.AUTH_SECRET == DEV_AUTH_SECRET or not self.AUTH_SECRET:
            invalid.append("AUTH_SECRET")
        if self.RAZORPAY_WEBHOOK_SECRET == DEV_WEBHOOK_SECRET or not self.RAZORPAY_WEBHOOK_SECRET:
            invalid.append("RAZORPAY_WEBHOOK_SECRET")
        if not self.BOOTSTRAP_ADMIN_EMAIL or not self.BOOTSTRAP_ADMIN_PASSWORD:
            invalid.append("BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD")
        elif len(self.BOOTSTRAP_ADMIN_PASSWORD) < 16:
            invalid.append("BOOTSTRAP_ADMIN_PASSWORD (16+ characters)")
        if not self.DATABASE_URL:
            invalid.append("DATABASE_URL")
        if not self.AUTH_REQUIRED:
            invalid.append("AUTH_REQUIRED (must be true)")
        if self.uses_live_razorpay and not (self.RAZORPAY_KEY_ID and self.RAZORPAY_KEY_SECRET):
            invalid.append("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET (PAYMENT_PROVIDER=razorpay)")
        if self.LLM_PROVIDER not in ("rules", "anthropic"):
            invalid.append("LLM_PROVIDER (rules or anthropic)")
        if invalid:
            raise RuntimeError("Production configuration is incomplete: " + ", ".join(invalid))


settings = Settings()
