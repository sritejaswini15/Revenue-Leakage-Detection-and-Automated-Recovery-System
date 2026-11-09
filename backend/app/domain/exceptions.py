"""Business exceptions. The API layer maps each class to an HTTP status code."""


class RecoverXError(Exception):
    """Base class for expected, user-facing failures."""


class InvalidRequestError(RecoverXError, ValueError):
    """The input is malformed or unsupported."""


class AuthenticationError(RecoverXError):
    pass


class PermissionDeniedError(RecoverXError):
    pass


class NotFoundError(RecoverXError):
    pass


class ConflictError(RecoverXError):
    """The request conflicts with the current state of a resource."""


class PolicyViolationError(ConflictError):
    """Merchant policy forbids the requested action."""


class NotRetryableError(ConflictError):
    """The provider cannot retry this payment (e.g. it failed outright); send a payment link instead."""


class ExpiredError(RecoverXError):
    pass


class RateLimitedError(RecoverXError):
    pass


class ProviderError(RecoverXError):
    """An external provider rejected or failed the request."""
