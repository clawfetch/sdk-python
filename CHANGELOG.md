# Changelog

All notable changes to the `clawfetch` Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-05

### Added
- `parse(source, *, filename=None, format=None)` on both `ClawFetch` and `AsyncClawFetch` — parse office documents (docx, pptx, xlsx, pdf, odt, ods, odp, rtf, epub, csv, doc, ppt) into GitHub-Flavored Markdown via the new `POST /parse` endpoint ($0.002). Accepts a document URL or raw `bytes`. No OCR — scanned/image-only PDFs return HTTP 422.

## [0.1.0] - 2026-03-29

### Added
- Initial release of the ClawFetch Python SDK
- **Sync client** (`ClawFetch`): 7 API methods — `fetch`, `render`, `extract`, `research`, `check_domains`, `suggest_domains`, `extractors`
- **Async client** (`AsyncClawFetch`): full async/await support via `httpx.AsyncClient` with `async with` context manager
- Automatic x402 micropayments via USDC on Base — no API keys needed
- EIP-3009 signature generation for x402 payment headers using `eth-account`
- Typed error hierarchy: `ClawFetchError` → `PaymentError`, `NetworkError`, `RateLimitError`, `ApiError`
- Configurable exponential backoff retry with jitter (429, 5xx, network errors)
- `RetryOptions` dataclass for fine-grained retry configuration
- `Retry-After` header support for rate-limited responses
- Debug logging via Python `logging` module
- Configurable request timeout
- OpenAPI 3.1 specification (YAML + JSON)
- 128 pytest unit tests covering sync client, async client, x402 payment flow, error handling, and retry behavior
- Python 3.9+ support
