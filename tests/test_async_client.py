"""
ClawFetch Python SDK — Async Client Unit Tests

Tests AsyncClawFetch with mocked HTTP responses. Mirrors sync test_client.py structure.
No real API calls are made. No real USDC is spent.
"""

import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from clawfetch.async_client import AsyncClawFetch
from clawfetch.client import RetryOptions, BASE_URL
from clawfetch.errors import ApiError, ClawFetchError, NetworkError, PaymentError, RateLimitError


# ── Test constants ───────────────────────────────────────────────

TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
MOCK_BASE_URL = "https://mock.clawfetch.test"

TEST_RECIPIENT = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
TEST_USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _make_payment_required_header() -> str:
    """Build a realistic base64-encoded PAYMENT-REQUIRED header (x402 v2)."""
    payload = {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": "eip155:8453",
                "asset": TEST_USDC_BASE,
                "payTo": TEST_RECIPIENT,
                "maxAmountRequired": "1000",
                "maxTimeoutSeconds": 300,
                "extra": {"name": "USD Coin", "version": "2"},
            }
        ],
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _mock_response(status_code: int, json_data: dict, headers: dict | None = None) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data)
    resp.raise_for_status = MagicMock()

    real_headers = httpx.Headers(headers or {})
    resp.headers = real_headers

    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code} Error",
            request=MagicMock(),
            response=resp,
        )

    return resp


# ── Constructor Tests ────────────────────────────────────────────


class TestAsyncConstructor:
    """Tests for AsyncClawFetch client initialization."""

    def test_create_with_valid_key(self):
        cf = AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        assert cf is not None

    def test_wallet_address_derived_correctly(self):
        cf = AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        assert cf.address.lower() == TEST_ADDRESS.lower()

    def test_default_base_url(self):
        cf = AsyncClawFetch(private_key=TEST_PRIVATE_KEY)
        assert cf._base_url == BASE_URL

    def test_custom_base_url(self):
        cf = AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url="https://custom.api.com")
        assert cf._base_url == "https://custom.api.com"

    def test_trailing_slash_stripped(self):
        cf = AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url="https://api.test.com/")
        assert cf._base_url == "https://api.test.com"

    def test_retry_defaults(self):
        cf = AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        assert cf._retry is not None
        assert cf._retry.max_retries == 3

    def test_retry_disabled(self):
        cf = AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False)
        assert cf._retry is None

    def test_custom_retry(self):
        opts = RetryOptions(max_retries=5, initial_delay_ms=100)
        cf = AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=opts)
        assert cf._retry.max_retries == 5
        assert cf._retry.initial_delay_ms == 100


# ── Async Context Manager Tests ──────────────────────────────────


class TestAsyncContextManager:
    """Tests for async with support."""

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            assert cf is not None
            assert cf.address.lower() == TEST_ADDRESS.lower()

    @pytest.mark.asyncio
    async def test_close_called_on_exit(self):
        cf = AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        with patch.object(cf._client, "aclose", new_callable=AsyncMock) as mock_close:
            async with cf:
                pass
            mock_close.assert_awaited_once()


# ── Health Endpoint Tests ────────────────────────────────────────


class TestAsyncHealth:
    """Tests for the free /health endpoint (no x402 payment)."""

    @pytest.mark.asyncio
    async def test_health_returns_data(self):
        health_data = {"status": "ok", "service": "clawfetch", "version": "0.2.0"}
        mock_resp = _mock_response(200, health_data)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "get", new_callable=AsyncMock, return_value=mock_resp):
                result = await cf.health()
                assert result == health_data
                assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_raises_on_network_error(self):
        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
                with pytest.raises(NetworkError):
                    await cf.health()

    @pytest.mark.asyncio
    async def test_health_raises_on_timeout(self):
        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "get", new_callable=AsyncMock, side_effect=httpx.TimeoutException("timeout")):
                with pytest.raises(NetworkError):
                    await cf.health()


# ── Fetch Endpoint Tests ────────────────────────────────────────


class TestAsyncFetch:
    """Tests for cf.fetch() — markdown content retrieval."""

    @pytest.mark.asyncio
    async def test_fetch_direct_200(self):
        fetch_data = {"url": "https://example.com", "title": "Example", "content": "# Hello"}
        mock_resp = _mock_response(200, fetch_data)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, return_value=mock_resp):
                result = await cf.fetch("https://example.com")
                assert result["content"] == "# Hello"

    @pytest.mark.asyncio
    async def test_fetch_with_x402_payment_flow(self):
        """Full x402 flow: 402 → sign → retry → 200."""
        payment_header = _make_payment_required_header()
        resp_402 = _mock_response(402, {}, headers={"payment-required": payment_header})
        resp_402.raise_for_status = MagicMock()

        fetch_data = {"url": "https://example.com", "content": "# Paid Content"}
        resp_200 = _mock_response(200, fetch_data)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(side_effect=[resp_402, resp_200])
            with patch.object(cf._client, "request", mock_request):
                result = await cf.fetch("https://example.com")
                assert result["content"] == "# Paid Content"
                assert mock_request.call_count == 2

                # Second call should include payment header
                second_call = mock_request.call_args_list[1]
                assert "PAYMENT-SIGNATURE" in second_call.kwargs.get("headers", {})

    @pytest.mark.asyncio
    async def test_fetch_with_max_chars(self):
        mock_resp = _mock_response(200, {"url": "u", "content": "x"})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.fetch("https://example.com", max_chars=200)
                call_kwargs = mock_request.call_args
                assert call_kwargs.kwargs["json"]["maxChars"] == 200


# ── Render Endpoint Tests ────────────────────────────────────────


class TestAsyncRender:
    """Tests for cf.render()."""

    @pytest.mark.asyncio
    async def test_render_returns_content(self):
        render_data = {"url": "https://app.example.com", "content": "# Rendered"}
        mock_resp = _mock_response(200, render_data)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, return_value=mock_resp):
                result = await cf.render("https://app.example.com")
                assert result["content"] == "# Rendered"

    @pytest.mark.asyncio
    async def test_render_with_max_chars(self):
        mock_resp = _mock_response(200, {"url": "u", "content": "x"})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.render("https://app.example.com", max_chars=500)
                assert mock_request.call_args.kwargs["json"]["maxChars"] == 500


# ── Extract Endpoint Tests ───────────────────────────────────────


class TestAsyncExtract:
    """Tests for cf.extract()."""

    @pytest.mark.asyncio
    async def test_extract_returns_structured_data(self):
        extract_data = {
            "url": "https://coingecko.com/en/coins/bitcoin",
            "extractor": "coingecko",
            "data": {"name": "Bitcoin", "price": 98432.12},
        }
        mock_resp = _mock_response(200, extract_data)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, return_value=mock_resp):
                result = await cf.extract("https://coingecko.com/en/coins/bitcoin")
                assert result["extractor"] == "coingecko"
                assert result["data"]["name"] == "Bitcoin"


# ── Research Endpoint Tests ──────────────────────────────────────


class TestAsyncResearch:
    """Tests for cf.research()."""

    @pytest.mark.asyncio
    async def test_research_returns_results(self):
        research_data = {
            "topic": "x402",
            "summary": "x402 enables micropayments...",
            "sources": [{"url": "https://x402.org", "title": "x402"}],
        }
        mock_resp = _mock_response(200, research_data)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, return_value=mock_resp):
                result = await cf.research("x402")
                assert result["summary"]
                assert len(result["sources"]) > 0

    @pytest.mark.asyncio
    async def test_research_with_sources_option(self):
        mock_resp = _mock_response(200, {"topic": "t", "summary": "s", "sources": []})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.research("test", sources=5)
                assert mock_request.call_args.kwargs["json"]["sources"] == 5


# ── Domain Endpoints Tests ───────────────────────────────────────


class TestAsyncDomains:
    """Tests for domain check and suggest endpoints."""

    @pytest.mark.asyncio
    async def test_domains_check(self):
        check_data = {
            "domains": [
                {"domain": "test.com", "available": False},
                {"domain": "test.ai", "available": True},
            ]
        }
        mock_resp = _mock_response(200, check_data)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, return_value=mock_resp):
                result = await cf.domains_check(["test.com", "test.ai"])
                assert len(result["domains"]) == 2

    @pytest.mark.asyncio
    async def test_domains_suggest(self):
        suggest_data = {
            "query": "ai coding",
            "suggestions": [{"domain": "aicode.ai", "available": True}],
        }
        mock_resp = _mock_response(200, suggest_data)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, return_value=mock_resp):
                result = await cf.domains_suggest("ai coding")
                assert result["query"] == "ai coding"

    @pytest.mark.asyncio
    async def test_domains_suggest_with_tlds(self):
        mock_resp = _mock_response(200, {"query": "t", "suggestions": []})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.domains_suggest("test", tlds=[".ai", ".dev"])
                assert mock_request.call_args.kwargs["json"]["tlds"] == [".ai", ".dev"]


# ── Extractors Endpoint Tests ────────────────────────────────────


class TestAsyncExtractors:
    """Tests for cf.extractors()."""

    @pytest.mark.asyncio
    async def test_extractors_returns_list(self):
        data = {
            "extractors": [
                {"name": "coingecko", "domains": ["coingecko.com"]},
                {"name": "github", "domains": ["github.com"]},
            ]
        }
        mock_resp = _mock_response(200, data)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, return_value=mock_resp):
                result = await cf.extractors()
                assert isinstance(result, list)
                assert len(result) == 2

    @pytest.mark.asyncio
    async def test_extractors_uses_get(self):
        mock_resp = _mock_response(200, {"extractors": []})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.extractors()
                assert mock_request.call_args.args[0] == "GET"


# ── Error Handling Tests ─────────────────────────────────────────


class TestAsyncErrorHandling:
    """Tests for HTTP error responses in async client."""

    @pytest.mark.asyncio
    async def test_400_raises_api_error(self):
        mock_resp = _mock_response(400, {"error": "Invalid URL"})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, return_value=mock_resp):
                with pytest.raises(ApiError) as exc_info:
                    await cf.fetch("not-a-url")
                assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_500_raises_api_error_with_retry_disabled(self):
        mock_resp = _mock_response(500, {"error": "Internal Server Error"})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, return_value=mock_resp):
                with pytest.raises(ApiError) as exc_info:
                    await cf.fetch("https://example.com")
                assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_network_error_wrapped(self):
        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
                with pytest.raises(NetworkError):
                    await cf.fetch("https://example.com")

    @pytest.mark.asyncio
    async def test_timeout_error_wrapped(self):
        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False) as cf:
            with patch.object(cf._client, "request", new_callable=AsyncMock, side_effect=httpx.ReadTimeout("timeout")):
                with pytest.raises(NetworkError):
                    await cf.fetch("https://example.com")


# ── Async Retry Tests ────────────────────────────────────────────


class TestAsyncRetry:
    """Tests for retry logic in async client."""

    @pytest.mark.asyncio
    async def test_retries_on_429(self):
        """Should retry on 429 and succeed on next attempt."""
        resp_429 = _mock_response(429, {"error": "rate limited"}, headers={"retry-after": "0"})
        resp_200 = _mock_response(200, {"url": "u", "content": "ok"})

        opts = RetryOptions(max_retries=2, initial_delay_ms=1)  # fast retry for test

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=opts) as cf:
            mock_request = AsyncMock(side_effect=[resp_429, resp_200])
            with patch.object(cf._client, "request", mock_request):
                result = await cf.fetch("https://example.com")
                assert result["content"] == "ok"
                assert mock_request.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_503(self):
        """Should retry on 503 and succeed on next attempt."""
        resp_503 = _mock_response(503, {"error": "unavailable"})
        resp_200 = _mock_response(200, {"url": "u", "content": "ok"})

        opts = RetryOptions(max_retries=2, initial_delay_ms=1)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=opts) as cf:
            mock_request = AsyncMock(side_effect=[resp_503, resp_200])
            with patch.object(cf._client, "request", mock_request):
                result = await cf.fetch("https://example.com")
                assert result["content"] == "ok"

    @pytest.mark.asyncio
    async def test_no_retry_on_400(self):
        """Should NOT retry on 400 client error."""
        resp_400 = _mock_response(400, {"error": "bad request"})

        opts = RetryOptions(max_retries=3, initial_delay_ms=1)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=opts) as cf:
            mock_request = AsyncMock(return_value=resp_400)
            with patch.object(cf._client, "request", mock_request):
                with pytest.raises(ApiError):
                    await cf.fetch("bad")
                assert mock_request.call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_retries_network_errors(self):
        """Should retry on network errors (ConnectError)."""
        resp_200 = _mock_response(200, {"url": "u", "content": "ok"})

        opts = RetryOptions(max_retries=2, initial_delay_ms=1)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=opts) as cf:
            mock_request = AsyncMock(side_effect=[httpx.ConnectError("refused"), resp_200])
            with patch.object(cf._client, "request", mock_request):
                result = await cf.fetch("https://example.com")
                assert result["content"] == "ok"
                assert mock_request.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_then_raises(self):
        """After all retries exhausted, should raise the last error."""
        resp_503 = _mock_response(503, {"error": "unavailable"})

        opts = RetryOptions(max_retries=2, initial_delay_ms=1)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=opts) as cf:
            mock_request = AsyncMock(return_value=resp_503)
            with patch.object(cf._client, "request", mock_request):
                with pytest.raises(ApiError) as exc_info:
                    await cf.fetch("https://example.com")
                assert exc_info.value.status_code == 503
                assert mock_request.call_count == 3  # 1 + 2 retries

    @pytest.mark.asyncio
    async def test_uses_async_sleep_not_time_sleep(self):
        """Verify async retry uses asyncio.sleep, not blocking time.sleep."""
        resp_429 = _mock_response(429, {"error": "limited"}, headers={"retry-after": "0"})
        resp_200 = _mock_response(200, {"url": "u", "content": "ok"})

        opts = RetryOptions(max_retries=1, initial_delay_ms=1)

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=opts) as cf:
            mock_request = AsyncMock(side_effect=[resp_429, resp_200])
            with patch.object(cf._client, "request", mock_request):
                with patch("clawfetch.async_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    await cf.fetch("https://example.com")
                    mock_sleep.assert_awaited_once()


# ── Request Method Tests ─────────────────────────────────────────


class TestAsyncRequestMethods:
    """Verify correct HTTP methods and paths are used."""

    @pytest.mark.asyncio
    async def test_fetch_uses_post(self):
        mock_resp = _mock_response(200, {"url": "u", "content": "c"})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.fetch("https://example.com")
                assert mock_request.call_args.args[0] == "POST"
                assert mock_request.call_args.args[1] == f"{MOCK_BASE_URL}/fetch"

    @pytest.mark.asyncio
    async def test_render_uses_post(self):
        mock_resp = _mock_response(200, {"url": "u", "content": "c"})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.render("https://example.com")
                assert mock_request.call_args.args[0] == "POST"

    @pytest.mark.asyncio
    async def test_extract_uses_post(self):
        mock_resp = _mock_response(200, {"url": "u", "data": {}})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.extract("https://example.com")
                assert mock_request.call_args.args[0] == "POST"

    @pytest.mark.asyncio
    async def test_research_uses_post(self):
        mock_resp = _mock_response(200, {"topic": "t", "summary": "s", "sources": []})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.research("test")
                assert mock_request.call_args.args[0] == "POST"

    @pytest.mark.asyncio
    async def test_domains_check_uses_post(self):
        mock_resp = _mock_response(200, {"domains": []})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.domains_check(["test.com"])
                assert mock_request.call_args.args[0] == "POST"

    @pytest.mark.asyncio
    async def test_domains_suggest_uses_post(self):
        mock_resp = _mock_response(200, {"query": "q", "suggestions": []})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(return_value=mock_resp)
            with patch.object(cf._client, "request", mock_request):
                await cf.domains_suggest("test")
                assert mock_request.call_args.args[0] == "POST"


# ── x402 Payment Tests (async) ──────────────────────────────────


class TestAsyncX402Payment:
    """Tests specific to x402 payment flow in async context."""

    @pytest.mark.asyncio
    async def test_payment_signature_in_retry_header(self):
        """Verify signed payment is base64-encoded and in the retry header."""
        payment_header = _make_payment_required_header()
        resp_402 = _mock_response(402, {}, headers={"payment-required": payment_header})
        resp_200 = _mock_response(200, {"url": "u", "content": "paid"})

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            mock_request = AsyncMock(side_effect=[resp_402, resp_200])
            with patch.object(cf._client, "request", mock_request):
                result = await cf.fetch("https://example.com")
                assert result["content"] == "paid"

                # Verify second call has payment
                second_call_headers = mock_request.call_args_list[1].kwargs["headers"]
                sig = second_call_headers["PAYMENT-SIGNATURE"]
                decoded = json.loads(base64.b64decode(sig))
                assert decoded["x402Version"] == 2
                assert "payload" in decoded

    @pytest.mark.asyncio
    async def test_payment_nonce_unique(self):
        """Each async payment call generates unique nonce."""
        payment_required = {
            "accepts": [{
                "network": "eip155:8453",
                "asset": TEST_USDC_BASE,
                "payTo": TEST_RECIPIENT,
                "maxAmountRequired": "500",
                "maxTimeoutSeconds": 300,
                "extra": {"name": "USD Coin", "version": "2"},
            }]
        }

        async with AsyncClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            p1 = cf._create_payment_payload(payment_required)
            p2 = cf._create_payment_payload(payment_required)
            assert p1["payload"]["authorization"]["nonce"] != p2["payload"]["authorization"]["nonce"]
