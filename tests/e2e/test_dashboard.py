"""E2E tests for dashboard display.

The dashboard is the page most likely to face a sick backend, so the majority
of these tests are about what the user still sees when the API misbehaves.
"""

from playwright.sync_api import Page, expect

from tests.e2e.drf_stub import DEFAULT_ACCESS_TOKEN, EMPTY_OVERVIEW, StubResponse
from tests.e2e.support import (
    date_range,
    expect_path,
    mock_drf_dashboard_failure,
    mock_drf_dashboard_overview,
    mock_drf_refresh,
    net_balance,
    summary_card,
    summary_value,
)

#: The token the stub hands out when the browser asks for a refresh.
ROTATED_ACCESS = "rotated-access-token"

CARD_TITLES = ("Today", "This Week", "This Month")


class TestDashboard:
    def test_dashboard_redirects_when_not_authenticated(self, page: Page):
        page.goto("/dashboard/")
        expect_path(page, "/auth/login")

    def test_dashboard_page_loads_when_authenticated(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_dashboard_overview(mock_drf, has_data=False)
        page.goto("/dashboard/")

        expect(page).to_have_title("Dashboard - Budget Tracker")
        expect(page.locator("h1")).to_have_text("Dashboard")

    def test_dashboard_forwards_the_access_token(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_dashboard_overview(mock_drf, has_data=False)
        page.goto("/dashboard/")

        calls = mock_drf.calls("GET", "/dashboard/overview/")
        assert len(calls) == 1
        assert calls[0].bearer_token == DEFAULT_ACCESS_TOKEN

    def test_dashboard_displays_all_three_cards(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_dashboard_overview(mock_drf, has_data=True)
        page.goto("/dashboard/")

        for title in CARD_TITLES:
            expect(summary_card(page, title)).to_be_visible()

    def test_dashboard_displays_the_net_balance_of_each_period(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_dashboard_overview(mock_drf, has_data=True)
        page.goto("/dashboard/")

        # Scoped per card: these figures are what the user compares.
        expect(net_balance(summary_card(page, "Today"))).to_have_text("$37.50")
        expect(net_balance(summary_card(page, "This Week"))).to_have_text("$200.00")
        expect(net_balance(summary_card(page, "This Month"))).to_have_text("$500.00")

    def test_dashboard_displays_the_breakdown_of_each_period(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_dashboard_overview(mock_drf, has_data=True)
        page.goto("/dashboard/")

        week = summary_card(page, "This Week")
        expect(summary_value(week, "Income")).to_have_text("$500.00")
        expect(summary_value(week, "Expense")).to_have_text("$300.00")
        # The backend does not aggregate these yet, so they render as zero.
        expect(summary_value(week, "Investment")).to_have_text("$0.00")
        expect(summary_value(week, "Savings")).to_have_text("$0.00")

    def test_dashboard_displays_the_date_range_of_each_period(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_dashboard_overview(mock_drf, has_data=True)
        page.goto("/dashboard/")

        expect(date_range(summary_card(page, "Today"))).to_have_text(
            "Sep 26 - Sep 26, 2026"
        )
        expect(date_range(summary_card(page, "This Week"))).to_have_text(
            "Sep 21 - Sep 27, 2026"
        )
        expect(date_range(summary_card(page, "This Month"))).to_have_text(
            "Sep 01 - Sep 30, 2026"
        )

    def test_dashboard_links_to_the_transactions_page(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_dashboard_overview(mock_drf, has_data=True)
        page.goto("/dashboard/")

        page.get_by_role("link", name="View all transactions").click()
        expect_path(page, "/transactions/")


class TestDashboardGracefulDegradation:
    def test_cards_render_when_the_user_has_no_data(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_dashboard_overview(mock_drf, has_data=False)
        page.goto("/dashboard/")

        for title in CARD_TITLES:
            card = summary_card(page, title)
            expect(card).to_be_visible()
            # The period is unknown, so the title falls back to its label and
            # the figures fall back to zero rather than the page going blank.
            expect(net_balance(card)).to_have_text("$0.00")
            expect(date_range(card)).to_have_text("No data")

    def test_a_single_missing_period_degrades_on_its_own(
        self, page: Page, authed: Page, mock_drf
    ):
        overview = {
            "today": None,
            "this_week": {
                "period": "weekly",
                "total_income": "500.00",
                "total_expense": "300.00",
                "total_investment": "0.00",
                "total_savings": "0.00",
                "net_balance": "200.00",
                "start_date": "2026-09-21",
                "end_date": "2026-09-27",
            },
            "this_month": None,
        }
        mock_drf_dashboard_overview(mock_drf, overview=overview)
        page.goto("/dashboard/")

        expect(net_balance(summary_card(page, "Today"))).to_have_text("$0.00")
        expect(net_balance(summary_card(page, "This Week"))).to_have_text("$200.00")
        expect(net_balance(summary_card(page, "This Month"))).to_have_text("$0.00")

    def test_a_failing_backend_does_not_break_the_page(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_dashboard_failure(mock_drf, status=500)
        page.goto("/dashboard/")

        # A 500 from the API must degrade, not surface.
        expect(page.locator("h1")).to_have_text("Dashboard")
        for title in CARD_TITLES:
            expect(net_balance(summary_card(page, title))).to_have_text("$0.00")

    def test_an_unreadable_payload_does_not_break_the_page(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf.on_call(
            "GET",
            "/dashboard/overview/",
            lambda _r: StubResponse(200, {"today": {"period": "daily"}}),
        )
        page.goto("/dashboard/")

        expect(page.locator("h1")).to_have_text("Dashboard")
        for title in CARD_TITLES:
            expect(net_balance(summary_card(page, title))).to_have_text("$0.00")

    def test_the_empty_envelope_is_not_treated_as_a_failure(
        self, page: Page, authed: Page, mock_drf
    ):
        """All-null is a real answer, so it must not be confused with an error."""
        mock_drf.on_call(
            "GET",
            "/dashboard/overview/",
            lambda _r: StubResponse(200, dict(EMPTY_OVERVIEW)),
        )
        page.goto("/dashboard/")

        expect(page.get_by_text("Failed")).to_have_count(0)
        for title in CARD_TITLES:
            expect(net_balance(summary_card(page, title))).to_have_text("$0.00")

    def test_dashboard_refreshes_an_expired_token_and_retries(
        self, page: Page, expired: Page, mock_drf
    ):
        """The dashboard repository is refresh-wrapped, like the transactions one."""
        mock_drf.require_token(ROTATED_ACCESS)
        mock_drf_refresh(mock_drf, access=ROTATED_ACCESS)
        mock_drf_dashboard_overview(mock_drf, has_data=True)

        page.goto("/dashboard/")

        expect(net_balance(summary_card(page, "This Week"))).to_have_text("$200.00")
        assert mock_drf.calls("POST", "/users/auth/refresh/") != []
        retried = mock_drf.calls("GET", "/dashboard/overview/")[-1]
        assert retried.bearer_token == ROTATED_ACCESS
