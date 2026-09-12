class FundamentalDataProviderError(Exception):
    """A provider call failed (session establishment, non-2xx response, unexpected payload shape)."""

    def __init__(self, message: str, status_code: int | None = None, raw: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw
