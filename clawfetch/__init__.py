"""ClawFetch — Web Intelligence API for AI Agents (x402-native)"""

from .client import ClawFetch, RetryOptions
from .errors import ApiError, ClawFetchError, NetworkError, PaymentError, RateLimitError

__all__ = [
    "ClawFetch",
    "RetryOptions",
    "ClawFetchError",
    "PaymentError",
    "NetworkError",
    "RateLimitError",
    "ApiError",
]
__version__ = "0.1.0"
