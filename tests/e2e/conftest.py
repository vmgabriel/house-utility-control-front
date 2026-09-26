"""Playwright test configuration and fixtures.

Three pieces of infrastructure make the E2E suite deterministic:

1. :class:`StubDRFServer` -- a real HTTP server that impersonates the DRF
   backend, so the Flask BFF's own ``httpx`` calls land somewhere predictable.
   See ``drf_stub`` for why Playwright route interception cannot be used here.
2. ``live_server`` -- the real Flask app, served by Werkzeug on a background
   thread, with ``DRF_API_BASE_URL`` pointed at the stub.
3. ``offline_assets`` -- Alpine.js is served from ``tests/e2e/vendor`` and
   Tailwind's CDN script is stubbed out, so no test depends on a third-party
   CDN being reachable. Alpine is the one dependency that changes behaviour:
   it is what reveals the create-transaction modal and the dashboard cards.

The browser fixtures are defined here rather than taken from ``pytest-playwright``
because that plugin wraps ``pytest_runtest_call`` for *every* test in the
session, which breaks ``pytest-asyncio``'s auto mode as soon as the E2E and
async unit suites are collected together. Owning four small fixtures keeps
``make test-all`` working; ``--headed`` and ``--browser`` are re-declared below.
"""

from __future__ import annotations

import inspect
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from werkzeug.serving import BaseWSGIServer, make_server

from src.interfaces.web.app import create_app
from tests.e2e.drf_stub import EXPIRED_ACCESS_TOKEN, StubDRFServer
from tests.e2e.support import set_auth_cookies

VENDOR_DIR = Path(__file__).parent / "vendor"

#: The CDN scripts the templates pull in. Matched by prefix, since the Alpine
#: URL pins a version range rather than an exact filename.
ALPINE_CDN = re.compile(r"^https://cdn\.jsdelivr\.net/npm/alpinejs")
TAILWIND_CDN = re.compile(r"^https://cdn\.tailwindcss\.com")

#: A browser-side call to the API would mean the BFF contract was bypassed.
BROWSER_API_CALL = re.compile(r"/api/v\d+/")

#: Anything off the loopback interface. The suite must not depend on the
#: internet, so a leak fails loudly instead of passing on a warm cache.
OFF_MACHINE = re.compile(r"^https?://(?!(?:127\.0\.0\.1|localhost)(?::\d+)?(?:/|$))")


# ---------------------------------------------------------------------------
# Servers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drf_stub() -> Iterator[StubDRFServer]:
    """The fake DRF backend, shared by every test in the session."""
    server = StubDRFServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(scope="session")
def live_server(drf_stub: StubDRFServer) -> Iterator[str]:
    """Run the real Flask app in a thread and yield its base URL.

    Ports are bound to 0 and read back, so a busy 5000/8000 never breaks the
    suite. Environment variables are set explicitly (and restored) because
    ``create_app`` reads them at call time.
    """
    previous = {
        key: os.environ.get(key)
        for key in ("DRF_API_BASE_URL", "FLASK_SECRET_KEY", "FLASK_DEBUG")
    }
    os.environ["DRF_API_BASE_URL"] = drf_stub.base_url
    os.environ["FLASK_SECRET_KEY"] = "e2e-test-secret-key"
    os.environ["FLASK_DEBUG"] = "false"

    app = create_app()
    # TESTING stays off on purpose: the app must answer like production, which
    # is what the graceful-degradation tests are actually about.
    app.config["TESTING"] = False

    server: BaseWSGIServer = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(
        target=server.serve_forever, name="flask-live-server", daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session")
def base_url(live_server: str) -> str:
    """The origin the browser is pointed at, for relative ``page.goto`` calls."""
    return live_server


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("e2e", "End-to-end tests")
    group.addoption(
        "--headed",
        action="store_true",
        default=False,
        help="Run the browser with a visible window.",
    )
    group.addoption(
        "--browser",
        default="chromium",
        choices=["chromium", "firefox", "webkit"],
        help="Browser engine the E2E suite runs with.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """Refuse to run E2E and async tests inside one pytest process.

    Playwright's sync API suspends its event loop inside a greenlet for the
    lifetime of the session, so the thread still reports a running loop
    afterwards. pytest-asyncio's auto mode executes every coroutine through
    ``asyncio.Runner.run``, which refuses to start while a loop is running --
    so collecting both suites yields dozens of ``Runner.run() cannot be called
    from a running event loop`` errors that say nothing about the cause.

    Failing here instead turns that into one actionable message.
    """
    has_e2e = any("tests/e2e/" in str(item.fspath) for item in items)
    has_async = any(
        inspect.iscoroutinefunction(getattr(item, "function", None)) for item in items
    )
    if has_e2e and has_async:
        pytest.exit(
            "The E2E suite and the async unit/integration suites cannot share a "
            "pytest process: Playwright's sync API holds the event loop for the "
            "whole session, which breaks pytest-asyncio's auto mode.\n"
            "Run them separately:\n"
            "  make test        # unit + integration\n"
            "  make test-e2e    # end-to-end (or make test-all for both)",
            returncode=1,
        )


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as instance:
        yield instance


@pytest.fixture(scope="session")
def browser_type(playwright_instance, pytestconfig):
    return getattr(playwright_instance, pytestconfig.getoption("--browser"))


@pytest.fixture(scope="session")
def browser(browser_type: Browser, pytestconfig) -> Iterator[Browser]:
    """One browser for the whole session; contexts isolate the tests."""
    instance = browser_type.launch(headless=not pytestconfig.getoption("--headed"))
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def context(browser: Browser, base_url: str) -> Iterator[BrowserContext]:
    """A fresh context per test, so cookies never leak between them."""
    ctx = browser.new_context(base_url=base_url)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def page(context: BrowserContext) -> Iterator[Page]:
    page_instance = context.new_page()
    try:
        yield page_instance
    finally:
        page_instance.close()


# ---------------------------------------------------------------------------
# Backend stubbing
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_drf(drf_stub: StubDRFServer) -> Iterator[StubDRFServer]:
    """The fake DRF backend, reset to its happy-path defaults for one test."""
    drf_stub.reset()
    try:
        yield drf_stub
    finally:
        drf_stub.reset()


# ---------------------------------------------------------------------------
# Browser assets
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def offline_assets(page: Page) -> Iterator[Page]:
    """Serve Alpine locally, stub Tailwind, and block the outside world.

    Route interception earns its keep here rather than on the API: these are
    genuine browser requests, so ``page.route`` is the correct tool. Letting
    the CDN load would make every modal and card assertion depend on someone
    else's uptime.
    """
    alpine = (VENDOR_DIR / "alpine.min.js").read_text(encoding="utf-8")

    def serve_alpine(route) -> None:
        route.fulfill(status=200, content_type="text/javascript", body=alpine)

    def skip_tailwind(route) -> None:
        # Tailwind only styles; no assertion depends on it, and the JIT build
        # is the slowest request on the page.
        route.fulfill(status=200, content_type="text/javascript", body="")

    def reject_api(route) -> None:
        route.abort()
        raise AssertionError(
            f"The browser called the API directly at {route.request.url}. "
            "The BFF must proxy DRF server-side; use the `mock_drf` fixture."
        )

    def reject_external(route) -> None:
        route.abort()
        raise AssertionError(
            f"The E2E suite tried to reach {route.request.url}. Tests must be "
            "hermetic: serve the asset locally instead of fetching it."
        )

    # Registered last, so the two CDN routes above win for their own URLs.
    page.route(OFF_MACHINE, reject_external)
    page.route(BROWSER_API_CALL, reject_api)
    page.route(TAILWIND_CDN, skip_tailwind)
    page.route(ALPINE_CDN, serve_alpine)
    yield page


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def authed(page: Page, base_url: str) -> Page:
    """A page that already carries a valid access token cookie."""
    set_auth_cookies(page, base_url=base_url)
    return page


@pytest.fixture
def expired(page: Page, base_url: str) -> Page:
    """A page whose access token the backend rejects, forcing a refresh."""
    set_auth_cookies(page, access_token=EXPIRED_ACCESS_TOKEN, base_url=base_url)
    return page
