"""E2E tests for the profile page."""

from playwright.sync_api import Page, expect

from tests.e2e.support import (
    expect_path,
    form_csrf_token,
    mock_drf_profile_failure,
)


class TestProfilePage:
    def test_redirects_when_not_authenticated(self, page: Page):
        page.goto("/profile/")
        expect_path(page, "/auth/login")

    def test_renders_general_tab_by_default(self, page: Page, authed: Page, mock_drf):
        page.goto("/profile/")

        expect(page).to_have_title("Profile & Settings - Budget Tracker")
        expect(page.locator("h1")).to_have_text("Account Settings")
        general_form = page.locator("form[action$='/profile/']")
        expect(general_form).to_be_visible()

    def test_profile_data_is_prefilled(self, page: Page, authed: Page, mock_drf):
        mock_drf.seed_profile({"first_name": "Ada", "timezone": "Europe/Madrid"})
        page.goto("/profile/")

        expect(page.locator("input[name='first_name']")).to_have_value("Ada")
        expect(page.locator("select[name='timezone']")).to_have_value("Europe/Madrid")

    def test_tab_switches_to_preferences_without_reload(
        self, page: Page, authed: Page, mock_drf
    ):
        page.goto("/profile/")
        page.get_by_role("button", name="Preferences").click()

        prefs_form = page.locator("form[action$='/profile/preferences']")
        expect(prefs_form).to_be_visible()
        general_form = page.locator("form[action$='/profile/']")
        expect(general_form).to_be_hidden()

    def test_preferences_form_submits(self, page: Page, authed: Page, mock_drf):
        page.goto("/profile/")
        page.get_by_role("button", name="Preferences").click()

        page.locator("select[name='language']").select_option("en")
        page.locator("select[name='currency']").select_option("EUR")
        page.get_by_role("button", name="Save Preferences").click()

        expect(page.get_by_text("Preferences updated successfully!")).to_be_visible()
        calls = mock_drf.calls("PATCH", "/profile/me/preferences/")
        assert len(calls) == 1
        assert calls[0].json == {
            "language": "en",
            "currency": "EUR",
            "date_format": "YYYY-MM-DD",
        }

    def test_general_form_submits(self, page: Page, authed: Page, mock_drf):
        page.goto("/profile/")
        page.locator("input[name='first_name']").fill("Grace")
        page.locator("textarea[name='bio']").fill("Compiler pioneer")
        page.get_by_role("button", name="Save General").click()

        expect(page.get_by_text("Profile updated successfully!")).to_be_visible()
        calls = mock_drf.calls("PATCH", "/profile/me/")
        assert len(calls) == 1
        assert calls[0].json["first_name"] == "Grace"
        assert calls[0].json["bio"] == "Compiler pioneer"

    def test_forged_csrf_token_is_rejected(self, page: Page, authed: Page, mock_drf):
        page.goto("/profile/")
        response = page.request.post(
            "/profile/",
            form={"csrf_token": "forged-token", "first_name": "X"},
        )
        assert "Invalid CSRF token" in response.text()
        assert mock_drf.calls("PATCH", "/profile/me/") == []

    def test_profile_form_contains_a_csrf_token(
        self, page: Page, authed: Page, mock_drf
    ):
        page.goto("/profile/")
        token = form_csrf_token(page, action_suffix="/profile/")
        assert token

    def test_backend_failure_renders_flash(self, page: Page, authed: Page, mock_drf):
        mock_drf_profile_failure(mock_drf)
        page.goto("/profile/")
        expect(page.get_by_text("Could not load profile")).to_be_visible()
