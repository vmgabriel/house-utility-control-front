# BFF-Specific Rules (Flask + Jinja2 + Alpine.js)

## Authentication & Cookies
- JWT tokens stored in `HttpOnly`, `Secure` (unless `FLASK_DEBUG=true`), `SameSite=Lax` cookies via `JWTCookieManager`.
- Tokens NEVER exposed to browser JS. `document.cookie` must not contain them (asserted in E2E).
- `session["user_id"]`, `session["is_staff"]`, `session["user_name"]` populated on login; cleared on logout.

## CSRF
- All state-changing routes are POST-only and validate `request.form["csrf_token"]` via `current_app.csrf_manager.validate_token()`.
- Token injected into all templates via `@app.context_processor` (key: `csrf_token`).
- Cookie set in `after_request` hook; NOT `HttpOnly` (JS may read it for `X-CSRF-Token` header on `fetch()` calls).
- Every form MUST include `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`.

## Views
- Views are `async def` (Flask runs via `asgiref`).
- Protected views first read access token from `JWTCookieManager`; redirect to `auth.login` if absent.
- Re-raise `ServiceUnavailableError` explicitly above broad `except Exception` so unreachable DRF surfaces branded `503` page.
- No generic `500` handler — a 500 is a bug and must show traceback.

## Templates & Alpine.js
- Base template: `src/interfaces/web/templates/base.html` (extends by all pages).
- Each Alpine component must have its own `x-data` scope. Multiple independent zones per page are allowed (rule 8 in AGENTS.md).
- Modal triggers: use `$dispatch('event-name')` from a button inside the same `x-data` scope as the modal, OR hoist scope to `<body>`.
- Pass data from Jinja to Alpine via `data-*` attributes read through `$el.dataset`, NOT interpolated into `x-data` body (Jinja HTML-escapes but doesn't JS-escape).
- Use `x-cloak` on server-rendered Alpine elements to prevent flash before boot.
- `{{ csrf_token }}` and `{{ today }}` injected by context processor — never assume they're available without it.

## Security Headers
- `src/interfaces/web/security.py` adds `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy` on every response via `after_request`.
- No `Content-Security-Policy` (Tailwind/Alpine CDN requires `'unsafe-inline'`). Test asserts CSP is absent.

## Error Handling
- `httpx.TransportError` → `ServiceUnavailableError` at `DRFAPIClient` boundary.
- `DomainException` → flash message to user.
- Broad `Exception` → generic flash, never leak stack trace to user.
- Logout always clears cookies locally even if backend is unreachable.

## ViewModels
- Dataclasses with `@classmethod from_domain(cls, entity)` factory.
- Format currency, dates, CSS classes, and display strings here — NOT in templates.
- Templates receive ViewModels, never raw domain entities.
