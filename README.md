# clawfetch

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

## How It Works

1. SDK makes a request to ClawFetch
2. Server returns `402 Payment Required` with USDC amount
3. SDK auto-signs an EIP-3009 gasless USDC transfer on Base
4. Request is retried with payment header
5. You get structured data back

No gas fees. No API keys. Just USDC on Base.

## Requirements

- Python 3.9+
- A wallet with USDC on Base (even $1 gives you 1,000+ requests)

## License

MIT
