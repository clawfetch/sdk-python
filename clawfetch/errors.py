"""ClawFetch error hierarchy — mirrors the TypeScript SDK error classes."""

from __future__ import annotations


class ClawFetchError(Exception):
    """Base error for all ClawFetch SDK errors."""

    def __init__(self, message: str, status_code: int, endpoint: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.status_code}, {self.endpoint!r})"


class PaymentError(ClawFetchError):
    """Payment-related errors (402, insufficient USDC, invalid signature)."""

    def __init__(self, message: str, status_code: int = 402, endpoint: str = "") -> None:
        super().__init__(message, status_code, endpoint)


class NetworkError(ClawFetchError):
    """Network errors (connection refused, DNS failure, timeout)."""

    def __init__(
        self, message: str, endpoint: str = "", cause: Exception | None = None
    ) -> None:
        super().__init__(message, 0, endpoint)
        self.__cause__ = cause


class RateLimitError(ClawFetchError):
    """Rate limit errors (429 Too Many Requests)."""

    def __init__(
        self, message: str, endpoint: str = "", retry_after_ms: int | None = None
    ) -> None:
        super().__init__(message, 429, endpoint)
        self.retry_after_ms = retry_after_ms


class ApiError(ClawFetchError):
    """API errors (4xx other than 402/429, 5xx)."""

    def __init__(self, message: str, status_code: int, endpoint: str = "") -> None:
        super().__init__(message, status_code, endpoint)
