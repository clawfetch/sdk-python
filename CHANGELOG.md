# Changelog

All notable changes to the `clawfetch` Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-10-18

### Fixed
- `research()` now sends `query` on the wire (was `topic`, which the server rejects with HTTP 400 after payment). The result count is sent as `maxResults`; the new `max_results=` keyword is preferred and `sources=` is kept as a deprecated alias.
- `domains_suggest()` now sends `keywords` as an array (was `query`, rejected with HTTP 400 after payment). It accepts a list or a string (split on whitespace/commas, so `domains_suggest("agent")` still works), strips leading dots from `tlds`, and adds `max_check=` (`maxCheck`).
- Docstrings now describe the real response shapes: `fetch()` returns `text` (not `content`), `render()` returns `title`/`description`/`text`/`url`, `research()` returns `query`/`sources`/`results`, `domains_check()` returns `checked`/`available`/`taken`/`results` (where `available` may be `None` with an `error`), `domains_suggest()` returns `keywords`/`generated`/`checked`/`available`/`allResults`. Responses are returned as-is from the server.
- Domain method docstrings and README now list the live $0.003 price (were $0.008).

### Added
- `render(..., wait_for=)` → `waitFor` (CSS selector to wait for).
- `extract(..., type=)` → `type` (explicit extractor name; auto-detected when omitted).
- Tests asserting the exact JSON request body for every POST endpoint, sync and async.

## [0.2.1] - 2026-08-06

### Changed
- Documented prices updated to match the live API: `render()` $0.005, `extract()` $0.008, `parse()` $0.005, `research()` $0.02, `domains_check()`/`domains_suggest()` $0.003. `fetch()` and `extractors()` unchanged at $0.001.

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
