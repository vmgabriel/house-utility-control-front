# Project Context (Current State)

## Active Focus
- Current Module: `purchases` (Shopping Lists & Wishlist BFF integration complete)
- Current Phase: Production Readiness (Phase 3: Gunicorn, JSON Logging, Rate Limiting, CI/CD) — **implemented**, pending developer review. No commits made.

## Recent Architectural Decisions
- **Modular Monolith structure**: `src/{budget,identity,profile,tasks,purchases,rentals}/` with `src/shared/` kernel. Each context owns its 4 layers; no cross-context imports (enforced by `tests/integration/test_context_boundaries.py` via AST parsing).
- **Single composition root**: `src/interfaces/web/app.py` is the only module that knows both Flask and `httpx`. All repositories and use cases are wired there and attached as `app.*` attributes.
- **Auto-refresh JWT proxy**: Protected repositories are wrapped in `AutoRefreshingRepository` (in `src/shared/http/`). A 401 triggers one refresh + one retry; rotated tokens are staged on `g.refreshed_tokens` and written in `after_request`.
- **CSRF via HMAC**: Stateless tokens (salt + timestamp + HMAC-SHA256) validated in constant time. All state-changing routes are POST-only. Token injected via `@app.context_processor`; cookie set in `after_request`.
- **Nextcloud uploads via reverse proxy**: Browser PUTs to same-origin `/nextcloud-dav/rentals/*`; Caddy injects `Authorization: Basic` from env. Local-dev fallback (`NEXTCLOUD_UPLOAD_VIA_BFF=true`) routes through Flask but is forbidden in production.
- **Kanban with SortableJS**: HTML5 DnD rejected (no touch support). SortableJS (~20kb CDN) + Alpine `x-init` handles drag between columns. Optimistic UI with `location.reload()` revert on failure.
- **Structured JSON logging**: Hand-rolled `JsonFormatter` in `src/shared/logging.py` (stdlib-only, no `python-json-logger` dependency). Emits `timestamp`, `level`, `logger`, `message`, `request_id`. Format env-driven (`LOG_FORMAT=json`).
- **`RequestIdMiddleware`**: Outermost WSGI middleware; mints or propagates `X-Request-ID`, binds to `ContextVar` so every log record carries it, echoes on response.
- **E2E hermeticity**: `tests/e2e/drf_stub.py` runs an in-thread fake DRF server on ephemeral port. `page.route()` deliberately NOT used (browser never calls DRF directly — Flask does via `httpx`). Stub asserts on actual `Authorization` header and payload.
- **CI (`.github/workflows/ci.yml`)**: Gates every PR/push to `main` with three jobs: `lint`, `test` (unit+integration), `e2e` (Playwright headless).

## Phase 3 Deliverables (implemented)
- Gunicorn replacing Flask dev server (`4 workers × 2 threads`, `gthread` worker class).
- JSON logging wired into `create_app()` with request-id correlation (`src/shared/logging.py`).
- Rate limiting on auth endpoints (`/auth/login`, `/users/register`) via Flask-Limiter (`src/shared/rate_limit.py`); disabled in tests.
- `docker-compose.prod.yml` with `restart: unless-stopped`, no direct port exposure (external proxy handles TLS), required `FLASK_SECRET_KEY`, and a `/healthz` healthcheck.
- CI pipeline (`.github/workflows/ci.yml`) running image build, lint, full test suite, and E2E on every push/PR.
- Non-root container user (`appuser`, uid 10001).

## Operational Notes
- `FLASK_DEBUG=true` relaxes two things: JWT cookies drop `Secure` flag (no HTTPS on localhost), and Flask reloader activates. Both MUST be off in production.
- Playwright's sync API holds the event loop for the whole session, breaking `pytest-asyncio`'s `auto` mode. `make test-all` forks two processes on purpose; `tests/e2e/conftest.py` calls `pytest.exit` if combined.
- `datamodel-code-generator` was removed (Checkpoint 8): OpenAPI spec marked several fields required that the live API returns as null, causing 500s. Hand-written Pydantic schemas are the source of truth.
- `Money` (non-negative) vs `SignedMoney` (allows negative) split: `Money` for transaction amounts, `SignedMoney` exclusively for `net_balance`.

## Module Map (High Level)
- `budget`: Transactions, dashboard summaries, `Money`/`SignedMoney` VOs.
- `identity`: Registration, login, staff admin (ban with reason), user listing.
- `profile`: User preferences (timezone, language, currency, date_format).
- `tasks`: Goals, daily plans, Kanban board, local LLM (Ollama) async evaluation.
- `purchases`: Shopping lists (soft-delete, restore), wishlist (one-per-user), tags.
- `rentals`: Properties, documents, utility readings, payments, Nextcloud uploads.
- `shared`: `http/` (DRFAPIClient, AutoRefreshingRepository), `auth/` (JWT, CSRF), `domain/` (base exceptions, Clock port).

## Key Constraints for Current Task
- Reference `.skills/ddd-rules.md` for entity/value object patterns and Modular Monolith boundaries.
- Reference `.skills/testing-rules.md` for test structure and async isolation.
- Reference `.skills/bff-rules.md` for CSRF, cookies, Alpine, and template rendering rules.
- DO NOT repeat the full `AGENTS.md`. Assume rules are known.
