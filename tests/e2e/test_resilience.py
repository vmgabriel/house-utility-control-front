"""Resilience and security tests in a real browser.

Two things the component tests cannot check:

1. The security headers on a response actually served by Werkzeug over a real
   socket, rather than by the in-process test client.
2. That the branded 503 page renders correctly in a browser -- Tailwind loads,
   nothing depends on Alpine, and the escape link is clickable.

The outage server here points the BFF at a port with nothing listening, so
``httpx`` fails at the transport layer. That is a different failure from the one
the ``mock_drf`` helpers can produce: they answer with an HTTP *status*, which
means the backend did reach us and the BFF is right to degrade locally.
"""

from __future__ import annotations

import os
import re
import socket
import threading
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect
from werkzeug.serving import make_server

from src.interfaces.web.app import create_app
from tests.e2e.support import set_auth_cookies

#: Routes that change state. Each must be POST-only; a GET has to 405.
STATE_CHANGING_ROUTES = [
    "/auth/logout",
    "/transactions/create",
    "/transactions/tx-1/delete",
]

EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-xss-protection": "1; mode=block",
    "referrer-policy": "strict-origin-when-cross-origin",
}


def _closed_port() -> int:
    """A port that was free a moment ago, so connecting to it is refused.

    Binding to port 0 and releasing is the reliable way to get one; a hardcoded
    port like 9 can be served by a real discard daemon on some machines, and a
    test that silently stops testing is worse than no test.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def outage_base_url() -> Iterator[str]:
    """Serve the real BFF with the DRF backend pointed at a dead port.

    A second server rather than a reconfigured one because ``live_server`` is
    session-scoped and every other E2E test depends on it reaching the stub.
    """
    previous = {
        key: os.environ.get(key)
        for key in ("DRF_API_BASE_URL", "FLASK_SECRET_KEY", "FLASK_DEBUG")
    }
    os.environ["DRF_API_BASE_URL"] = f"http://127.0.0.1:{_closed_port()}/api/v1"
    os.environ["FLASK_SECRET_KEY"] = "e2e-test-secret-key"
    os.environ["FLASK_DEBUG"] = "false"

    app = create_app()
    app.config["TESTING"] = False
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(
        target=server.serve_forever, name="flask-outage-server", daemon=True
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


@pytest.fixture
def outage_page(browser: Browser, outage_base_url: str) -> Iterator[Page]:
    """A page scoped to the outage server, authenticated but unreachable."""
    ctx: BrowserContext = browser.new_context(base_url=outage_base_url)
    try:
        instance = ctx.new_page()
        set_auth_cookies(instance, base_url=outage_base_url)
        yield instance
    finally:
        ctx.close()


class TestSecurityHeadersOverTheWire:
    """The component tests use the in-process client. This confirms Werkzeug
    emits the same headers on a genuine socket."""

    def test_headers_on_a_rendered_page(self, authed: Page):
        response = authed.goto("/dashboard/")
        for header, value in EXPECTED_HEADERS.items():
            assert response.headers[header] == value

    def test_headers_on_a_redirect(self, authed: Page):
        """`/` is a 302, and a redirect is just as attackable as a page: it
        carries a `Location` and leaks a `Referer` on the next hop."""
        response = authed.request.get("/", max_redirects=0)
        assert response.status == 302
        for header, value in EXPECTED_HEADERS.items():
            assert response.headers[header] == value

    def test_headers_on_json(self, page: Page):
        response = page.goto("/healthz")
        assert response.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize("url", STATE_CHANGING_ROUTES)
    def test_state_changing_routes_reject_get_in_a_browser(
        self, authed: Page, url: str
    ):
        response = authed.goto(url)
        assert response.status == 405, f"GET {url} was not rejected"
        assert "POST" in response.headers["allow"]

    def test_token_cookies_are_not_readable_from_javascript(self, authed: Page):
        """The end-to-end proof of the HttpOnly guarantee: ask the page itself
        what cookies it can see."""
        authed.goto("/dashboard/")
        visible = authed.evaluate("document.cookie")
        assert "bt_access_token" not in visible
        assert "bt_refresh_token" not in visible


class TestOutageIsBrandedNotFatal:
    """The backend being unreachable must never produce a 500."""

    @pytest.mark.parametrize("url", ["/dashboard/", "/transactions/"])
    def test_pages_serve_503(self, outage_page: Page, url: str):
        response = outage_page.goto(url)
        assert response.status == 503
        expect(
            outage_page.get_by_role("heading", name="Service Temporarily Unavailable")
        ).to_be_visible()

    def test_the_login_page_still_renders_during_an_outage(self, outage_page: Page):
        """The login form itself never calls DRF, so it must keep working. A
        user whose session expired during an outage needs somewhere to type."""
        response = outage_page.goto("/auth/login")
        assert response.status == 200
        expect(outage_page.get_by_role("heading", name="Sign In")).to_be_visible()

    def test_submitting_the_login_form_serves_503(self, outage_page: Page):
        """...but submitting it does call DRF, and that is where the outage
        surfaces."""
        outage_page.goto("/auth/login")
        outage_page.fill('input[name="email"]', "ana@example.com")
        outage_page.fill('input[name="password"]', "password123")
        with outage_page.expect_response(
            lambda r: "/auth/login" in r.url and r.request.method == "POST"
        ) as response:
            outage_page.click('button[type="submit"]')
        assert response.value.status == 503

    def test_the_error_page_renders_with_tailwind(self, outage_page: Page):
        """A 503 that loses its styling is still a 503, but it reads as broken.
        The body class proves the Tailwind CDN script ran."""
        outage_page.goto("/dashboard/")
        expect(outage_page.locator("body")).to_have_class(re.compile(r"bg-gray-50"))

    def test_the_error_page_keeps_the_security_headers(self, outage_page: Page):
        response = outage_page.goto("/dashboard/")
        for header, value in EXPECTED_HEADERS.items():
            assert response.headers[header] == value

    def test_the_error_page_offers_a_way_back(self, outage_page: Page):
        outage_page.goto("/dashboard/")
        home = outage_page.get_by_role("link", name="Return to Home")
        expect(home).to_be_visible()
        expect(home).to_have_attribute("href", "/")
        home.click()
        # `/` redirects to the dashboard, which 503s again -- still not a 500.
        expect(
            outage_page.get_by_role("heading", name="Service Temporarily Unavailable")
        ).to_be_visible()

    def test_the_error_page_does_not_claim_the_account_is_empty(
        self, outage_page: Page
    ):
        """The dangerous outcome is not a 500, it is a convincing lie. A user
        with transactions must never be shown an empty dashboard."""
        outage_page.goto("/transactions/")
        expect(outage_page.get_by_text("No transactions yet")).not_to_be_attached()

    def test_the_error_page_leaks_no_internals(self, outage_page: Page):
        outage_page.goto("/dashboard/")
        body = outage_page.locator("body").inner_text()
        assert "ConnectError" not in body
        assert "Traceback" not in body
        assert "127.0.0.1" not in body


class TestOutageDoesNotLogTheUserOut:
    """A flaky network must not end the session."""

    def test_navigating_through_an_outage_keeps_the_session(self, outage_page: Page):
        outage_page.goto("/dashboard/")
        expect(
            outage_page.get_by_role("heading", name="Service Temporarily Unavailable")
        ).to_be_visible()
        # Still authenticated: the cookies survived, so a recovered backend
        # brings the user straight back to their data.
        cookies = {c["name"] for c in outage_page.context.cookies()}
        assert "bt_access_token" in cookies
        assert "bt_refresh_token" in cookies
