# clawfetch

Python SDK for [ClawFetch](https://api.clawfetch.ai) — Web Intelligence API for AI Agents.

Pay-per-request via [x402](https://x402.org) (gasless USDC on Base). No API keys, no subscriptions.

## Install

```bash
pip install clawfetch
```

## Quick Start

### Sync

```python
from clawfetch import ClawFetch

with ClawFetch(private_key="0x...") as cf:
    # Fetch any URL as clean markdown ($0.001)
    page = cf.fetch("https://example.com")

    # Extract structured data ($0.003)
    btc = cf.extract("https://coingecko.com/en/coins/bitcoin")
    print(btc["data"])

    # Multi-source research ($0.01)
    report = cf.research("latest AI agent frameworks")
```

### Async

```python
import asyncio
from clawfetch import AsyncClawFetch

async def main():
    async with AsyncClawFetch(private_key="0x...") as cf:
        page = await cf.fetch("https://example.com")
        btc = await cf.extract("https://coingecko.com/en/coins/bitcoin")
        report = await cf.research("latest AI agent frameworks")

asyncio.run(main())
```

Both clients have identical APIs — all 7 endpoints, full x402 payment flow, retry with exponential backoff, and typed error hierarchy.

## API

| Method | Price | Description |
|--------|-------|-------------|
| `fetch(url)` | $0.001 | URL → clean markdown |
| `render(url)` | $0.002 | JS-rendered page → markdown |
| `extract(url)` | $0.003 | Structured data from 17+ sites |
| `research(topic)` | $0.010 | Multi-source topic research |
| `domains_check(domains)` | $0.002 | Domain availability check |
| `domains_suggest(query)` | $0.002 | Domain name suggestions |
| `parse(source)` | $0.002 | Office document (docx/pptx/xlsx/pdf/…) → markdown |
| `extractors()` | $0.001 | List available extractors |
| `health()` | Free | API status check |

## Error Handling

```python
from clawfetch import (
    ClawFetch,
    ClawFetchError,    # Base class
    PaymentError,      # 402 — insufficient USDC
    NetworkError,      # Connection/timeout failures
    RateLimitError,    # 429 — includes retry_after_ms
    ApiError,          # Other 4xx/5xx errors
)
```

## Configuration

```python
from clawfetch import ClawFetch, RetryOptions

cf = ClawFetch(
    private_key="0x...",
    base_url="https://api.clawfetch.ai",  # default
    timeout=30.0,                          # seconds
    retry=RetryOptions(
        max_retries=3,
        initial_delay_ms=500,
        max_delay_ms=10000,
        backoff_multiplier=2.0,
    ),
    debug=False,
)
```

## Requirements

- Python 3.9+
- A wallet with USDC on Base (even $1 gives you 1,000+ requests)

## License

MIT

---

# Original README below

Python SDK for [ClawFetch](https://api.clawfetch.ai) — Web Intelligence API for AI Agents.

Pay-per-request via [x402](https://x402.org) (gasless USDC on Base). No API keys, no subscriptions.

## Install

```bash
pip install clawfetch
```

## Quick Start

```python
from clawfetch import ClawFetch

cf = ClawFetch(private_key="0x...")

# Fetch any URL as clean markdown ($0.001)
page = cf.fetch("https://example.com")

# Extract structured data ($0.003)
btc = cf.extract("https://coingecko.com/en/coins/bitcoin")
print(btc["data"])  # {'name': 'Bitcoin', 'price': 98432.12, ...}

# JS-rendered pages ($0.002)
rendered = cf.render("https://app.uniswap.org")

# Multi-source research ($0.01)
report = cf.research("latest AI agent frameworks")

# Domain availability ($0.002)
domains = cf.domains_check(["coolstartup.com", "coolstartup.ai"])

# List extractors ($0.001)
extractors = cf.extractors()
```

## Configuration

```python
from clawfetch import ClawFetch, RetryOptions

cf = ClawFetch(
    private_key="0x...",
    base_url="https://api.clawfetch.ai",  # default
    timeout=30.0,                           # request timeout in seconds
    retry=RetryOptions(                     # or False to disable retries
        max_retries=3,
        initial_delay_ms=500,
        max_delay_ms=10_000,
        backoff_multiplier=2.0,
    ),
    debug=False,                            # enable debug logging
)
```

## Error Handling

All errors extend `ClawFetchError` for easy catching:

```python
from clawfetch import ClawFetch, ClawFetchError, PaymentError, NetworkError, RateLimitError, ApiError

cf = ClawFetch(private_key="0x...")

try:
    result = cf.fetch("https://example.com")
except PaymentError as e:
    print(f"Payment failed ({e.status_code}): {e}")
except RateLimitError as e:
    print(f"Rate limited, retry after {e.retry_after_ms}ms")
except NetworkError as e:
    print(f"Network error: {e}")
except ApiError as e:
    print(f"API error {e.status_code}: {e}")
except ClawFetchError as e:
    print(f"ClawFetch error: {e}")
```

| Error | Status | Retried |
|-------|--------|---------|
| `PaymentError` | 402 | No |
| `RateLimitError` | 429 | Yes (with Retry-After) |
| `ApiError` | 400, 401, 404 | No |
| `ApiError` | 500, 502, 503, 504 | Yes |
| `NetworkError` | Connection/timeout | Yes |

## Retry Behavior

By default, retries are enabled with exponential backoff + jitter:

| Attempt | Delay |
|---------|-------|
| 1st retry | ~500ms |
| 2nd retry | ~1,000ms |
| 3rd retry | ~2,000ms |

Respects `Retry-After` headers on 429 responses. Non-retryable errors (400, 401, 402, 404) fail immediately.

Disable retries:

```python
cf = ClawFetch(private_key="0x...", retry=False)
```

## How It Works

1. SDK makes a request to ClawFetch
2. Server returns `402 Payment Required` with USDC amount
3. SDK auto-signs an EIP-3009 gasless USDC transfer on Base
4. Request is retried with payment header
5. You get structured data back

No gas fees. No API keys. Just USDC on Base.

## Context Manager

```python
with ClawFetch(private_key="0x...") as cf:
    data = cf.extract("https://coingecko.com/en/coins/bitcoin")
# Client is automatically closed
```

## Requirements

- Python 3.9+
- A wallet with USDC on Base (even $1 gives you 1,000+ requests)

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
