"""ClawFetch Async Python SDK — async/await support using httpx.AsyncClient."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data

from .client import RetryOptions, BASE_URL, _RETRYABLE_STATUS_CODES
from .errors import ApiError, ClawFetchError, NetworkError, PaymentError, RateLimitError

logger = logging.getLogger("clawfetch.async")


class AsyncClawFetch:
    """Async client for the ClawFetch Web Intelligence API.

    Mirrors all methods from the sync ``ClawFetch`` client, but uses
    ``httpx.AsyncClient`` and ``async/await`` for non-blocking I/O.

    Handles the full x402 payment flow asynchronously:
    1. Make request → get 402 with payment requirements
    2. Sign EIP-3009 gasless USDC transfer on Base
    3. Retry with PAYMENT-SIGNATURE header → get data

    Usage::

        import asyncio
        from clawfetch import AsyncClawFetch

        async def main():
            async with AsyncClawFetch(private_key="0x...") as cf:
                btc = await cf.extract("https://coingecko.com/en/coins/bitcoin")
                print(btc["data"])

        asyncio.run(main())
    """

    def __init__(
        self,
        private_key: str,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        retry: RetryOptions | bool | None = None,
        debug: bool = False,
    ):
        """Initialize AsyncClawFetch client.

        Args:
            private_key: Ethereum private key (hex string with 0x prefix).
            base_url: API base URL. Defaults to https://api.clawfetch.ai.
            timeout: Request timeout in seconds. Defaults to 30.
            retry: Retry configuration. Pass False to disable, True or None for defaults,
                   or a RetryOptions instance for custom config.
            debug: Enable debug logging.
        """
        self._account = Account.from_key(private_key)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

        # Configure retry
        if retry is False:
            self._retry: RetryOptions | None = None
        elif retry is True or retry is None:
            self._retry = RetryOptions()
        elif isinstance(retry, RetryOptions):
            self._retry = retry
        else:
            self._retry = RetryOptions()

        if debug:
            logger.setLevel(logging.DEBUG)
            if not logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(
                    logging.Formatter("[clawfetch.async] %(levelname)s %(message)s")
                )
                logger.addHandler(handler)

    @property
    def address(self) -> str:
        """Wallet address derived from the private key."""
        return self._account.address

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ─── Public endpoints ──────────────────────────────────────

    async def fetch(self, url: str, *, max_chars: int | None = None) -> dict:
        """Fetch a URL as clean markdown ($0.001).

        Args:
            url: URL to fetch.
            max_chars: Maximum characters to return.

        Returns:
            Dict with url, title, content, contentType fields.

        Raises:
            PaymentError: If x402 payment fails.
            RateLimitError: If rate limited (429).
            ApiError: If server returns 4xx/5xx.
            NetworkError: If connection fails.
        """
        body: dict = {"url": url}
        if max_chars:
            body["maxChars"] = max_chars
        return await self._paid_post("/fetch", body)

    async def render(self, url: str, *, max_chars: int | None = None) -> dict:
        """Render JS-heavy page with stealth browser ($0.005).

        Args:
            url: URL to render.
            max_chars: Maximum characters to return.

        Returns:
            Dict with url, title, content fields.
        """
        body: dict = {"url": url}
        if max_chars:
            body["maxChars"] = max_chars
        return await self._paid_post("/render", body)

    async def extract(self, url: str) -> dict:
        """Extract structured data from supported URL ($0.008).

        Args:
            url: URL to extract from (must match a supported extractor).

        Returns:
            Dict with url, extractor, data fields.
        """
        return await self._paid_post("/extract", {"url": url})

    async def research(self, topic: str, *, sources: int | None = None) -> dict:
        """Multi-source research on a topic ($0.02).

        Args:
            topic: Research topic/query.
            sources: Number of sources to consult.

        Returns:
            Dict with topic, summary, sources fields.
        """
        body: dict = {"topic": topic}
        if sources:
            body["sources"] = sources
        return await self._paid_post("/research", body)

    async def domains_check(self, domains: list[str]) -> dict:
        """Check domain availability ($0.008).

        Args:
            domains: List of domain names to check.

        Returns:
            Dict with domains list, each having domain and available fields.
        """
        return await self._paid_post("/domains/check", {"domains": domains})

    async def domains_suggest(self, query: str, *, tlds: list[str] | None = None) -> dict:
        """Generate domain suggestions ($0.008).

        Args:
            query: Topic or keyword for domain suggestions.
            tlds: Preferred TLDs (e.g., [".ai", ".dev"]).

        Returns:
            Dict with query and suggestions list.
        """
        body: dict = {"query": query}
        if tlds:
            body["tlds"] = tlds
        return await self._paid_post("/domains/suggest", body)

    async def parse(
        self,
        source: str | bytes,
        *,
        filename: str | None = None,
        format: str | None = None,
    ) -> dict:
        """Parse an office document into GitHub-Flavored Markdown ($0.005).

        Supports docx, pptx, xlsx, pdf, odt, ods, odp, rtf, epub, csv, doc, ppt.
        No OCR: scanned/image-only PDFs are rejected with HTTP 422.

        Args:
            source: A document URL (http/https) or raw document bytes.
            filename: Optional filename hint used for format detection.
            format: Optional explicit format override (e.g. "csv").

        Returns:
            Dict with markdown, format, and chars fields.
        """
        import base64 as _b64

        body: dict = {}
        if isinstance(source, bytes):
            body["base64"] = _b64.b64encode(source).decode()
        else:
            body["url"] = source
        if filename:
            body["filename"] = filename
        if format:
            body["format"] = format
        return await self._paid_post("/parse", body)

    async def extractors(self) -> list[dict]:
        """List available extractors ($0.001).

        Returns:
            List of extractor dicts with name, domains, description, fields.
        """
        resp = await self._paid_request("GET", "/extractors")
        return resp.get("extractors", [])

    async def health(self) -> dict:
        """Check service health (free, no payment required).

        Returns:
            Dict with status, service, version fields.

        Raises:
            NetworkError: If connection to API fails.
        """
        try:
            r = await self._client.get(f"{self._base_url}/health")
            r.raise_for_status()
            return r.json()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise NetworkError(
                f"Health check failed: {exc}", "/health", cause=exc
            ) from exc

    # ─── Retry engine ──────────────────────────────────────────

    def _should_retry(self, status_code: int) -> bool:
        """Determine if a status code is retryable."""
        return status_code in _RETRYABLE_STATUS_CODES

    def _get_retry_delay_ms(self, attempt: int, retry_after_ms: int | None = None) -> int:
        """Calculate delay for a retry attempt with exponential backoff + jitter."""
        if self._retry is None:
            return 0

        if retry_after_ms and retry_after_ms > 0:
            return retry_after_ms

        delay = self._retry.initial_delay_ms * (
            self._retry.backoff_multiplier ** attempt
        )
        # Add jitter (±25%)
        jitter = delay * 0.25
        delay = delay + random.uniform(-jitter, jitter)
        return min(int(delay), self._retry.max_delay_ms)

    def _parse_retry_after(self, headers: httpx.Headers) -> int | None:
        """Parse Retry-After header into milliseconds."""
        val = headers.get("retry-after")
        if val is None:
            return None
        try:
            return int(float(val) * 1000)
        except (ValueError, TypeError):
            return None

    # ─── x402 payment flow with retry ──────────────────────────

    async def _paid_post(self, path: str, body: dict) -> dict:
        return await self._paid_request("POST", path, json_body=body)

    async def _paid_request(
        self, method: str, path: str, json_body: dict | None = None
    ) -> dict:
        url = f"{self._base_url}{path}"
        kwargs: dict = {}
        if json_body is not None:
            kwargs["json"] = json_body

        max_attempts = (self._retry.max_retries + 1) if self._retry else 1
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            try:
                logger.debug(
                    "Request %s %s (attempt %d/%d)", method, path, attempt + 1, max_attempts
                )

                # First request — expect 402
                resp = await self._client.request(method, url, **kwargs)

                if resp.status_code == 402:
                    # x402 payment flow
                    payment_required = self._parse_payment_required(resp)
                    payment_payload = self._create_payment_payload(payment_required)
                    encoded = base64.b64encode(
                        json.dumps(payment_payload).encode()
                    ).decode()

                    # Retry with payment header
                    headers = {"PAYMENT-SIGNATURE": encoded}
                    resp = await self._client.request(method, url, headers=headers, **kwargs)

                # Check for retryable errors
                if resp.status_code == 429:
                    retry_after_ms = self._parse_retry_after(resp.headers)
                    if attempt < max_attempts - 1 and self._retry:
                        delay_ms = self._get_retry_delay_ms(attempt, retry_after_ms)
                        logger.debug(
                            "Rate limited (429), retrying in %dms", delay_ms
                        )
                        await asyncio.sleep(delay_ms / 1000)
                        continue
                    raise RateLimitError(
                        f"Rate limited on {path}",
                        endpoint=path,
                        retry_after_ms=retry_after_ms,
                    )

                if resp.status_code >= 500 and self._should_retry(resp.status_code):
                    if attempt < max_attempts - 1 and self._retry:
                        delay_ms = self._get_retry_delay_ms(attempt)
                        logger.debug(
                            "Server error %d, retrying in %dms",
                            resp.status_code,
                            delay_ms,
                        )
                        await asyncio.sleep(delay_ms / 1000)
                        continue
                    # Final attempt — raise
                    try:
                        body_text = resp.json().get("error", resp.text)
                    except Exception:
                        body_text = str(resp.status_code)
                    raise ApiError(
                        f"Server error on {path}: {body_text}",
                        resp.status_code,
                        endpoint=path,
                    )

                if resp.status_code >= 400:
                    # Non-retryable client errors
                    try:
                        body_text = resp.json().get("error", str(resp.status_code))
                    except Exception:
                        body_text = str(resp.status_code)
                    raise ApiError(
                        f"API error on {path}: {body_text}",
                        resp.status_code,
                        endpoint=path,
                    )

                return resp.json()

            except (PaymentError, RateLimitError, ApiError):
                raise

            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout) as exc:
                last_error = exc
                if attempt < max_attempts - 1 and self._retry:
                    delay_ms = self._get_retry_delay_ms(attempt)
                    logger.debug(
                        "Network error (%s), retrying in %dms",
                        type(exc).__name__,
                        delay_ms,
                    )
                    await asyncio.sleep(delay_ms / 1000)
                    continue
                raise NetworkError(
                    f"Network error on {path}: {exc}",
                    endpoint=path,
                    cause=exc,
                ) from exc

        # Should never reach here, but just in case
        raise NetworkError(
            f"All {max_attempts} attempts failed for {path}",
            endpoint=path,
            cause=last_error,
        )

    # ─── x402 helpers (same as sync — signing is CPU-bound) ───

    def _parse_payment_required(self, resp: httpx.Response) -> dict:
        """Parse x402 payment requirements from 402 response."""
        header = resp.headers.get("payment-required") or resp.headers.get(
            "PAYMENT-REQUIRED"
        )
        if header:
            return json.loads(base64.b64decode(header))

        body = resp.json()
        if "x402Version" in body:
            return body

        raise ValueError("Cannot parse x402 payment requirements from 402 response")

    def _create_payment_payload(self, payment_required: dict) -> dict:
        """Create EIP-3009 TransferWithAuthorization payload."""
        if "accepts" in payment_required:
            req = payment_required["accepts"][0]
        else:
            req = payment_required

        now = int(time.time())
        nonce = os.urandom(32)

        authorization = {
            "from": self._account.address,
            "to": req["payTo"],
            "value": str(req.get("maxAmountRequired", req.get("amount", "0"))),
            "validAfter": str(now - 600),
            "validBefore": str(now + req.get("maxTimeoutSeconds", 300)),
            "nonce": "0x" + nonce.hex(),
        }

        extra = req.get("extra", {})
        token_name = extra.get("name", "USD Coin")
        token_version = extra.get("version", "2")
        chain_id = int(req["network"].split(":")[1])
        token_address = req["asset"]

        domain = {
            "name": token_name,
            "version": token_version,
            "chainId": chain_id,
            "verifyingContract": token_address,
        }

        types = {
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        }

        message = {
            "from": authorization["from"],
            "to": authorization["to"],
            "value": int(authorization["value"]),
            "validAfter": int(authorization["validAfter"]),
            "validBefore": int(authorization["validBefore"]),
            "nonce": bytes.fromhex(authorization["nonce"][2:]),
        }

        signed = self._account.sign_typed_data(
            domain_data=domain,
            message_types=types,
            message_data=message,
        )

        return {
            "x402Version": 2,
            "payload": {
                "authorization": authorization,
                "signature": signed.signature.hex()
                if isinstance(signed.signature, bytes)
                else str(signed.signature),
            },
        }
