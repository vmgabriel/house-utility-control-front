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
```

**Focused Testing:**
```bash
hatch run pytest tests/integration/profile/test_repository.py -v
```

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
