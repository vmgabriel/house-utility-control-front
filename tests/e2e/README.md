# End-to-end tests

Playwright drives a real Chromium against the real Flask app, so every test
exercises the whole chain: WSGI routing, `async def` views through asgiref,
Jinja2 rendering, CSRF and cookie handling, and the `httpx` client.

```bash
make test-e2e          # headless
make test-e2e-headed   # visible browser, for debugging a journey
```

## Layout

| File | Purpose |
| --- | --- |
| `drf_stub.py` | A fake DRF backend served over HTTP on a background thread |
| `support.py` | `mock_drf_*` helpers and locator utilities |
| `conftest.py` | Fixtures: the two servers, the browser, offline assets, auth |
| `test_authentication.py` | Login, logout, CSRF, session cookies |
| `test_transactions.py` | List, create modal, create, delete |
| `test_dashboard.py` | Summary cards and graceful degradation |
| `test_smoke.py` | The harness itself: if these fail, everything else is noise |
| `vendor/alpine.min.js` | Served locally so no test depends on a CDN |

## Why the backend is faked over HTTP, not with `page.route`

The obvious approach is `page.route("**/api/v1/...")`. It does not work here,
and would fail *silently*.

This frontend is a server-side-rendered BFF. Every DRF call is made by Flask
through `httpx`; the browser never issues an API request. `page.route` only
intercepts requests that originate inside the page, so a route for
`/api/v1/transactions/` would match nothing. The suite would go green while
every test silently hit a real (absent) backend and exercised only the error
paths.

The honest seam is the network boundary between Flask and DRF, so the fixture
starts a real HTTP server that impersonates DRF and points
`DRF_API_BASE_URL` at it. `drf_stub.py` documents this in full.

A side benefit: the stub runs in a thread inside the pytest process, so tests
read and write it directly (`stub.requests`, `stub.transactions`) with no
control channel. That is how the tests assert on the payload the BFF actually
sent, and on the `Authorization` header it forwarded.

Route interception is still used, for the requests the browser genuinely makes:
Alpine is served from `vendor/`, Tailwind's CDN script is stubbed to an empty
body, and any off-loopback request fails the test. The suite is hermetic by
construction rather than by luck.

The stub also enforces `Authorization: Bearer` and keeps a set of accepted
tokens, so the 401 → refresh → single retry flow in `AutoRefreshingRepository`
is exercised for real instead of mocked away.

## Ports

Both servers bind to port 0 and read the port back, so a busy 5000 or 8000
never breaks the suite and two runs can coexist.

## The E2E suite needs its own pytest process

Playwright's sync API suspends its event loop inside a greenlet for the
lifetime of the session, so the thread keeps reporting a running loop
afterwards. `pytest-asyncio` in auto mode runs every coroutine through
`asyncio.Runner.run`, which refuses to start while a loop is running.

Collecting `tests/` in one process therefore produces dozens of
`Runner.run() cannot be called from a running event loop` errors. `make test-all`
runs the suites as two processes, and `conftest.py` exits with an explanation
if you try to combine them.

For the same reason the suite drives Playwright directly instead of using
`pytest-playwright`: that plugin wraps `pytest_runtest_call` for every test in
the session, which breaks the async suites on its own.
