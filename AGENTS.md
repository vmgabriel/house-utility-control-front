# AGENTS.md - Budget Tracker Frontend (SSR BFF)

This is a **Python Backend-for-Frontend (BFF)** built with Flask, Jinja2, Tailwind CSS, and Alpine.js. It talks to a decoupled Django REST Framework (DRF) backend via `httpx`.

**Core Philosophy:** There is no Node.js, no build step, and no bundler. This is a **Modular Monolith** following strict Domain-Driven Design (DDD) and Clean Architecture principles. The architecture must "scream" business capabilities, not framework details.

**Read context by purpose:**
- `CONTEXT.md` — current state, active focus, recent architectural decisions, module map.
- `.skills/ddd-rules.md` — layers, value objects, ports, context boundaries.
- `.skills/testing-rules.md` — unit/integration/E2E split, async isolation, respx, DRF stub.
- `.skills/bff-rules.md` — CSRF, JWT cookies, Alpine.js, templates, security headers.
- `.skills/git-rules.md` — commit policy and working-tree hygiene.

---

## 🛠 Tech Stack

- **Runtime:** Python 3.11+, Flask, Jinja2, `httpx`, `asgiref` (async views).
- **Frontend:** Tailwind CSS + Alpine.js via CDN — no build step, no Node.js.
- **Validation:** Hand-written Pydantic schemas per bounded context (no codegen).
- **Tests:** pytest, pytest-asyncio, `respx`, Playwright.
- **Tooling:** Docker Compose + Make; Ruff + Black; Hatch inside the container.

---

## 🏗 Architecture & Directory Structure

Isolated **Bounded Contexts** organized around a **Shared Kernel**, with a single **Composition Root**.

```text
src/
├── shared/        # SHARED KERNEL: auth (JWT/CSRF), http (DRFAPIClient, AutoRefreshingRepository), domain, infrastructure, utils
├── budget/        # BOUNDED CONTEXT: Transactions & dashboard (Money / SignedMoney)
├── identity/      # BOUNDED CONTEXT: Authentication & session
├── profile/       # BOUNDED CONTEXT: User preferences
├── users/         # BOUNDED CONTEXT: Registration, staff admin
├── tasks/         # BOUNDED CONTEXT: Goals, daily plans, Kanban
├── purchases/     # BOUNDED CONTEXT: Shopping lists & wishlist
├── rentals/       # BOUNDED CONTEXT: Properties, documents, payments
└── interfaces/    # COMPOSITION ROOT: no business logic lives here
    └── web/
        ├── app.py          # THE ONLY PLACE that knows Flask, httpx, and all contexts
        ├── security.py     # Security headers middleware
        └── templates/      # base.html + errors/, plus not-yet-migrated profile/ + users/
```

Each bounded context owns four layers (`domain/`, `application/`, `infrastructure/`, `interfaces/`). See `.skills/ddd-rules.md` for the layer contract.

---

## 🚫 Critical Constraints (Do Not Violate)

Non-negotiable. Statements are summarized here; full detail lives in the referenced skill files.

1. **NO cross-context imports.** Contexts communicate only via `src/shared` or the external DRF API, enforced by `tests/integration/test_context_boundaries.py`. See `.skills/ddd-rules.md`.
2. **Framework-free core.** `domain/` and `application/` must not import Flask, `httpx`, `pydantic`, `dotenv`, or infrastructure code. See `.skills/ddd-rules.md`.
3. **Single composition root.** `src/interfaces/web/app.py` is the only module that wires dependencies and knows both Flask and `httpx`. See `.skills/ddd-rules.md`.
4. **No generic 500 handlers.** A 500 is a bug — let it crash and show the traceback. Only catch specific failures (e.g. `httpx.TransportError`) to render the branded `503`. See `.skills/bff-rules.md`.
5. **Pytest async isolation.** Never collect `tests/e2e` with unit/integration in the same process; use `make test-all`. See `.skills/testing-rules.md`.
6. **No file uploads through the BFF in production.** Document bytes never travel through Flask; the browser `PUT`s to a same-origin path the reverse proxy forwards to Nextcloud. See `CONTEXT.md`.
7. **Hand-written schemas are authoritative.** Mappers read `src/<context>/infrastructure/schemas.py`, never the DRF OpenAPI schema (see the divergence table below).
8. **Scope page assertions to `<main>`.** Tests counting page-level markers must slice to `<main>` via `tests/integration/rentals/page_html.py::main_content`, because `base.html` owns interactive zones of its own.

---

## 🛠 Common Commands

All tooling runs inside the `bff` Docker container. Use `make`; do not invoke `hatch` on the host — it is a different environment from the one CI and the image use.

```bash
make setup      # Build the image (installs deps + Playwright's Chromium)
make run        # Start the BFF (http://localhost:5001)
make stop       # Stop, keeping volumes and the built image
make logs       # Follow logs
make lint       # ruff check + black --check
make format     # black + ruff --fix
make test       # Unit + Integration tests (fast, respx mocked)
make test-e2e   # Playwright E2E tests (headless, real HTTP stub)
make test-all   # Runs unit/integration AND e2e in separate processes
make shell      # Shell inside the running BFF
```

`src/` and `tests/` are bind-mounted read-only, so source edits need no rebuild. `make setup` is only required after changing `Dockerfile` or `pyproject.toml`.

**Focused testing:**
```bash
docker compose run --rm bff hatch run pytest tests/integration/profile/test_repository.py -v
```

---

## 🔌 Working with the DRF API

There is no generated contract and no schema codegen step. The DRF OpenAPI schema is a reference, not a source of truth, so every mapper reads a hand-written Pydantic schema in its own context (`src/<context>/infrastructure/schemas.py`).

drf-spectacular declares these fields required and non-nullable, but the live API returns `null`:

| Field | Declared | Actual API behaviour |
| --- | --- | --- |
| `DashboardOverview.today` / `this_week` / `this_month` | `DashboardSummary` | `null` when the user has no transactions in the window |
| `Profile.avatar_url` / `Profile.bio` | `str` | `null` until the user sets them |

Following the schema spec here would raise on a freshly registered user — an `AttributeError` inside the dashboard mapper, surfacing as a 500. To add a field the backend already returns: extend the hand-written schema and the mapper, cover it in an integration test (`respx`), and mark it `str | None` if it can come back `null`.

---

## 🚀 Adding a New Bounded Context

1. **Scaffold:** `mkdir -p src/<context>/{domain,application,infrastructure,interfaces}`, each with an `__init__.py`. `test_context_boundaries.py` only sees contexts that have one.
2. **Domain:** entities, value objects, and `typing.Protocol` ports (pure Python).
3. **Application:** use cases depending only on the ports.
4. **Infrastructure:** implement the repository with `src.shared.http.DRFAPIClient` and Pydantic schemas.
5. **Interfaces:** a Flask blueprint, ViewModels, and Jinja2 templates under `src/<context>/interfaces/templates/`.
6. **Wire:** register `("<context>", "templates")` in the `context_templates` list in `src/interfaces/web/app.py` (nothing fails at import if this is missed — it has no test coverage), import and attach the repository/use cases, and register the blueprint.
7. **Test:** add unit, integration, and E2E tests, then run `make test-all`. The boundary check discovers the new context on its own.
