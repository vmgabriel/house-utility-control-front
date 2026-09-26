"""Infrastructure smoke tests for the E2E harness itself.

If these fail, every other E2E failure is a red herring: they prove the live
Flask server, the fake DRF backend, the offline asset routes and the auth
cookies are all wired up.
"""

from playwright.sync_api import Page, expect

from tests.e2e.drf_stub import DEFAULT_ACCESS_TOKEN
from tests.e2e.support import (
    expect_path,
    mock_drf_transactions_list,
    summary_card,
    transaction_row,
)


class TestHarness:
    def test_live_server_serves_login_page(self, page: Page):
        page.goto("/auth/login")
        expect(page.locator("h2")).to_have_text("Sign In")

    def test_flask_reaches_the_drf_stub(self, page: Page, authed: Page):
        page.goto("/transactions/")
        expect_path(page, "/transactions/")

    def test_stub_records_the_bearer_token(self, page: Page, authed: Page, mock_drf):
        mock_drf_transactions_list(
            mock_drf,
            transactions=[
                {
                    "id": "tx-1",
                    "description": "Groceries",
                    "category": "Food",
                }
            ],
        )
        page.goto("/transactions/")

        calls = mock_drf.calls("GET", "/transactions/")
        assert len(calls) == 1
        assert calls[0].bearer_token == DEFAULT_ACCESS_TOKEN
        expect(transaction_row(page, "Groceries")).to_be_visible()

    def test_alpine_is_served_locally(self, page: Page, authed: Page):
        page.goto("/dashboard/")
        # The dashboard cards only exist once Alpine has evaluated x-if.
        expect(summary_card(page, "Today")).to_be_visible()

    def test_unauthenticated_visit_is_redirected(self, page: Page):
        page.goto("/dashboard/")
        expect_path(page, "/auth/login")
