"""Tests for graceful degradation when the DRF backend is unreachable.

The distinction under test throughout is the one the audit drew:

* the backend **answered** with an error -> the view handles it locally
  (flash message, empty dashboard). A reachable backend that is unhappy is
  something the user can act on, and existing local state is still meaningful.
* the backend **never answered** -> no HTTP response exists, so there is nothing
  honest to render. The request must become a branded 503.

Both paths are asserted here, because the bug this guards against is the second
one quietly being downgraded into the first: a user who owns a hundred
transactions must never be shown "No transactions yet" because a socket failed.
"""

import httpx
import pytest
import respx
from flask import Flask

from src.infrastructure.api.drf_client import ServiceUnavailableError
from src.infrastructure.auth.jwt_cookie_manager import ACCESS_COOKIE, REFRESH_COOKIE
from src.interfaces.web.app import create_app

BASE = "http://api.test/api/v1"

#: A port nothing listens on. Port 9 is the discard service, so a connection to
#: it is refused immediately rather than hanging for the full client timeout --
#: these tests are about the error path, not about waiting.
UNREACHABLE = "http://127.0.0.1:9/api/v1"

BRANDED = b"Service Temporarily Unavailable"


def _create_app(base_url: str) -> Flask:
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DRF_API_BASE_URL", BASE)
    monkeypatch.setenv("FLASK_DEBUG", "false")
    return _create_app(BASE)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def offline_app(monkeypatch):
    """An app pointed at a backend that does not exist."""
    monkeypatch.setenv("DRF_API_BASE_URL", UNREACHABLE)
    monkeypatch.setenv("FLASK_DEBUG", "false")
    return _create_app(UNREACHABLE)


@pytest.fixture
def offline_client(offline_app):
    return offline_app.test_client()


def authenticate(client):
    client.set_cookie(ACCESS_COOKIE, "acc-1")
    client.set_cookie(REFRESH_COOKIE, "ref-1")


class TestTransportFailureBecomesServiceUnavailableError:
    """The adapter is the boundary: no `httpx` exception escapes it."""

    async def test_connect_error_is_translated(self, app):
        """Driven through a use case, so this stays a black-box test of the
        boundary rather than reaching into the client for a private method."""
        with respx.mock:
            respx.get(f"{BASE}/dashboard/overview/").mock(
                side_effect=httpx.ConnectError("refused")
            )
            with pytest.raises(ServiceUnavailableError) as excinfo:
                await app.overview_use_case.execute("acc-1")
        # The original error is chained, so the traceback survives in the logs.
        assert isinstance(excinfo.value.__cause__, httpx.ConnectError)
        # And the DRF host is not baked into the message the web layer would log.
        assert "127.0.0.1" not in str(excinfo.value)

    async def test_timeout_is_translated(self, app):
        with respx.mock:
            respx.get(f"{BASE}/dashboard/overview/").mock(
                side_effect=httpx.ReadTimeout("too slow")
            )
            with pytest.raises(ServiceUnavailableError):
                await app.overview_use_case.execute("acc-1")

    async def test_transport_family_is_covered_beyond_timeout_and_connect(self, app):
        """Read errors and protocol errors are the same situation to this
        layer: no usable response. Pinning only the two named in the brief
        would leave a third turning into a 500 later."""
        for error in (
            httpx.ReadError("half-closed"),
            httpx.RemoteProtocolError("bad framing"),
            httpx.PoolTimeout("pool"),
        ):
            with respx.mock:
                respx.get(f"{BASE}/dashboard/overview/").mock(side_effect=error)
                with pytest.raises(ServiceUnavailableError):
                    await app.overview_use_case.execute("acc-1")

    @respx.mock
    def test_http_error_responses_are_not_translated(self, app):
        """A 500 is a real answer. It must stay a `DRFAPIClientError` so the
        views keep degrading locally instead of claiming the backend is
        unreachable."""
        respx.get(f"{BASE}/dashboard/overview/").mock(return_value=httpx.Response(500))
        client = app.test_client()
        authenticate(client)
        response = client.get("/dashboard/")
        assert response.status_code == 200
        assert BRANDED not in response.data


class TestBranded503Page:
    def test_dashboard_shows_503_when_backend_is_down(self, offline_client):
        authenticate(offline_client)
        response = offline_client.get("/dashboard/")
        assert response.status_code == 503
        assert BRANDED in response.data

    def test_transactions_show_503_when_backend_is_down(self, offline_client):
        authenticate(offline_client)
        response = offline_client.get("/transactions/")
        assert response.status_code == 503
        assert BRANDED in response.data

    def test_login_shows_503_when_backend_is_down(self, offline_app, offline_client):
        csrf_token = offline_app.csrf_manager.generate_token()
        response = offline_client.post(
            "/auth/login",
            data={
                "email": "ana@example.com",
                "password": "pw",
                "csrf_token": csrf_token,
            },
        )
        assert response.status_code == 503
        assert BRANDED in response.data

    def test_create_shows_503_when_backend_is_down(self, offline_app, offline_client):
        authenticate(offline_client)
        csrf_token = offline_app.csrf_manager.generate_token()
        response = offline_client.post(
            "/transactions/create",
            data={
                "type": "expense",
                "amount": "10.00",
                "description": "Lunch",
                "category": "Food",
                "csrf_token": csrf_token,
            },
        )
        assert response.status_code == 503
        assert BRANDED in response.data

    def test_delete_shows_503_when_backend_is_down(self, offline_app, offline_client):
        authenticate(offline_client)
        csrf_token = offline_app.csrf_manager.generate_token()
        response = offline_client.post(
            "/transactions/txn-1/delete", data={"csrf_token": csrf_token}
        )
        assert response.status_code == 503
        assert BRANDED in response.data

    def test_503_page_carries_a_way_back(self, offline_client):
        """A dead end is not graceful. The page must offer navigation."""
        authenticate(offline_client)
        body = offline_client.get("/dashboard/").data.decode()
        assert 'href="/"' in body

    def test_503_page_does_not_claim_the_user_has_no_data(self, offline_client):
        """The specific lie to avoid: an empty state reads as a fact about the
        user's finances."""
        authenticate(offline_client)
        body = offline_client.get("/dashboard/").data.decode()
        assert "No data" not in body
        assert "$0.00" not in body

    def test_503_page_leaks_no_internals(self, offline_client):
        authenticate(offline_client)
        body = offline_client.get("/dashboard/").data.decode()
        assert UNREACHABLE not in body
        assert "ConnectError" not in body
        assert "Traceback" not in body

    def test_503_page_does_not_depend_on_a_session(self, offline_client):
        """The page must render for an anonymous visitor too, since login is
        one of the flows that can trigger it."""
        response = offline_client.post(
            "/auth/login",
            data={
                "email": "a@b.c",
                "password": "pw",
                "csrf_token": offline_client.application.csrf_manager.generate_token(),
            },
        )
        assert response.status_code == 503
        assert b"Service Unavailable - Budget Tracker" in response.data

    def test_503_responses_carry_the_security_headers(self, offline_client):
        authenticate(offline_client)
        response = offline_client.get("/dashboard/")
        assert response.status_code == 503
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_503_template_has_no_session_dependencies(self, app):
        """The page must render with nothing but the request context, so it
        cannot depend on the session, the flash queue, or the CSRF context
        processor. Asserted structurally: if someone makes it extend
        ``base.html`` or read a context variable, this fails."""
        source = app.jinja_loader.get_source(app.jinja_env, "errors/503.html")[0]
        assert "extends" not in source
        for context_variable in ("csrf_token", "get_flashed_messages", "url_for"):
            assert context_variable not in source, (
                f"503.html now reads {context_variable}, which is unavailable "
                f"on the failure path"
            )


class TestLogoutAlwaysSucceedsLocally:
    """The one flow where a 503 would be worse than swallowing the error.

    Logging out is a local action. Refusing to clear the session because the
    backend is unreachable leaves the user authenticated against a service that
    cannot validate anything, so this path deliberately does *not* 503.
    """

    def test_logout_clears_cookies_even_when_backend_is_down(
        self, offline_app, offline_client
    ):
        authenticate(offline_client)
        csrf_token = offline_app.csrf_manager.generate_token()
        response = offline_client.post("/auth/logout", data={"csrf_token": csrf_token})
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]
        assert any(
            cookie.startswith(f"{ACCESS_COOKIE}=") and "Max-Age=0" in cookie
            for cookie in response.headers.getlist("Set-Cookie")
        )

    def test_session_is_really_gone_after_logout_with_backend_down(
        self, offline_client
    ):
        authenticate(offline_client)
        csrf_token = offline_client.application.csrf_manager.generate_token()
        offline_client.post("/auth/logout", data={"csrf_token": csrf_token})
        # The dashboard no longer authenticates anyone.
        assert offline_client.get("/dashboard/").status_code == 302


class TestReachableBackendKeepsLocalDegradation:
    """The other half of the contract: a 5xx from a *live* backend must keep
    degrading the way Checkpoint 4 built it, not turn into a 503."""

    @respx.mock
    def test_dashboard_renders_empty_on_500(self, client):
        authenticate(client)
        respx.get(f"{BASE}/dashboard/overview/").mock(return_value=httpx.Response(500))
        response = client.get("/dashboard/")
        assert response.status_code == 200
        assert b"$0.00" in response.data

    @respx.mock
    def test_transactions_flash_on_500(self, client):
        authenticate(client)
        respx.get(f"{BASE}/transactions/").mock(return_value=httpx.Response(500))
        response = client.get("/transactions/")
        assert response.status_code == 200
        assert b"Failed to load transactions" in response.data
