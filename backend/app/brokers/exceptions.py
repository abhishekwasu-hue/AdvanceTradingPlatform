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
