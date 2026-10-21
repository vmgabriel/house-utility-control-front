"""The rebrand's contract, asserted on rendered pages.

Two things a rename silently breaks, both invisible in a screenshot:

1. The favicon link points at a file the static route does not actually serve,
   so the browser requests a URL that 404s and the tab keeps the generic globe.
2. One page keeps the old name, because it does not extend `base.html` and the
   brand lives there -- the login and error pages are exactly those.

Both are cheap to assert here.
"""

import httpx
import pytest
import respx

from src.interfaces.web.app import create_app
from src.shared.auth.jwt_cookie_manager import ACCESS_COOKIE, REFRESH_COOKIE

BASE = "http://api.test/api/v1"

OLD_BRAND = "Budget Tracker"
NEW_BRAND = "Home Utility"

# The ICO container: reserved (2 bytes), type 1 (icon), then the frame count.
ICO_MAGIC = b"\x00\x00\x01\x00"

FAVICON_LINK = '<link rel="icon" type="image/x-icon" href="/static/favicon.ico">'


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DRF_API_BASE_URL", BASE)
    monkeypatch.setenv("FLASK_DEBUG", "false")
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def dashboard_html(client) -> str:
    """The rendered dashboard, which extends `base.html`."""
    client.set_cookie(ACCESS_COOKIE, "acc-1")
    client.set_cookie(REFRESH_COOKIE, "ref-1")

    with respx.mock:
        respx.get(f"{BASE}/dashboard/overview/").mock(
            return_value=httpx.Response(
                200,
                json={"today": None, "this_week": None, "this_month": None},
            )
        )
        respx.get(f"{BASE}/profile/").mock(
            return_value=httpx.Response(500, json={"detail": "unavailable"})
        )
        respx.get(f"{BASE}/transactions/accounts/").mock(
            return_value=httpx.Response(200, json={"count": 0, "results": []})
        )
        return client.get("/dashboard/").get_data(as_text=True)


class TestFavicon:
    def test_the_icon_is_served(self, client):
        """The static route must actually have the file behind it.

        A link tag pointing at a missing file renders fine and 404s silently in
        the browser, which is the failure this pins down.
        """
        response = client.get("/static/favicon.ico")
        assert response.status_code == 200
        assert "icon" in response.headers["Content-Type"]
        assert response.data.startswith(ICO_MAGIC)

    def test_the_link_tag_points_at_it(self, dashboard_html):
        assert FAVICON_LINK in dashboard_html

    def test_the_standalone_pages_declare_it_too(self, client):
        """`base.html` owns the link for every page that extends it.

        The login, register and error pages do not extend it, so they carry
        their own -- otherwise the tab icon changes the moment the user signs
        out.
        """
        for path in ("/auth/login", "/auth/register", "/users/register"):
            body = client.get(path).get_data(as_text=True)
            assert 'href="/static/favicon.ico"' in body, path


class TestBrand:
    def test_the_nav_shows_the_new_name(self, dashboard_html):
        assert NEW_BRAND in dashboard_html
        assert OLD_BRAND not in dashboard_html

    def test_the_page_title_carries_the_brand(self, dashboard_html):
        assert "<title>Home Utility - Dashboard</title>" in dashboard_html

    def test_the_login_page_is_rebranded(self, client):
        body = client.get("/auth/login").get_data(as_text=True)
        assert "<title>Home Utility - Login</title>" in body
        assert NEW_BRAND in body
        assert OLD_BRAND not in body

    def test_the_old_name_is_gone_from_the_public_pages(self, client):
        """Every page a visitor can reach without a session.

        The old name on one of them is a customer-facing bug that no unit test
        elsewhere would catch.
        """
        for path in ("/auth/login", "/auth/register", "/users/register"):
            body = client.get(path).get_data(as_text=True)
            assert OLD_BRAND not in body, path
            assert NEW_BRAND in body, path
