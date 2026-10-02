"""E2E tests for the user management bounded context."""

from playwright.sync_api import Page, expect

from tests.e2e.drf_stub import DEFAULT_USER
from tests.e2e.support import expect_path, mock_drf_login


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


class TestAdminAccess:
    def test_non_staff_is_redirected(self, page: Page, authed: Page, mock_drf):
        page.goto("/users/admin")
        expect_path(page, "/dashboard/")
        expect(page.get_by_text("Access denied.")).to_be_visible()

    def test_staff_sees_the_user_table(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin")
        expect(page.locator("h1")).to_have_text("User Management")
        expect(page.get_by_text("ana@example.com")).to_be_visible()
        expect(page.get_by_text("Total Users: 2")).to_be_visible()


class TestAdminActions:
    def test_change_plan_via_dropdown(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin")
        row = page.locator("tr", has_text="ana@example.com")
        row.locator("select[name='plan']").select_option("premium")

        expect(page.get_by_text("User plan updated successfully.")).to_be_visible()
        calls = mock_drf.calls("PATCH", "/users/user-1/plan/")
        assert len(calls) == 1
        assert calls[0].json == {"plan": "premium"}

    def test_toggle_active_status(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        page.goto("/users/admin")
        row = page.locator("tr", has_text="ana@example.com")
        row.get_by_role("button", name="Active").click()

        expect(page.get_by_text("User deactivated successfully.")).to_be_visible()
        calls = mock_drf.calls("PATCH", "/users/user-1/")
        assert len(calls) == 1
        assert calls[0].json == {"is_active": False}

    def test_forged_csrf_token_is_rejected(self, page: Page, mock_drf):
        mock_drf.seed_users(*_seed_users())
        _login_as_staff(page, mock_drf)

        response = page.request.post(
            "/users/admin/user-1/plan",
            form={"csrf_token": "forged", "plan": "premium"},
        )
        assert "Invalid CSRF token" in response.text()
        assert mock_drf.calls("PATCH", "/users/user-1/plan/") == []
