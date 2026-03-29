"""ClawFetch Python SDK — handles x402 payment flow automatically."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data

BASE_URL = "https://api.clawfetch.ai"


class ClawFetch:
    """Client for the ClawFetch Web Intelligence API.

    Handles the full x402 payment flow:
    1. Make request → get 402 with payment requirements
    2. Sign EIP-3009 gasless USDC transfer on Base
    3. Retry with PAYMENT-SIGNATURE header → get data

    Usage::

        from clawfetch import ClawFetch

        cf = ClawFetch(private_key="0x...")
        btc = cf.extract("https://coingecko.com/en/coins/bitcoin")
        print(btc["data"])
    """

    def __init__(
        self,
        private_key: str,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ):
        self._account = Account.from_key(private_key)
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    @property
    def address(self) -> str:
        return self._account.address

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ─── Public endpoints ──────────────────────────────────────

    def fetch(self, url: str, *, max_chars: int | None = None) -> dict:
        """Fetch a URL as clean markdown ($0.001)."""
        body: dict = {"url": url}
        if max_chars:
            body["maxChars"] = max_chars
        return self._paid_post("/fetch", body)

    def render(self, url: str, *, max_chars: int | None = None) -> dict:
        """Render JS-heavy page with stealth browser ($0.002)."""
        body: dict = {"url": url}
        if max_chars:
            body["maxChars"] = max_chars
        return self._paid_post("/render", body)

    def extract(self, url: str) -> dict:
        """Extract structured data from supported URL ($0.003)."""
        return self._paid_post("/extract", {"url": url})

    def research(self, topic: str, *, sources: int | None = None) -> dict:
        """Multi-source research on a topic ($0.01)."""
        body: dict = {"topic": topic}
        if sources:
            body["sources"] = sources
        return self._paid_post("/research", body)

    def domains_check(self, domains: list[str]) -> dict:
        """Check domain availability ($0.002)."""
        return self._paid_post("/domains/check", {"domains": domains})

    def domains_suggest(self, query: str, *, tlds: list[str] | None = None) -> dict:
        """Generate domain suggestions ($0.002)."""
        body: dict = {"query": query}
        if tlds:
            body["tlds"] = tlds
        return self._paid_post("/domains/suggest", body)

    def extractors(self) -> list[dict]:
        """List available extractors ($0.001)."""
        resp = self._paid_request("GET", "/extractors")
        return resp.get("extractors", [])

    def health(self) -> dict:
        """Check service health (free)."""
        r = self._client.get(f"{self._base_url}/health")
        r.raise_for_status()
        return r.json()

    # ─── x402 payment flow ─────────────────────────────────────

    def _paid_post(self, path: str, body: dict) -> dict:
        return self._paid_request("POST", path, json_body=body)

    def _paid_request(
        self, method: str, path: str, json_body: dict | None = None
    ) -> dict:
        url = f"{self._base_url}{path}"
        kwargs: dict = {}
        if json_body is not None:
            kwargs["json"] = json_body

        # First request — expect 402
        resp = self._client.request(method, url, **kwargs)
        if resp.status_code != 402:
            resp.raise_for_status()
            return resp.json()

        # Parse payment requirements from header or body
        payment_required = self._parse_payment_required(resp)

        # Sign EIP-3009 authorization
        payment_payload = self._create_payment_payload(payment_required)

        # Encode as base64 header
        encoded = base64.b64encode(
            json.dumps(payment_payload).encode()
        ).decode()

        # Retry with payment header
        headers = {"PAYMENT-SIGNATURE": encoded}
        resp2 = self._client.request(method, url, headers=headers, **kwargs)
        resp2.raise_for_status()
        return resp2.json()

    def _parse_payment_required(self, resp: httpx.Response) -> dict:
        """Parse x402 payment requirements from 402 response."""
        # v2: base64-encoded JSON in PAYMENT-REQUIRED header
        header = resp.headers.get("payment-required") or resp.headers.get(
            "PAYMENT-REQUIRED"
        )
        if header:
            return json.loads(base64.b64decode(header))

        # v1: JSON body
        body = resp.json()
        if "x402Version" in body:
            return body

        raise ValueError("Cannot parse x402 payment requirements from 402 response")

    def _create_payment_payload(self, payment_required: dict) -> dict:
        """Create EIP-3009 TransferWithAuthorization payload."""
        # v2 format: accepts is a list of payment options
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

        # Get token metadata for EIP-712 domain
        extra = req.get("extra", {})
        token_name = extra.get("name", "USD Coin")
        token_version = extra.get("version", "2")
        chain_id = int(req["network"].split(":")[1])
        token_address = req["asset"]

        # Sign EIP-712 typed data
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
