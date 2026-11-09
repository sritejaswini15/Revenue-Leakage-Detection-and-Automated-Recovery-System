"""Logging and error reporting for every RecoverX process: the API, the hourly job and the Temporal worker."""

from app.core.config import settings
from app.core.logging import configure_logging


def init_sentry() -> None:
    """Report unhandled errors to Sentry when SENTRY_DSN is set.

    No request bodies, local variables or personal data are sent: stack-frame locals can hold
    customer contact details or provider secrets.
    """
    if not settings.SENTRY_DSN:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        release=f"recoverx@{settings.APP_VERSION}",
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
    )


def setup_process() -> None:
    """Configure logs (JSON in production) and error reporting from the settings."""
    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    init_sentry()
