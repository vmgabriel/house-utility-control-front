# Budget Tracker Frontend (BFF)

A secure, Server-Side Rendered (SSR) Backend-for-Frontend (BFF) for the Budget Tracker application. Built with Python, Flask, Jinja2, Tailwind CSS, and Alpine.js, following Domain-Driven Design (DDD) and Clean Architecture principles.

## 🏗 Architecture

The project follows a strict Hexagonal/Clean Architecture:

- **Domain**: Pure Python business rules, entities, and value objects. Zero framework dependencies.
- **Application**: Use cases orchestrating domain logic. Depends only on domain and ports.
- **Infrastructure**: Adapters for external services (DRF API client via `httpx`, secure JWT cookie management, CSRF protection).
- **Interfaces**: Flask web layer, Jinja2 templates, and ViewModels mapping domain to UI.

Dependencies point inward — `interfaces → application → domain` — and `domain` imports no framework at all. The only module that knows both Flask and `httpx` is `interfaces/web/app.py`, the composition root.

Two adapter details worth knowing before reading the code:

- `infrastructure/api/drf_client.py` is the outer boundary, so it is the *only* place `httpx` exceptions are caught. Nothing above it ever sees a socket error.
- `AutoRefreshingRepository` decorates the protected repositories. A `401` from DRF triggers exactly one refresh and one retry, so an expired access token is invisible to the user.

## 🚀 Tech Stack

- **Core**: Python 3.11+, Flask, Jinja2
- **Frontend**: Tailwind CSS (CDN), Alpine.js (lightweight reactivity)
- **HTTP**: `httpx` (async), `pydantic` (data validation)
- **Tooling**: Hatch (project management), Makefile, Ruff, Black, Pytest, Playwright

## 🛠 Setup & Development

1. **Prerequisites**: Python 3.11+, Hatch
2. **Install dependencies**:
   ```bash
   make setup
   ```
   *(Note: If `playwright install-deps` fails due to sudo restrictions, ensure Chromium is already installed on your system).*
3. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your DRF_API_BASE_URL and FLASK_SECRET_KEY
   ```
4. **Run the development server**:
   ```bash
   make run
   ```
   The app will be available at `http://localhost:5000`.

`FLASK_DEBUG=true` relaxes two things for local work: the JWT cookies drop `Secure` (there is no HTTPS on `localhost`), and Flask's debug reloader comes up. Both must be off in production — a `Secure` flag ignored over plain HTTP is the difference between a protected token and a leaked one.

## 🧪 Testing

The project includes comprehensive testing at all levels:

```bash
make lint       # Run Ruff and Black
make test       # Run unit and integration tests
make test-e2e   # Run Playwright E2E tests (headless)
make test-all   # Run all test suites
```

Current state: **24 unit**, **186 integration**, **88 end-to-end**.

| Suite | What it proves |
| --- | --- |
| `tests/unit` | Domain invariants and use-case orchestration, with no framework in the loop. |
| `tests/integration` | The real WSGI stack (including `async def` views) plus `respx` for DRF, covering auth flows, CSRF, method strictness, security headers, and the outage path. |
| `tests/e2e` | A real browser against a real Flask server on a real socket, with a fake DRF backend. |

Two things to know before adding tests:

- **The E2E and async suites cannot share a `pytest` process.** Playwright's sync API holds the event loop for the whole session, which breaks `pytest-asyncio`'s auto mode. `make test-all` runs them as two processes on purpose; `tests/e2e/conftest.py` explains why and fails loudly if you try to collect both.
- **DRF is faked over HTTP, not intercepted in the browser.** The BFF is server-side rendered, so `page.route()` would mock nothing while the suite appeared to pass. The only honest seam is the network boundary, so `tests/e2e/drf_stub.py` runs a real server on a real port.

## 🔒 Security Features

- **JWT Tokens**: Stored exclusively in `HttpOnly`, `Secure`, `SameSite=Lax` cookies. Never exposed to JavaScript. The E2E suite asserts this by asking the page what it can see through `document.cookie`.
- **CSRF Protection**: Custom HMAC-based CSRF tokens required for all state-changing POST requests. The token is stateless — a random salt, an issue timestamp, and an HMAC-SHA256 signature over the two, verified in constant time with a 7-day expiry.
- **Method Strictness**: `/auth/logout`, `/transactions/create`, and `/transactions/<id>/delete` are POST-only and answer `405` to anything else. `/auth/login` serves `GET` to deliver the form, but only its `POST` mutates state.
- **Auto-Refresh**: Seamless JWT token rotation on 401 responses without user interruption.
- **Security Headers**: `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, and `Referrer-Policy` enforced on **every** response — HTML, JSON, redirects, and error pages alike. See `src/interfaces/web/security.py`.
- **No `Content-Security-Policy`**: A known gap, not an oversight. Tailwind and Alpine load from CDNs and the templates use Alpine's inline directives, so a real CSP needs `'unsafe-inline'` for scripts. It is documented in the module and asserted absent so nobody adds one silently.

## 💥 Handling Backend Outages

The BFF draws one line between two failures that look similar but deserve opposite treatment:

| What happened | What the user sees |
| --- | --- |
| DRF **answered** with an error (`4xx`/`5xx`) | Inline flash, or an empty dashboard. A reachable backend that is unhappy is something the user can act on, and the local state is still meaningful. |
| DRF **never answered** (refused, timed out, dropped) | A branded `503` page. There is no response to act on, so rendering an empty dashboard would be a lie — `$0.00` and "No transactions yet" are statements about the user's finances, and a failed socket is not evidence for either. |

`httpx.TransportError` is translated into `ServiceUnavailableError` at the client boundary, and the web layer maps that to `errors/503.html`. Views re-raise it rather than swallowing it, which is why each one has an explicit `except ServiceUnavailableError: raise` above its broad handler.

Two deliberate exceptions:

- **Logout always succeeds locally.** Refusing to clear the session because the backend is unreachable would leave the user authenticated against a service that cannot validate anything. The cookies are dropped either way.
- **There is no generic `500` handler.** A `500` means a bug in *this* app, and a branded page would hide the traceback that is the only useful output of one. Only external failures get dressed up.

## 🧩 Frontend Rules

Two rules govern every Jinja2 template and file-upload feature.

### Alpine.js Scope Strictness

All interactive elements — buttons, modals, forms with `x-model`, `@click`, `$dispatch` — must live inside a single, top-level `x-data="..."` scope. A button that opens a modal and that modal must share the same `x-data` parent; action buttons are never placed outside the subtree. Pages that need several independent interactive zones declare separate `x-data` blocks and keep their state apart.

Alpine.js silently ignores directives outside an `x-data` scope, so a misplaced button renders fine and does nothing. No build step, no lint rule, and no unit test catches that — the E2E suite does, by clicking it.

### Direct Uploads (Nextcloud)

Large files (documents, contracts) never travel through the Flask BFF. Use the Nextcloud Direct Upload API flow instead:

1. BFF fetches the upload token.
2. Frontend uploads the file directly to Nextcloud.
3. Frontend sends the resulting file path to the BFF for DB registration.

Proxying file bytes through the BFF would tie upload capacity and timeouts to the same process that renders every page.

## 📁 Project Structure

```text
src/
├── domain/                 # Pure business logic: entities, value objects, ports
├── application/            # Use cases and ports
├── infrastructure/         # DRF API client, mappers, cookie/CSRF managers
└── interfaces/             # Flask app, blueprints, Jinja2 templates, ViewModels
tests/
├── unit/                   # Domain and application tests
├── integration/            # Infrastructure and web component tests
└── e2e/                    # Playwright end-to-end tests (with DRF stub)
```

Inside `interfaces/web/`, `app.py` is the composition root, `security.py` holds the response-header policy, and `templates/errors/503.html` is the outage page. It deliberately does not extend `base.html`: the error page must render with nothing but the request context available, so it cannot depend on the session, the flash queue, or the CSRF context processor — any of which might be what broke.
