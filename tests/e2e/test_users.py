"""E2E tests for the user management bounded context."""

from playwright.sync_api import Page, expect

from tests.e2e.drf_stub import DEFAULT_USER
from tests.e2e.support import (
    account_menu_button,
    expect_path,
    mock_drf_fail_user_list,
    mock_drf_login,
)


def _staff_user():
    return {**DEFAULT_USER, "is_staff": True}


def _seed_users():
    return (
        {
            "id": "user-1",
            "email": "ana@example.com",
            "full_name": "Ana Ruiz",
            "plan": "free",
            "is_active": True,
            "is_staff": False,
        },
        {
            "id": "user-2",
            "email": "bob@example.com",
            "full_name": "Bob Lee",
            "plan": "pro",
            "is_active": True,
            "is_staff": True,
        },
    )


def _login_as_staff(page: Page, mock_drf) -> None:
    mock_drf.backend.user = _staff_user()
    mock_drf_login(mock_drf)
    page.goto("/auth/login")
    page.get_by_label("Email").fill("admin@example.com")
    page.get_by_label("Password").fill("password123")
    page.get_by_role("button", name="Sign In").click()
    expect_path(page, "/dashboard/")


class TestRegistration:
    def test_public_registration_flow(self, page: Page, mock_drf):
        page.goto("/users/register")
        expect(page.locator("h1")).to_have_text("Create Account")

        page.locator("input[name='full_name']").fill("New User")
        page.locator("input[name='email']").fill("new@example.com")
        page.locator("input[name='password']").fill("password123")
        page.locator("input[name='confirm_password']").fill("password123")
        page.get_by_role("button", name="Create Account").click()

        expect_path(page, "/auth/login")
        expect(page.get_by_text("Account created successfully!")).to_be_visible()

        calls = mock_drf.calls("POST", "/users/auth/register/")
        assert len(calls) == 1
        assert calls[0].json["email"] == "new@example.com"

    def test_mismatched_passwords_are_rejected_locally(self, page: Page, mock_drf):
        page.goto("/users/register")
        page.locator("input[name='full_name']").fill("New User")
        page.locator("input[name='email']").fill("new@example.com")
        page.locator("input[name='password']").fill("password123")
        page.locator("input[name='confirm_password']").fill("different123")
        page.get_by_role("button", name="Create Account").click()

        expect(page.get_by_text("Passwords do not match.")).to_be_visible()
        assert mock_drf.calls("POST", "/users/auth/register/") == []


class TestAdminDashboard:
    def test_non_staff_is_redirected(self, page: Page, authed: Page, mock_drf):
        page.goto("/users/admin")
        expect_path(page, "/dashboard/")
        expect(page.get_by_text("Access denied.")).to_be_visible()

    def test_non_staff_cannot_reach_the_user_table_either(
        self, page: Page, authed: Page, mock_drf
    ):
        # The guard moved onto a new route but must cover the old one too;
        # /admin/list is a different URL and an easy one to forget.
        page.goto("/users/admin/list")
        expect_path(page, "/dashboard/")
        expect(page.get_by_text("Access denied.")).to_be_visible()

    def test_staff_sees_headline_counts(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin")

        expect(page.locator("h1")).to_have_text("Admin Dashboard")
        # Two seeded users, both active, neither premium.
        expect(page.get_by_test_id("stat-total")).to_have_text("2")
        expect(page.get_by_test_id("stat-active")).to_have_text("2")
        expect(page.get_by_test_id("stat-banned")).to_have_text("0")
        expect(page.get_by_test_id("stat-premium")).to_have_text("0")

    def test_banned_and_premium_users_are_counted_separately(
        self, page: Page, mock_drf
    ):
        banned_premium = {
            **_seed_users()[0],
            "is_active": False,
            "plan": "premium",
        }
        mock_drf.seed_users(banned_premium, _seed_users()[1])
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin")

        expect(page.get_by_test_id("stat-total")).to_have_text("2")
        expect(page.get_by_test_id("stat-active")).to_have_text("1")
        expect(page.get_by_test_id("stat-banned")).to_have_text("1")
        expect(page.get_by_test_id("stat-premium")).to_have_text("1")

    def test_counts_survive_a_failing_backend(self, page: Page, mock_drf):
        # A reachable backend that errored is not the same claim as "no users",
        # so the cards show dashes rather than zeroes.
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)
        mock_drf_fail_user_list(mock_drf)

        page.goto("/users/admin")

        expect(page.get_by_text("Could not load admin statistics.")).to_be_visible()
        expect(page.get_by_test_id("stat-total")).to_have_text("-")
        # The page still renders, so the admin can navigate onward.
        expect(page.get_by_role("link", name="Manage Users")).to_be_visible()

    def test_manage_users_button_reaches_the_table(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin")
        page.get_by_role("link", name="Manage Users").click()

        expect_path(page, "/users/admin/list")
        expect(page.locator("h1")).to_have_text("User Management")

    def test_account_menu_links_to_the_admin_panel(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)
        page.goto("/dashboard/")

        account_menu_button(page).click()
        page.get_by_role("link", name="Admin Panel").click()

        expect_path(page, "/users/admin")

    def test_admin_link_is_absent_for_non_staff(
        self, page: Page, authed: Page, mock_drf
    ):
        page.goto("/dashboard/")

        account_menu_button(page).click()

        expect(page.get_by_role("link", name="Settings")).to_be_visible()
        expect(page.get_by_role("link", name="Admin Panel")).to_have_count(0)


class TestAdminList:
    def test_staff_sees_the_user_table(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin/list")
        expect(page.locator("h1")).to_have_text("User Management")
        expect(page.get_by_text("ana@example.com")).to_be_visible()
        expect(page.get_by_text("Total Users: 2")).to_be_visible()


class TestAdminActions:
    def test_change_plan_via_dropdown(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin/list")
        row = page.locator("tr", has_text="ana@example.com")
        row.locator("select[name='plan']").select_option("premium")

        expect(page.get_by_text("User plan updated successfully.")).to_be_visible()
        calls = mock_drf.calls("PATCH", "/users/user-1/plan/")
        assert len(calls) == 1
        assert calls[0].json == {"plan": "premium"}

    def test_ban_requires_confirmation_modal(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin/list")
        row = page.locator("tr", has_text="ana@example.com")
        row.get_by_role("button", name="Ban User").click()

        modal = page.get_by_role("dialog")
        expect(modal).to_be_visible()
        expect(modal).to_contain_text("Ana Ruiz")

        modal.get_by_label("Reason for ban (optional)").fill("Test ban reason")

        # Nothing is sent until the confirmation is accepted.
        assert mock_drf.calls("PATCH", "/users/user-1/") == []

        modal.get_by_role("button", name="Yes, Ban User").click()

        expect(page.get_by_text("User banned successfully.")).to_be_visible()
        calls = mock_drf.calls("PATCH", "/users/user-1/")
        assert len(calls) == 1
        assert calls[0].json == {"is_active": False, "ban_reason": "Test ban reason"}

    def test_admin_cannot_ban_self(self, page: Page, mock_drf):
        # The signed-in admin is user-123 (DEFAULT_USER), seeded as their own row.
        mock_drf.seed_users(_staff_user(), *_seed_users())
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin/list")
        row = page.locator("tr", has_text="test@example.com")
        button = row.get_by_role("button", name="Ban User")

        expect(button).to_be_disabled()
        expect(button).to_have_attribute("title", "You cannot ban yourself")

    def test_banned_user_is_visually_distinct_and_can_be_unbanned(
        self, page: Page, mock_drf
    ):
        banned_user = {**_seed_users()[0], "is_active": False}
        mock_drf.seed_users(banned_user, _seed_users()[1])
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin/list")
        row = page.locator("tr", has_text="ana@example.com")
        expect(row.get_by_text("Banned")).to_be_visible()
        expect(row.get_by_text("Active", exact=True)).to_have_count(0)

        row.get_by_role("button", name="Unban User").click()

        expect(page.get_by_text("User activated successfully.")).to_be_visible()
        calls = mock_drf.calls("PATCH", "/users/user-1/")
        assert len(calls) == 1
        assert calls[0].json == {"is_active": True}

    def test_forged_csrf_token_is_rejected(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        response = page.request.post(
            "/users/admin/user-1/plan",
            form={"csrf_token": "forged", "plan": "premium"},
        )
        assert "Invalid CSRF token" in response.text()
        assert mock_drf.calls("PATCH", "/users/user-1/plan/") == []
