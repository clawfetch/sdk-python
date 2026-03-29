"""
ClawFetch Python SDK — Retry Logic & Error Classification Tests

Tests retry behavior, error hierarchy, and configurable retry options.
"""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from clawfetch import ClawFetch, RetryOptions
from clawfetch.errors import ApiError, ClawFetchError, NetworkError, PaymentError, RateLimitError

TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
MOCK_BASE_URL = "https://mock.clawfetch.test"


def _mock_response(status_code: int, json_data: dict, headers: dict | None = None):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)
    resp.headers = httpx.Headers(headers or {})
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=resp
        )
    return resp


# ── Error Hierarchy Tests ────────────────────────────────────────


class TestErrorHierarchy:
    """All errors extend ClawFetchError."""

    def test_payment_error_is_clawfetch_error(self):
        err = PaymentError("test", 402, "/fetch")
        assert isinstance(err, ClawFetchError)
        assert err.status_code == 402
        assert err.endpoint == "/fetch"

    def test_network_error_is_clawfetch_error(self):
        err = NetworkError("test", "/fetch")
        assert isinstance(err, ClawFetchError)
        assert err.status_code == 0

    def test_rate_limit_error_is_clawfetch_error(self):
        err = RateLimitError("test", "/fetch", retry_after_ms=5000)
        assert isinstance(err, ClawFetchError)
        assert err.status_code == 429
        assert err.retry_after_ms == 5000

    def test_api_error_is_clawfetch_error(self):
        err = ApiError("test", 500, "/fetch")
        assert isinstance(err, ClawFetchError)
        assert err.status_code == 500

    def test_error_repr(self):
        err = ApiError("bad request", 400, "/extract")
        assert "ApiError" in repr(err)
        assert "400" in repr(err)

    def test_network_error_preserves_cause(self):
        cause = ConnectionError("refused")
        err = NetworkError("fail", "/fetch", cause=cause)
        assert err.__cause__ is cause

    def test_errors_are_catchable_by_base_class(self):
        """All specific errors can be caught with 'except ClawFetchError'."""
        for ErrorCls, args in [
            (PaymentError, ("msg", 402, "/p")),
            (NetworkError, ("msg", "/p")),
            (RateLimitError, ("msg", "/p")),
            (ApiError, ("msg", 500, "/p")),
        ]:
            try:
                raise ErrorCls(*args)
            except ClawFetchError:
                pass  # Expected


# ── Retry Behavior Tests ────────────────────────────────────────


class TestRetryBehavior:
    """Tests for automatic retry with backoff."""

    def test_retry_on_503_then_success(self):
        resp_503 = _mock_response(503, {"error": "Unavailable"})
        resp_200 = _mock_response(200, {"content": "ok"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=2, initial_delay_ms=1, max_delay_ms=1, backoff_multiplier=1),
        ) as cf:
            with patch.object(cf._client, "request", side_effect=[resp_503, resp_200]) as mock_req:
                result = cf.fetch("https://example.com")
                assert result["content"] == "ok"
                assert mock_req.call_count == 2

    def test_retry_on_502_then_success(self):
        resp_502 = _mock_response(502, {"error": "Bad Gateway"})
        resp_200 = _mock_response(200, {"content": "ok"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=2, initial_delay_ms=1, max_delay_ms=1, backoff_multiplier=1),
        ) as cf:
            with patch.object(cf._client, "request", side_effect=[resp_502, resp_200]) as mock_req:
                result = cf.fetch("https://example.com")
                assert result["content"] == "ok"
                assert mock_req.call_count == 2

    def test_retry_on_500_then_success(self):
        resp_500 = _mock_response(500, {"error": "Internal"})
        resp_200 = _mock_response(200, {"content": "ok"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=2, initial_delay_ms=1, max_delay_ms=1, backoff_multiplier=1),
        ) as cf:
            with patch.object(cf._client, "request", side_effect=[resp_500, resp_200]):
                result = cf.fetch("https://example.com")
                assert result["content"] == "ok"

    def test_retry_on_429_then_success(self):
        resp_429 = _mock_response(429, {"error": "Rate limited"}, {"retry-after": "1"})
        resp_200 = _mock_response(200, {"content": "ok"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=2, initial_delay_ms=1, max_delay_ms=10, backoff_multiplier=1),
        ) as cf:
            with patch.object(cf._client, "request", side_effect=[resp_429, resp_200]):
                result = cf.fetch("https://example.com")
                assert result["content"] == "ok"

    def test_retry_on_network_error_then_success(self):
        resp_200 = _mock_response(200, {"content": "ok"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=2, initial_delay_ms=1, max_delay_ms=1, backoff_multiplier=1),
        ) as cf:
            with patch.object(
                cf._client, "request",
                side_effect=[httpx.ConnectError("refused"), resp_200],
            ):
                result = cf.fetch("https://example.com")
                assert result["content"] == "ok"

    def test_retry_on_timeout_then_success(self):
        resp_200 = _mock_response(200, {"content": "ok"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=2, initial_delay_ms=1, max_delay_ms=1, backoff_multiplier=1),
        ) as cf:
            with patch.object(
                cf._client, "request",
                side_effect=[httpx.ReadTimeout("timeout"), resp_200],
            ):
                result = cf.fetch("https://example.com")
                assert result["content"] == "ok"

    def test_exhausted_retries_raises_api_error(self):
        resp_503 = _mock_response(503, {"error": "Unavailable"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=2, initial_delay_ms=1, max_delay_ms=1, backoff_multiplier=1),
        ) as cf:
            with patch.object(cf._client, "request", return_value=resp_503) as mock_req:
                with pytest.raises(ApiError) as exc_info:
                    cf.fetch("https://example.com")
                assert exc_info.value.status_code == 503
                assert mock_req.call_count == 3  # 1 initial + 2 retries

    def test_exhausted_retries_on_429_raises_rate_limit_error(self):
        resp_429 = _mock_response(429, {"error": "Rate limited"}, {"retry-after": "1"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=1, initial_delay_ms=1, max_delay_ms=1, backoff_multiplier=1),
        ) as cf:
            with patch.object(cf._client, "request", return_value=resp_429) as mock_req:
                with pytest.raises(RateLimitError) as exc_info:
                    cf.fetch("https://example.com")
                assert exc_info.value.retry_after_ms == 1000
                assert mock_req.call_count == 2

    def test_exhausted_retries_on_network_error(self):
        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=1, initial_delay_ms=1, max_delay_ms=1, backoff_multiplier=1),
        ) as cf:
            with patch.object(
                cf._client, "request",
                side_effect=httpx.ConnectError("refused"),
            ) as mock_req:
                with pytest.raises(NetworkError):
                    cf.fetch("https://example.com")
                assert mock_req.call_count == 2


# ── No-Retry Tests ──────────────────────────────────────────────


class TestNoRetry:
    """Tests with retry disabled."""

    def test_no_retry_on_503(self):
        resp_503 = _mock_response(503, {"error": "Unavailable"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False
        ) as cf:
            with patch.object(cf._client, "request", return_value=resp_503) as mock_req:
                with pytest.raises(ApiError):
                    cf.fetch("https://example.com")
                assert mock_req.call_count == 1

    def test_no_retry_on_429(self):
        resp_429 = _mock_response(429, {"error": "Rate limited"}, {"retry-after": "5"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False
        ) as cf:
            with patch.object(cf._client, "request", return_value=resp_429) as mock_req:
                with pytest.raises(RateLimitError) as exc_info:
                    cf.fetch("https://example.com")
                assert exc_info.value.retry_after_ms == 5000
                assert mock_req.call_count == 1

    def test_no_retry_on_network_error(self):
        with ClawFetch(
            private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False
        ) as cf:
            with patch.object(
                cf._client, "request", side_effect=httpx.ConnectError("refused")
            ) as mock_req:
                with pytest.raises(NetworkError):
                    cf.fetch("https://example.com")
                assert mock_req.call_count == 1


# ── Non-Retryable Error Tests ───────────────────────────────────


class TestNonRetryable:
    """Errors that should NOT trigger retry."""

    def test_400_not_retried(self):
        resp_400 = _mock_response(400, {"error": "Bad request"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=3, initial_delay_ms=1),
        ) as cf:
            with patch.object(cf._client, "request", return_value=resp_400) as mock_req:
                with pytest.raises(ApiError) as exc_info:
                    cf.fetch("https://example.com")
                assert exc_info.value.status_code == 400
                assert mock_req.call_count == 1

    def test_401_not_retried(self):
        resp_401 = _mock_response(401, {"error": "Unauthorized"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=3, initial_delay_ms=1),
        ) as cf:
            with patch.object(cf._client, "request", return_value=resp_401) as mock_req:
                with pytest.raises(ApiError):
                    cf.fetch("https://example.com")
                assert mock_req.call_count == 1

    def test_404_not_retried(self):
        resp_404 = _mock_response(404, {"error": "Not found"})

        with ClawFetch(
            private_key=TEST_PRIVATE_KEY,
            base_url=MOCK_BASE_URL,
            retry=RetryOptions(max_retries=3, initial_delay_ms=1),
        ) as cf:
            with patch.object(cf._client, "request", return_value=resp_404) as mock_req:
                with pytest.raises(ApiError):
                    cf.fetch("https://example.com")
                assert mock_req.call_count == 1


# ── RetryOptions Tests ──────────────────────────────────────────


class TestRetryOptions:
    """Tests for RetryOptions configuration."""

    def test_default_values(self):
        opts = RetryOptions()
        assert opts.max_retries == 3
        assert opts.initial_delay_ms == 500
        assert opts.max_delay_ms == 10_000
        assert opts.backoff_multiplier == 2.0

    def test_custom_values(self):
        opts = RetryOptions(
            max_retries=5, initial_delay_ms=100, max_delay_ms=5000, backoff_multiplier=3.0
        )
        assert opts.max_retries == 5
        assert opts.initial_delay_ms == 100
        assert opts.max_delay_ms == 5000
        assert opts.backoff_multiplier == 3.0

    def test_retry_true_uses_defaults(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=True)
        assert cf._retry is not None
        assert cf._retry.max_retries == 3

    def test_retry_none_uses_defaults(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=None)
        assert cf._retry is not None

    def test_retry_false_disables(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False)
        assert cf._retry is None


# ── Retry-After Header Parsing Tests ────────────────────────────


class TestRetryAfterParsing:
    """Tests for Retry-After header parsing."""

    def test_integer_seconds(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        headers = httpx.Headers({"retry-after": "5"})
        assert cf._parse_retry_after(headers) == 5000

    def test_float_seconds(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        headers = httpx.Headers({"retry-after": "1.5"})
        assert cf._parse_retry_after(headers) == 1500

    def test_missing_header(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        headers = httpx.Headers({})
        assert cf._parse_retry_after(headers) is None

    def test_invalid_value(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        headers = httpx.Headers({"retry-after": "not-a-number"})
        assert cf._parse_retry_after(headers) is None


# ── Health Error Classification Tests ────────────────────────────


class TestHealthErrors:
    """Health endpoint should raise NetworkError on connection failure."""

    def test_health_connect_error_raises_network_error(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "get", side_effect=httpx.ConnectError("refused")):
                with pytest.raises(NetworkError) as exc_info:
                    cf.health()
                assert "Health check failed" in str(exc_info.value)

    def test_health_timeout_raises_network_error(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "get", side_effect=httpx.ReadTimeout("timeout")):
                with pytest.raises(NetworkError):
                    cf.health()
