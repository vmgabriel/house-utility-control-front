# AGENTS.md - Budget Tracker Frontend (SSR BFF)

This is a **Python Backend-for-Frontend (BFF)** built with Flask, Jinja2, Tailwind CSS, and Alpine.js. It communicates with a decoupled Django REST Framework (DRF) backend via `httpx`. 

**Core Philosophy:** There is no Node.js, no build step, and no bundler. This is a **Modular Monolith** following strict Domain-Driven Design (DDD) and Clean Architecture principles. The architecture must "scream" business capabilities, not framework details.

---

## 🏗 Architecture & Directory Structure

The project is organized into isolated **Bounded Contexts** and a **Shared Kernel**. 

```text
src/
├── shared/                 # SHARED KERNEL: Stable, generic, framework-agnostic
│   ├── auth/               # JWTCookieManager, CSRFTokenManager
│   ├── http/               # Base DRFAPIClient, AutoRefreshingRepository proxy
│   └── ui/                 # Base Jinja2 templates, Tailwind/Alpine setup
│
├── contract/               # GENERATED view of the DRF API surface (drift detection)
│   ├── types.py            # GENERATED: do not edit, do not import at runtime
│   ├── HEADER.txt          # Banner prepended to types.py
│   └── overrides.py        # Fields where the OpenAPI schema and live API disagree
│
├── budget/                 # BOUNDED CONTEXT: Personal Finance
│   ├── domain/             # PURE PYTHON: Entities, Value Objects (Money, SignedMoney), Ports
│   ├── application/        # PURE PYTHON: Use Cases
│   ├── infrastructure/     # DRF mappers, schemas, repository implementations
│   └── interfaces/         # Flask blueprints, ViewModels, Jinja2 templates
│
├── profile/                # BOUNDED CONTEXT: User Preferences
│   └── (same 4-layer structure)
│
├── users/                  # BOUNDED CONTEXT: Identity, Registration, Staff Admin
│   └── (same 4-layer structure)
│
└── interfaces/             # COMPOSITION ROOT (Legacy flat structure being migrated)
    └── web/
        ├── app.py          # THE ONLY PLACE that knows about Flask, httpx, and all contexts
        ├── security.py     # Security headers middleware
        └── templates/      # Legacy templates (being moved to context-specific folders)
```

---

## 🚫 CRITICAL CONSTRAINTS (Do Not Violate)

1. **NO Cross-Context Imports:** `src.budget` MUST NOT import from `src.profile` or `src.users`, and vice versa. They only communicate via the shared kernel or the external DRF API.
2. **Framework-Free Core:** `domain/` and `application/` layers MUST NOT import Flask, `httpx`, `pydantic`, `dotenv`, or any infrastructure code. They are pure Python (`dataclasses`, `typing`, `enum`, `decimal`).
3. **Single Composition Root:** `src/interfaces/web/app.py` is the **only** module allowed to wire dependencies (e.g., injecting `DRFProfileRepository` into `GetProfileUseCase`).
4. **No Generic 500 Handlers:** A 500 error is a bug. Let it crash and show the traceback. Only catch specific exceptions (like `httpx.ConnectError`) to render the branded `503.html` page.
5. **Pytest Async Isolation:** NEVER run `tests/e2e` and `tests/unit`/`tests/integration` in the same pytest process. Playwright's sync API holds the event loop, breaking `pytest-asyncio`. Use `make test-all`, which forks two processes.
6. **Never Import the Generated Contract:** `src.contract.types` MUST NOT be imported by any runtime module. It mirrors the OpenAPI schema, which is wrong about nullability in several places (see [API Contract Management](#-api-contract-management)); the hand-written pydantic schemas are authoritative. `types.py` exists only so `make verify-api-contract` can diff it.

---

## 🛠 Development Workflow & Commands

All tooling runs inside the Hatch environment. Use `make`, or prefix with `hatch run`.

```bash
make setup      # Install deps + playwright browsers
make run        # Start dev server (http://localhost:5000)
make lint       # ruff check + black --check
make format     # black + ruff --fix
make test       # Unit + Integration tests (fast, respx mocked)
make test-e2e   # Playwright E2E tests (headless, real HTTP stub)
make test-e2e-headed # E2E with visible browser
make test-all   # Runs unit/integration AND e2e in separate processes

make sync-api-contract   # Regenerate src/contract/types.py from the DRF schema
make verify-api-contract # Fail if the backend's API has drifted from that file
```

**Focused Testing:**
```bash
hatch run pytest tests/integration/profile/test_repository.py -v
```

---

## 🔌 API Contract Management

`src/contract/` holds the machine-generated view of the DRF API surface.

| File | Ownership | Purpose |
| --- | --- | --- |
| `types.py` | **GENERATED** | Dataclasses derived from the DRF OpenAPI schema |
| `HEADER.txt` | manual | The banner prepended to `types.py`; edit this, not the output |
| `overrides.py` | manual | Fields where the schema and the live API disagree |

### `types.py` is a drift-detection artifact, NOT a runtime type source

**Do not import `src.contract.types` from application, domain, or infrastructure
code.** The mappers keep using the hand-written pydantic schemas
(`src/infrastructure/api/schemas.py` and the per-context equivalents), because
drf-spectacular declares several fields required and non-nullable that the live
API actually returns as `null`:

| Field | Generated type | Actual API behaviour |
| --- | --- | --- |
| `DashboardOverview.today` / `this_week` / `this_month` | `DashboardSummary` | `null` when the user has no transactions in the window |
| `Profile.avatar_url` / `Profile.bio` | `str` | `null` until the user sets them |

Consuming the generated types would raise on a freshly registered user — an
`AttributeError` inside the dashboard mapper, surfacing as a 500. Every such
divergence must be catalogued in `overrides.py`, and
`tests/integration/test_contract_overrides.py` fails if a runtime schema is
tightened to match the generated contract, or if the backend later fixes the
schema (in which case the override should be deleted, not left stale).

### Workflow

```bash
make sync-api-contract                 # regenerate + ruff + black, then commit
make verify-api-contract               # what CI runs; non-zero on drift
make verify-api-contract SCHEMA_URL=…  # against a non-local backend
```

`sync` and `verify` share `CODEGEN_FLAGS` in the `Makefile` on purpose. The
verify target regenerates into `build/types_check.py` using identical flags and
diffs it against the committed file, so changing the generator's options in one
target but not the other would otherwise report drift on every run.

The schema endpoint serves **YAML** by default (`Content-Type:
application/vnd.oai.openapi`); the targets pass `Accept: application/json` and
validate the result parses as JSON before generating.

### Adding a new field the backend already returns

Do **not** regenerate-and-hope. Add it to the hand-written pydantic schema and
the mapper first, then run `make sync-api-contract` so the contract catches up.

---

## ⚙️ Key Behavioral Patterns

### 1. Authentication & Session
- JWT tokens are **NEVER** exposed to the browser. They are stored in `HttpOnly`, `Secure` (unless `FLASK_DEBUG=true`), `SameSite=Lax` cookies via `JWTCookieManager`.
- Protected repositories are wrapped in `AutoRefreshingRepository`. If a 401 occurs, it automatically refreshes the token, retries the request, and stages the new cookies on `g.refreshed_tokens` to be written in the `after_request` hook.
- Staff status (`is_staff`) is extracted during login and stored in the Flask `session` for template rendering (`current_user_is_staff`).

### 2. CSRF Protection
- All state-changing routes (`POST`, `PATCH`, `DELETE`) are strictly POST-only.
- Every form MUST include `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`.
- Validation is done via `current_app.csrf_manager.validate_token(request.form.get("csrf_token"))`.
- The `csrf_token` and `today` variables are injected into all templates via a Flask `@app.context_processor`.

### 3. Domain Value Objects
- **`Money`**: Strictly non-negative. Used for transaction amounts.
- **`SignedMoney`**: Allows negative values. Used exclusively for `net_balance` in dashboards.
- **`TransactionType`**: Must remain `class TransactionType(str, Enum)`. Do not let Ruff "fix" this to `StrEnum`, as the current form serializes correctly to the DRF JSON API.

### 4. Error Handling in Views
Every view that calls a use case must handle backend failures gracefully:
```python
try:
    result = await use_case.execute(...)
except ServiceUnavailableError:
    raise  # Let the global 503 handler catch this
except DomainException as e:
    flash(str(e), "error") # Show user-friendly domain validation errors
except Exception:
    flash("An unexpected error occurred.", "error")
```

### 5. Alpine.js Scope Strictness
- **CRITICAL:** All interactive elements (buttons, modals, forms with `x-model`, `@click`, `$dispatch`) MUST be strictly enclosed within a single, top-level `x-data="..."` scope in the Jinja2 template.
- **NEVER** place action buttons outside the `x-data` subtree. If a button needs to open a modal, both the button and the modal must share the same parent `x-data` container.
- If a page requires multiple independent interactive zones, explicitly define separate `x-data` blocks for each, but do not mix their state.
- *Rationale:* Alpine.js silently ignores directives outside of an `x-data` scope, leading to dead UI elements (e.g., buttons that do nothing).

### 6. Direct Uploads (Nextcloud)
- Large files (documents, contracts) must NEVER be routed through the Flask BFF.
- Always use the Nextcloud Direct Upload API flow: BFF fetches token -> Frontend uploads directly to Nextcloud -> Frontend sends file path to BFF for DB registration.

---

## 🧪 Testing Strategy

- **Unit Tests (`tests/unit/`):** Pure Python. Test domain rules (e.g., `Money` cannot be negative) and use case orchestration using `unittest.mock.AsyncMock`.
- **Integration Tests (`tests/integration/`):** Test the infrastructure layer. Use `respx` to mock DRF HTTP responses and assert that mappers correctly translate DRF JSON to Domain Entities. `test_port_conformance.py` ensures adapters match their `Protocol` definitions exactly.
- **E2E Tests (`tests/e2e/`):** Use Playwright. **Do not use `page.route()` to mock the API**, because the browser never calls the DRF API directly (Flask does). Instead, the E2E suite spins up an in-thread `StubDRFServer` that Flask's `httpx` client talks to. This tests the *actual* HTTP headers, Bearer tokens, and auto-refresh flow.

---

## 🚀 How to Add a New Bounded Context

When adding a new feature (e.g., `goals`), do not stuff it into `budget`. Create a new isolated context:

1. **Scaffold:** `mkdir -p src/goals/{domain,application,infrastructure,interfaces}`
2. **Domain:** Define pure Python entities, value objects, and `typing.Protocol` ports.
3. **Application:** Write use cases that depend *only* on the ports.
4. **Infrastructure:** Implement the repository using `DRFAPIClient` and `pydantic` schemas.
5. **Interfaces:** Create a Flask `Blueprint`, ViewModels, and Jinja2 templates.
6. **Wire:** Import the repository and use cases into `src/interfaces/web/app.py`, attach them to the `app` object, and register the blueprint.
7. **Test:** Add unit, integration, and E2E tests. Run `make test-all`.
