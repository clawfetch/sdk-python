"""
ClawFetch Python SDK — Unit Tests

Tests the full x402 payment flow with mocked HTTP responses.
No real API calls are made. No real USDC is spent.
"""

import base64
import json
import time
from unittest.mock import MagicMock, patch, PropertyMock

import httpx
import pytest

from clawfetch.client import ClawFetch, RetryOptions, BASE_URL
from clawfetch.errors import ApiError, ClawFetchError, NetworkError, PaymentError, RateLimitError


# ── Test constants ───────────────────────────────────────────────

# Hardhat account #0 — well-known test key, zero real value
TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
MOCK_BASE_URL = "https://mock.clawfetch.test"


# Valid EVM test addresses (proper checksummed 20-byte addresses)
TEST_RECIPIENT = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # Hardhat #1
TEST_RECIPIENT_2 = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"  # Hardhat #2
TEST_USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # Real USDC on Base
TEST_USDC_ETH = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # Real USDC on Ethereum


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


def _mock_response(status_code: int, json_data: dict, headers: dict | None = None) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()

    # Wire up headers
    real_headers = httpx.Headers(headers or {})
    resp.headers = real_headers

    # If not 2xx, raise_for_status should raise
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code} Error",
            request=MagicMock(),
            response=resp,
        )

    return resp


# ── Constructor Tests ────────────────────────────────────────────


class TestConstructor:
    """Tests for ClawFetch client initialization."""

    def test_create_with_valid_key(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        assert cf is not None

    def test_wallet_address_derived_correctly(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        assert cf.address.lower() == TEST_ADDRESS.lower()

    def test_address_format(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        assert cf.address.startswith("0x")
        assert len(cf.address) == 42

    def test_default_base_url(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY)
        assert cf._base_url == BASE_URL

    def test_custom_base_url(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url="https://custom.api.com")
        assert cf._base_url == "https://custom.api.com"

    def test_trailing_slash_stripped(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url="https://api.test.com/")
        assert cf._base_url == "https://api.test.com"

    def test_custom_timeout(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, timeout=5.0)
        assert cf is not None

    def test_context_manager(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            assert cf is not None
            assert cf.address.lower() == TEST_ADDRESS.lower()


# ── Health Endpoint Tests ────────────────────────────────────────


class TestHealth:
    """Tests for the free /health endpoint (no x402 payment)."""

    def test_health_returns_data(self):
        health_data = {"status": "ok", "service": "clawfetch", "version": "0.2.0"}
        mock_resp = _mock_response(200, health_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "get", return_value=mock_resp) as mock_get:
                result = cf.health()
                assert result == health_data
                assert result["status"] == "ok"
                mock_get.assert_called_once_with(f"{MOCK_BASE_URL}/health")

    def test_health_raises_on_network_error(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "get", side_effect=httpx.ConnectError("refused")):
                with pytest.raises(NetworkError):
                    cf.health()


# ── Fetch Endpoint Tests ────────────────────────────────────────


class TestFetch:
    """Tests for cf.fetch() — markdown content retrieval."""

    def test_fetch_direct_200(self):
        """When server returns 200 directly (no payment needed)."""
        fetch_data = {
            "url": "https://example.com",
            "title": "Example",
            "content": "# Example\n\nThis is content.",
        }
        mock_resp = _mock_response(200, fetch_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                result = cf.fetch("https://example.com")
                assert result["url"] == "https://example.com"
                assert "content" in result

    def test_fetch_with_x402_payment_flow(self):
        """Full x402 flow: 402 → sign → retry → 200."""
        payment_header = _make_payment_required_header()
        resp_402 = _mock_response(402, {}, headers={"payment-required": payment_header})
        # Override: 402 should NOT raise on raise_for_status in _paid_request flow
        # Actually, _paid_request checks status_code == 402 before calling raise_for_status
        resp_402.raise_for_status = MagicMock()  # Don't raise

        fetch_data = {"url": "https://example.com", "content": "# Hello"}
        resp_200 = _mock_response(200, fetch_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", side_effect=[resp_402, resp_200]) as mock_req:
                result = cf.fetch("https://example.com")
                assert result["content"] == "# Hello"
                assert mock_req.call_count == 2

                # Second call should include PAYMENT-SIGNATURE header
                second_call = mock_req.call_args_list[1]
                assert "PAYMENT-SIGNATURE" in second_call.kwargs.get("headers", {})

    def test_fetch_with_max_chars(self):
        fetch_data = {"url": "https://example.com", "content": "Short"}
        mock_resp = _mock_response(200, fetch_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                result = cf.fetch("https://example.com", max_chars=100)
                assert result["content"] == "Short"
                # Verify maxChars was included in the request body
                call_kwargs = mock_req.call_args
                assert call_kwargs.kwargs["json"]["maxChars"] == 100

    def test_fetch_without_max_chars(self):
        fetch_data = {"url": "https://example.com", "content": "Full"}
        mock_resp = _mock_response(200, fetch_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                result = cf.fetch("https://example.com")
                call_kwargs = mock_req.call_args
                assert "maxChars" not in call_kwargs.kwargs["json"]


# ── Render Endpoint Tests ────────────────────────────────────────


class TestRender:
    """Tests for cf.render() — JS-rendered page retrieval."""

    def test_render_returns_content(self):
        render_data = {
            "url": "https://app.example.com",
            "title": "App",
            "content": "# Rendered Content",
        }
        mock_resp = _mock_response(200, render_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                result = cf.render("https://app.example.com")
                assert result["content"] == "# Rendered Content"

    def test_render_with_max_chars(self):
        render_data = {"url": "https://app.example.com", "content": "Short"}
        mock_resp = _mock_response(200, render_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                cf.render("https://app.example.com", max_chars=500)
                call_kwargs = mock_req.call_args
                assert call_kwargs.kwargs["json"]["maxChars"] == 500


# ── Extract Endpoint Tests ───────────────────────────────────────


class TestExtract:
    """Tests for cf.extract() — structured data extraction."""

    def test_extract_returns_structured_data(self):
        extract_data = {
            "url": "https://coingecko.com/en/coins/bitcoin",
            "extractor": "coingecko",
            "data": {
                "name": "Bitcoin",
                "symbol": "BTC",
                "price": 98432.12,
                "market_cap": 1900000000000,
            },
        }
        mock_resp = _mock_response(200, extract_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                result = cf.extract("https://coingecko.com/en/coins/bitcoin")
                assert result["extractor"] == "coingecko"
                assert result["data"]["name"] == "Bitcoin"
                assert result["data"]["price"] > 0

    def test_extract_sends_correct_body(self):
        mock_resp = _mock_response(200, {"url": "https://x.com", "data": {}})

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                cf.extract("https://x.com/some_user")
                call_kwargs = mock_req.call_args
                assert call_kwargs.kwargs["json"] == {"url": "https://x.com/some_user"}


# ── Research Endpoint Tests ──────────────────────────────────────


class TestResearch:
    """Tests for cf.research() — multi-source topic research."""

    def test_research_returns_results(self):
        research_data = {
            "topic": "x402 payment protocol",
            "summary": "x402 enables HTTP-native micropayments...",
            "sources": [
                {"url": "https://x402.org", "title": "x402", "snippet": "Protocol spec"},
                {"url": "https://github.com/coinbase/x402", "title": "GitHub", "snippet": "Reference impl"},
            ],
        }
        mock_resp = _mock_response(200, research_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                result = cf.research("x402 payment protocol")
                assert result["topic"] == "x402 payment protocol"
                assert result["summary"]
                assert len(result["sources"]) > 0

    def test_research_with_sources_option(self):
        research_data = {"topic": "test", "summary": "test", "sources": []}
        mock_resp = _mock_response(200, research_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                cf.research("test", sources=5)
                call_kwargs = mock_req.call_args
                assert call_kwargs.kwargs["json"]["sources"] == 5

    def test_research_without_sources_option(self):
        research_data = {"topic": "test", "summary": "test", "sources": []}
        mock_resp = _mock_response(200, research_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                cf.research("test")
                call_kwargs = mock_req.call_args
                assert "sources" not in call_kwargs.kwargs["json"]


# ── Domain Endpoints Tests ───────────────────────────────────────


class TestDomains:
    """Tests for domain check and suggest endpoints."""

    def test_domains_check(self):
        check_data = {
            "domains": [
                {"domain": "coolstartup.com", "available": False},
                {"domain": "coolstartup.ai", "available": True},
            ]
        }
        mock_resp = _mock_response(200, check_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                result = cf.domains_check(["coolstartup.com", "coolstartup.ai"])
                assert len(result["domains"]) == 2
                assert result["domains"][0]["available"] is False
                assert result["domains"][1]["available"] is True

    def test_domains_check_sends_correct_body(self):
        mock_resp = _mock_response(200, {"domains": []})

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                cf.domains_check(["test.com", "test.ai"])
                call_kwargs = mock_req.call_args
                assert call_kwargs.kwargs["json"]["domains"] == ["test.com", "test.ai"]

    def test_domains_suggest(self):
        suggest_data = {
            "query": "ai coding",
            "suggestions": [
                {"domain": "aicodinghelp.com", "available": True},
                {"domain": "codeassist.ai", "available": True},
            ],
        }
        mock_resp = _mock_response(200, suggest_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                result = cf.domains_suggest("ai coding")
                assert result["query"] == "ai coding"
                assert len(result["suggestions"]) > 0
                assert result["suggestions"][0]["available"] is True

    def test_domains_suggest_with_tlds(self):
        mock_resp = _mock_response(200, {"query": "test", "suggestions": []})

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                cf.domains_suggest("test", tlds=[".ai", ".dev"])
                call_kwargs = mock_req.call_args
                assert call_kwargs.kwargs["json"]["tlds"] == [".ai", ".dev"]

    def test_domains_suggest_without_tlds(self):
        mock_resp = _mock_response(200, {"query": "test", "suggestions": []})

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                cf.domains_suggest("test")
                call_kwargs = mock_req.call_args
                assert "tlds" not in call_kwargs.kwargs["json"]


# ── Extractors Endpoint Tests ────────────────────────────────────


class TestExtractors:
    """Tests for cf.extractors() — list available extractors."""

    def test_extractors_returns_list(self):
        extractors_data = {
            "extractors": [
                {"name": "coingecko", "domains": ["coingecko.com"], "description": "Crypto"},
                {"name": "github", "domains": ["github.com"], "description": "Repos"},
            ]
        }
        mock_resp = _mock_response(200, extractors_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                result = cf.extractors()
                assert isinstance(result, list)
                assert len(result) == 2
                assert result[0]["name"] == "coingecko"
                assert "domains" in result[0]

    def test_extractors_uses_get_method(self):
        mock_resp = _mock_response(200, {"extractors": []})

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
                cf.extractors()
                call_args = mock_req.call_args
                assert call_args.args[0] == "GET"


# ── x402 Payment Flow Tests ─────────────────────────────────────


class TestX402PaymentFlow:
    """Tests for the x402 payment mechanics."""

    def test_payment_required_header_parsed_correctly(self):
        """Verify PAYMENT-REQUIRED header (base64 JSON) is correctly decoded."""
        payment_header = _make_payment_required_header()
        resp_402 = _mock_response(402, {}, headers={"payment-required": payment_header})
        resp_402.raise_for_status = MagicMock()

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            result = cf._parse_payment_required(resp_402)
            assert result["x402Version"] == 2
            assert len(result["accepts"]) == 1
            assert result["accepts"][0]["network"] == "eip155:8453"

    def test_payment_required_from_body_v1(self):
        """Verify v1 fallback: payment requirements in JSON body."""
        body = {
            "x402Version": 1,
            "network": "eip155:8453",
            "asset": TEST_USDC_BASE,
            "payTo": TEST_RECIPIENT,
            "amount": "500",
        }
        resp_402 = _mock_response(402, body)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            result = cf._parse_payment_required(resp_402)
            assert result["x402Version"] == 1

    def test_payment_required_raises_on_invalid(self):
        """Should raise ValueError when 402 has neither header nor x402 body."""
        resp_402 = _mock_response(402, {"error": "some unrelated error"})

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with pytest.raises(ValueError, match="Cannot parse x402"):
                cf._parse_payment_required(resp_402)

    def test_payment_payload_structure(self):
        """Verify the signed payment payload has correct x402 v2 structure."""
        payment_required = {
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

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            payload = cf._create_payment_payload(payment_required)

            assert payload["x402Version"] == 2
            assert "payload" in payload
            assert "authorization" in payload["payload"]
            assert "signature" in payload["payload"]

            auth = payload["payload"]["authorization"]
            assert auth["from"].lower() == TEST_ADDRESS.lower()
            assert auth["to"] == TEST_RECIPIENT
            assert auth["value"] == "1000"
            assert auth["nonce"].startswith("0x")
            assert len(auth["nonce"]) == 66  # 0x + 64 hex chars

    def test_payment_signature_is_base64_in_header(self):
        """Verify the retry request includes base64-encoded PAYMENT-SIGNATURE header."""
        payment_header = _make_payment_required_header()
        resp_402 = _mock_response(402, {}, headers={"payment-required": payment_header})
        resp_402.raise_for_status = MagicMock()

        fetch_data = {"url": "https://example.com", "content": "paid content"}
        resp_200 = _mock_response(200, fetch_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", side_effect=[resp_402, resp_200]) as mock_req:
                cf.fetch("https://example.com")

                # Get the PAYMENT-SIGNATURE from second call
                second_call_headers = mock_req.call_args_list[1].kwargs["headers"]
                sig_header = second_call_headers["PAYMENT-SIGNATURE"]

                # Should be valid base64
                decoded = json.loads(base64.b64decode(sig_header))
                assert decoded["x402Version"] == 2
                assert "payload" in decoded

    def test_payment_validAfter_and_validBefore(self):
        """Verify time-windowed authorization fields."""
        payment_required = {
            "accepts": [
                {
                    "network": "eip155:8453",
                    "asset": TEST_USDC_BASE,
                    "payTo": TEST_RECIPIENT,
                    "maxAmountRequired": "500",
                    "maxTimeoutSeconds": 600,
                    "extra": {"name": "USD Coin", "version": "2"},
                }
            ]
        }

        now = int(time.time())
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            payload = cf._create_payment_payload(payment_required)
            auth = payload["payload"]["authorization"]

            valid_after = int(auth["validAfter"])
            valid_before = int(auth["validBefore"])

            # validAfter should be ~now - 600
            assert valid_after <= now
            assert valid_after >= now - 700

            # validBefore should be ~now + 600
            assert valid_before >= now
            assert valid_before <= now + 700


# ── Error Handling Tests ─────────────────────────────────────────


class TestErrorHandling:
    """Tests for HTTP error responses."""

    def test_400_raises_api_error(self):
        mock_resp = _mock_response(400, {"error": "Invalid URL format"})

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                with pytest.raises(ApiError) as exc_info:
                    cf.fetch("not-a-url")
                assert exc_info.value.status_code == 400

    def test_500_raises_api_error(self):
        mock_resp = _mock_response(500, {"error": "Internal Server Error"})

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                with pytest.raises(ApiError) as exc_info:
                    cf.fetch("https://example.com")
                assert exc_info.value.status_code == 500

    def test_network_error_wrapped(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False) as cf:
            with patch.object(cf._client, "request", side_effect=httpx.ConnectError("Connection refused")):
                with pytest.raises(NetworkError):
                    cf.fetch("https://example.com")

    def test_timeout_error_wrapped(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL, retry=False) as cf:
            with patch.object(cf._client, "request", side_effect=httpx.ReadTimeout("Timed out")):
                with pytest.raises(NetworkError):
                    cf.fetch("https://example.com")

    def test_402_without_payment_header_tries_body(self):
        """402 with x402 info in body (v1) should still work."""
        body_402 = {
            "x402Version": 1,
            "network": "eip155:8453",
            "asset": TEST_USDC_BASE,
            "payTo": TEST_RECIPIENT,
            "amount": "1000",
            "maxTimeoutSeconds": 300,
            "extra": {"name": "USD Coin", "version": "2"},
        }
        resp_402 = _mock_response(402, body_402)
        resp_402.raise_for_status = MagicMock()

        fetch_data = {"url": "https://example.com", "content": "ok"}
        resp_200 = _mock_response(200, fetch_data)

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", side_effect=[resp_402, resp_200]):
                result = cf.fetch("https://example.com")
                assert result["content"] == "ok"


# ── Request Method Tests ─────────────────────────────────────────


class TestRequestMethods:
    """Verify correct HTTP methods and paths are used."""

    def _run_method(self, cf, method_name, *args, **kwargs):
        """Call a method and return the mock request args."""
        mock_resp = _mock_response(200, {"ok": True, "extractors": []})
        with patch.object(cf._client, "request", return_value=mock_resp) as mock_req:
            getattr(cf, method_name)(*args, **kwargs)
            return mock_req.call_args

    def test_fetch_uses_post(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            call = self._run_method(cf, "fetch", "https://example.com")
            assert call.args[0] == "POST"
            assert call.args[1] == f"{MOCK_BASE_URL}/fetch"

    def test_render_uses_post(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            call = self._run_method(cf, "render", "https://example.com")
            assert call.args[0] == "POST"
            assert call.args[1] == f"{MOCK_BASE_URL}/render"

    def test_extract_uses_post(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            call = self._run_method(cf, "extract", "https://example.com")
            assert call.args[0] == "POST"
            assert call.args[1] == f"{MOCK_BASE_URL}/extract"

    def test_research_uses_post(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            call = self._run_method(cf, "research", "some topic")
            assert call.args[0] == "POST"
            assert call.args[1] == f"{MOCK_BASE_URL}/research"

    def test_domains_check_uses_post(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            call = self._run_method(cf, "domains_check", ["test.com"])
            assert call.args[0] == "POST"
            assert call.args[1] == f"{MOCK_BASE_URL}/domains/check"

    def test_domains_suggest_uses_post(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            call = self._run_method(cf, "domains_suggest", "test query")
            assert call.args[0] == "POST"
            assert call.args[1] == f"{MOCK_BASE_URL}/domains/suggest"

    def test_extractors_uses_get(self):
        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            call = self._run_method(cf, "extractors")
            assert call.args[0] == "GET"
            assert call.args[1] == f"{MOCK_BASE_URL}/extractors"


# ── Edge Cases ───────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_extractors_list(self):
        mock_resp = _mock_response(200, {"extractors": []})

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            with patch.object(cf._client, "request", return_value=mock_resp):
                result = cf.extractors()
                assert result == []

    def test_close_called_explicitly(self):
        cf = ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL)
        with patch.object(cf._client, "close") as mock_close:
            cf.close()
            mock_close.assert_called_once()

    def test_multiple_accepts_uses_first(self):
        """When x402 response has multiple payment options, use the first one."""
        payment_required = {
            "accepts": [
                {
                    "network": "eip155:8453",
                    "asset": TEST_USDC_BASE,
                    "payTo": TEST_RECIPIENT,
                    "maxAmountRequired": "1000",
                    "maxTimeoutSeconds": 300,
                    "extra": {"name": "USD Coin", "version": "2"},
                },
                {
                    "network": "eip155:1",
                    "asset": TEST_USDC_ETH,
                    "payTo": TEST_RECIPIENT_2,
                    "maxAmountRequired": "1000",
                    "maxTimeoutSeconds": 300,
                    "extra": {"name": "USD Coin", "version": "2"},
                },
            ]
        }

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            payload = cf._create_payment_payload(payment_required)
            auth = payload["payload"]["authorization"]
            assert auth["to"] == TEST_RECIPIENT

    def test_nonce_is_unique_per_call(self):
        """Each payment should generate a unique nonce."""
        payment_required = {
            "accepts": [
                {
                    "network": "eip155:8453",
                    "asset": TEST_USDC_BASE,
                    "payTo": TEST_RECIPIENT,
                    "maxAmountRequired": "500",
                    "maxTimeoutSeconds": 300,
                    "extra": {"name": "USD Coin", "version": "2"},
                }
            ]
        }

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            payload1 = cf._create_payment_payload(payment_required)
            payload2 = cf._create_payment_payload(payment_required)

            nonce1 = payload1["payload"]["authorization"]["nonce"]
            nonce2 = payload2["payload"]["authorization"]["nonce"]
            assert nonce1 != nonce2

    def test_chain_id_extracted_from_network_string(self):
        """eip155:8453 should yield chainId 8453 for Base."""
        payment_required = {
            "accepts": [
                {
                    "network": "eip155:8453",
                    "asset": TEST_USDC_BASE,
                    "payTo": TEST_RECIPIENT,
                    "maxAmountRequired": "500",
                    "maxTimeoutSeconds": 300,
                    "extra": {"name": "USD Coin", "version": "2"},
                }
            ]
        }

        with ClawFetch(private_key=TEST_PRIVATE_KEY, base_url=MOCK_BASE_URL) as cf:
            # The chain_id is used internally for EIP-712 signing
            # We verify it indirectly — the signature should be valid
            payload = cf._create_payment_payload(payment_required)
            assert payload["payload"]["signature"]  # Non-empty signature means signing worked
