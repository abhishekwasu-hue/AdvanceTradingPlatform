class BrokerError(Exception):
    """Base class for all broker adapter errors."""


class BrokerAuthenticationError(BrokerError):
    """Login/token exchange failed, or the session has expired and needs re-authentication."""


class BrokerAPIError(BrokerError):
    """A broker API call failed (non-2xx response, malformed payload, etc.)."""

    def __init__(self, message: str, status_code: int | None = None, raw: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw


class BrokerOrderRejected(BrokerError):
    """The broker explicitly rejected an order (margin, risk check, invalid instrument, ...)."""


def is_clear_rejection(exc: BaseException) -> bool:
    """The broker answered and declined the order (a definite "no order exists"), as opposed to a timeout, a 5xx or a
    rate limit - after which the order may still have reached the exchange."""
    if isinstance(exc, BrokerOrderRejected):
        return True
    if isinstance(exc, BrokerAPIError) and exc.status_code is not None:
        return 400 <= exc.status_code < 500 and exc.status_code not in (408, 429)
    return False
