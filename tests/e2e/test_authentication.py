"""E2E tests for authentication flows.

Covers the login page, the login POST (success, bad credentials, backend
outage, forged CSRF) and logout. Because the BFF proxies DRF server-side, the
backend is faked at the network boundary; see ``tests/e2e/drf_stub.py``.
"""

from playwright.sync_api import Page, expect

from tests.e2e.drf_stub import DEFAULT_ACCESS_TOKEN, DEFAULT_REFRESH_TOKEN
from tests.e2e.support import (
    account_menu_button,
    expect_path,
    mock_drf_login,
    mock_drf_login_unavailable,
    mock_drf_logout,
    mock_drf_transactions_list,
    mock_drf_user_me,
    open_account_menu,
)

#: A token that is well-formed but not signed with the Flask secret key.
FORGED_CSRF_TOKEN = "forged-salt:0:" + "0" * 64


def fill_login_form(page: Page, email: str, password: str) -> None:
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)


def forge_csrf_token(page: Page, selector: str) -> None:
    """Replace a rendered CSRF token with one the server will not accept."""
    page.eval_on_selector(
        selector, "(el, token) => { el.value = token }", FORGED_CSRF_TOKEN
    )


def auth_cookie_names(page: Page) -> set[str]:
    """The names of the session cookies; the CSRF cookie is not one of them."""
    return {
        cookie["name"]
        for cookie in page.context.cookies()
        if cookie["name"] in {"bt_access_token", "bt_refresh_token"}
    }


class TestLoginPage:
    def test_login_page_loads(self, page: Page):
        page.goto("/auth/login")

        expect(page).to_have_title("Login - Budget Tracker")
        expect(page.locator("h2")).to_have_text("Sign In")
        expect(page.get_by_label("Email")).to_be_visible()
        expect(page.get_by_label("Password")).to_be_visible()
        expect(page.get_by_role("button", name="Sign In")).to_be_visible()

    def test_login_form_includes_a_csrf_token(self, page: Page):
        page.goto("/auth/login")

        csrf = page.locator("form input[name='csrf_token']")
        expect(csrf).to_be_attached()
        expect(csrf).to_have_attribute("type", "hidden")
        assert csrf.input_value(), "the rendered CSRF token must not be empty"

    def test_password_field_is_masked(self, page: Page):
        page.goto("/auth/login")
        expect(page.get_by_label("Password")).to_have_attribute("type", "password")

    def test_login_page_links_to_registration(self, page: Page):
        page.goto("/auth/login")
        page.get_by_role("link", name="Sign up").click()
        expect_path(page, "/users/register")
        expect(page.locator("h1")).to_have_text("Create Account")


class TestRegistration:
    def test_registration_is_advertised_as_unavailable(self, page: Page):
        page.goto("/auth/register")
        expect(page.get_by_text("Registration is not available yet.")).to_be_visible()
        expect(
            page.get_by_role("button", name="Sign Up (coming soon)")
        ).to_be_disabled()

    # The live registration flow is served by the users bounded context now;
    # it is covered end to end in tests/e2e/test_users.py.


class TestLogin:
    def test_login_with_valid_credentials_redirects_to_dashboard(
        self, page: Page, mock_drf
    ):
        mock_drf_login(mock_drf)
        mock_drf_transactions_list(mock_drf)

        page.goto("/auth/login")
        fill_login_form(page, "test@example.com", "password123")
        page.get_by_role("button", name="Sign In").click()

        expect_path(page, "/dashboard/")
        expect(page.locator("h1")).to_have_text("Dashboard")
        expect(page.get_by_text("Welcome back, Test User!")).to_be_visible()

    def test_login_greeting_comes_from_the_user_endpoint(self, page: Page, mock_drf):
        """The name is read from /users/me/, not taken from the login form."""
        mock_drf_login(mock_drf)
        mock_drf_user_me(mock_drf, full_name="Ana Ruiz", plan="pro")
        mock_drf_transactions_list(mock_drf)

        page.goto("/auth/login")
        fill_login_form(page, "ana@example.com", "password123")
        page.get_by_role("button", name="Sign In").click()

        expect_path(page, "/dashboard/")
        expect(page.get_by_text("Welcome back, Ana Ruiz!")).to_be_visible()
        assert mock_drf.calls("GET", "/users/me/") != []

    def test_login_stores_the_tokens_in_httponly_cookies(self, page: Page, mock_drf):
        mock_drf_login(mock_drf)
        mock_drf_transactions_list(mock_drf)

        page.goto("/auth/login")
        fill_login_form(page, "test@example.com", "password123")
        page.get_by_role("button", name="Sign In").click()
        expect_path(page, "/dashboard/")

        cookies = {cookie["name"]: cookie for cookie in page.context.cookies()}
        access = cookies["bt_access_token"]
        refresh = cookies["bt_refresh_token"]
        assert access["value"] == DEFAULT_ACCESS_TOKEN
        assert refresh["value"] == DEFAULT_REFRESH_TOKEN
        # The whole point of the BFF: the tokens must never be script-readable.
        assert access["httpOnly"] is True
        assert refresh["httpOnly"] is True

    def test_login_forwards_the_credentials_to_drf(self, page: Page, mock_drf):
        mock_drf_login(mock_drf)
        mock_drf_transactions_list(mock_drf)

        page.goto("/auth/login")
        fill_login_form(page, "test@example.com", "password123")
        page.get_by_role("button", name="Sign In").click()
        expect_path(page, "/dashboard/")

        calls = mock_drf.calls("POST", "/users/auth/login/")
        assert len(calls) == 1
        assert calls[0].json == {
            "email": "test@example.com",
            "password": "password123",
        }

    def test_login_with_invalid_credentials_shows_an_error(self, page: Page, mock_drf):
        mock_drf_login(mock_drf, success=False)

        page.goto("/auth/login")
        fill_login_form(page, "test@example.com", "wrongpassword")
        page.get_by_role("button", name="Sign In").click()

        expect_path(page, "/auth/login")
        expect(page.get_by_text("Invalid email or password")).to_be_visible()
        # Nothing was issued, so the browser still has no session.
        assert auth_cookie_names(page) == set()

    def test_login_does_not_leak_the_backend_error(self, page: Page, mock_drf):
        mock_drf_login(mock_drf, success=False)

        page.goto("/auth/login")
        fill_login_form(page, "test@example.com", "wrongpassword")
        page.get_by_role("button", name="Sign In").click()

        expect(page.get_by_text("No active account found.")).to_have_count(0)

    def test_login_survives_a_backend_outage(self, page: Page, mock_drf):
        mock_drf_login_unavailable(mock_drf)

        page.goto("/auth/login")
        fill_login_form(page, "test@example.com", "password123")
        page.get_by_role("button", name="Sign In").click()

        expect_path(page, "/auth/login")
        expect(page.get_by_text("Authentication service unavailable")).to_be_visible()

    def test_login_rejects_a_forged_csrf_token(self, page: Page, mock_drf):
        mock_drf_login(mock_drf)

        page.goto("/auth/login")
        fill_login_form(page, "test@example.com", "password123")
        forge_csrf_token(page, "input[name='csrf_token']")
        page.get_by_role("button", name="Sign In").click()

        expect_path(page, "/auth/login")
        expect(page.get_by_text("Invalid or expired CSRF token")).to_be_visible()
        # The credentials must never have left the server.
        assert mock_drf.calls("POST", "/users/auth/login/") == []

    def test_login_shows_a_password_too_short_hint(self, page: Page):
        page.goto("/auth/login")
        page.get_by_label("Email").fill("test@example.com")
        page.get_by_label("Password").fill("short")

        expect(
            page.get_by_text("Password must be at least 8 characters.")
        ).to_be_visible()


class TestLogout:
    def test_logout_clears_the_session_and_redirects(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf)
        page.goto("/dashboard/")

        open_account_menu(page)
        page.get_by_role("button", name="Logout").click()

        expect_path(page, "/auth/login")
        expect(page.get_by_text("You have been logged out.")).to_be_visible()
        assert auth_cookie_names(page) == set()

    def test_logout_revokes_the_token_on_drf(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf)
        page.goto("/dashboard/")

        open_account_menu(page)
        page.get_by_role("button", name="Logout").click()
        expect_path(page, "/auth/login")

        calls = mock_drf.calls("POST", "/users/auth/logout/")
        assert len(calls) == 1
        assert calls[0].bearer_token == DEFAULT_ACCESS_TOKEN

    def test_logout_survives_a_failing_backend_revocation(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf)
        mock_drf_logout(mock_drf, success=False)
        page.goto("/dashboard/")

        open_account_menu(page)
        page.get_by_role("button", name="Logout").click()

        # A remote failure must not trap the user in a session they asked to end.
        expect_path(page, "/auth/login")
        assert auth_cookie_names(page) == set()

    def test_logout_rejects_a_forged_csrf_token(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf)
        page.goto("/dashboard/")

        open_account_menu(page)
        forge_csrf_token(page, "nav form input[name='csrf_token']")
        page.get_by_role("button", name="Logout").click()

        expect(page.get_by_text("Invalid or expired CSRF token.")).to_be_visible()
        # Still signed in, and the token was never revoked.
        expect_path(page, "/dashboard/")
        assert mock_drf.calls("POST", "/users/auth/logout/") == []
        assert "bt_access_token" in auth_cookie_names(page)

    def test_protected_pages_redirect_to_login(self, page: Page):
        for path in ("/dashboard/", "/transactions/"):
            page.goto(path)
            expect_path(page, "/auth/login")


class TestAccountMenu:
    """The nav's avatar dropdown.

    Worth E2E coverage specifically because Alpine.js silently ignores
    directives outside an `x-data` scope: a toggle wired outside the scope
    renders perfectly and does nothing, which no unit test would catch.
    """

    def test_menu_items_are_hidden_until_toggled(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf)
        page.goto("/dashboard/")

        expect(page.get_by_role("button", name="Logout")).to_be_hidden()

        account_menu_button(page).click()

        expect(page.get_by_role("button", name="Logout")).to_be_visible()
        expect(page.get_by_role("link", name="Settings")).to_be_visible()

    def test_toggle_closes_the_menu(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf)
        page.goto("/dashboard/")

        account_menu_button(page).click()
        expect(page.get_by_role("button", name="Logout")).to_be_visible()

        account_menu_button(page).click()

        expect(page.get_by_role("button", name="Logout")).to_be_hidden()

    def test_clicking_outside_closes_the_menu(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(mock_drf)
        page.goto("/dashboard/")

        account_menu_button(page).click()
        expect(page.get_by_role("button", name="Logout")).to_be_visible()

        # The `@click.outside` dismiss: a click well clear of the dropdown.
        page.get_by_role("heading", name="Dashboard").click()

        expect(page.get_by_role("button", name="Logout")).to_be_hidden()

    def test_settings_link_reaches_the_profile(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf)
        page.goto("/dashboard/")

        account_menu_button(page).click()
        page.get_by_role("link", name="Settings").click()

        expect_path(page, "/profile/")

    def test_initials_come_from_the_signed_in_user(self, page: Page, mock_drf):
        # Only a real login populates `user_name`; the `authed` fixture seeds
        # cookies directly and skips it.
        mock_drf_login(mock_drf)
        mock_drf_transactions_list(mock_drf)
        page.goto("/auth/login")
        fill_login_form(page, "test@example.com", "password123")
        page.get_by_role("button", name="Sign In").click()
        expect_path(page, "/dashboard/")

        # "Test User" -> "TU". Asserted because an avatar badge that renders
        # blank is a silent regression nothing else would catch.
        expect(page.get_by_test_id("avatar-initials")).to_have_text("TU")
        # Scoped to the nav: the same name also appears in the welcome flash.
        expect(page.locator("nav").get_by_text("Test User")).to_be_visible()

    def test_avatar_degrades_when_the_session_has_no_name(
        self, page: Page, authed: Page, mock_drf
    ):
        # Cookies set without a login carry no `user_name`. The badge must still
        # render something rather than an empty circle.
        mock_drf_transactions_list(mock_drf)
        page.goto("/dashboard/")

        expect(page.get_by_test_id("avatar-initials")).to_have_text("U")
