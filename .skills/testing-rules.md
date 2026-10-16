# Testing Rules

## Suite Separation
- **Unit** (`tests/unit/`): Pure Python. Domain invariants + use-case orchestration with `unittest.mock.AsyncMock`. No framework imports.
- **Integration** (`tests/integration/`): Real WSGI stack + `respx` to mock DRF HTTP. Covers auth, CSRF, method strictness, security headers, outage path, port conformance.
- **E2E** (`tests/e2e/`): Playwright drives real Chromium against real Flask server + real in-thread `DRFStubServer`. **Do NOT use `page.route()`** — browser never calls DRF directly.

## Async Isolation (CRITICAL)
- Playwright's sync API holds the event loop for the whole session, breaking `pytest-asyncio`'s `auto` mode.
- `make test-all` forks two processes: `make test` then `make test-e2e`.
- `tests/e2e/conftest.py` calls `pytest.exit()` with an actionable message if both suites are collected together.

## DRF Stub (`tests/e2e/drf_stub.py`)
- Runs on ephemeral port; `DRF_API_BASE_URL` pointed at it via `monkeypatch`.
- Stub asserts on actual `Authorization` header and JSON payload — exercises 401 → refresh → retry flow for real.
- Mint real UUIDs for task/plan ids (domain `TaskId`/`DailyPlanId` reject non-UUIDs).
- Add new endpoints to `PUBLIC_PATHS` if unauthenticated; otherwise enforce bearer auth in stub.

## respx Patterns
- Use `@respx.mock` decorator or `respx.mock` context.
- Match exact URL + method: `respx.post("http://localhost:8000/api/v1/...").mock(return_value=httpx.Response(200, json={...}))`.
- Assert on `route.calls[0].request.headers["Authorization"]` for bearer-token tests.

## Test Naming
- `test_<unit>_<scenario>_<expected>` (e.g., `test_money_rejects_negative_amount`).
- Integration: `test_<repository>_<method>_<scenario>`.
- E2E: `test_<page>_<action>_<outcome>`.
