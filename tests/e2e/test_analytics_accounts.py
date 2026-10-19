"""E2E tests for the analytics and funding-account pages.

Chart.js and Alpine are served from ``tests/e2e/vendor`` (see ``offline_assets``)
so these assert against a real chart instance. A stubbed-out ``window.Chart``
would let this suite pass on a page whose chart code never ran.
"""

import time

from playwright.sync_api import Page, expect

from tests.e2e.drf_stub import StubDRFServer, StubResponse
from tests.e2e.support import (
    expect_path,
    mock_drf_analytics,
    mock_drf_funding_accounts,
    mock_drf_transactions_list,
)

ANALYTICS_PATH = "/transactions/analytics/"


def chart_snapshot(page: Page, canvas_id: str) -> dict | None:
    """The live Chart.js instance attached to ``canvas_id``, or ``None``."""
    return page.evaluate(
        """(id) => {
            const canvas = document.getElementById(id);
            if (!window.Chart || !canvas) return null;
            const chart = Chart.getChart(canvas);
            if (!chart) return null;
            return {
                type: chart.config.type,
                labels: chart.data.labels,
                datasetLabels: chart.data.datasets.map((set) => set.label),
                firstData: chart.data.datasets.map((set) => set.data[0]),
            };
        }""",
        canvas_id,
    )


def wait_for_chart(page: Page, canvas_id: str, timeout_ms: int = 10_000) -> None:
    page.wait_for_function(
        """(id) => window.Chart &&
                  Chart.getChart(document.getElementById(id)) !== undefined""",
        arg=canvas_id,
        timeout=timeout_ms,
    )


def wait_for_analytics_calls(
    stub: StubDRFServer, count: int, timeout_ms: int = 10_000
) -> list:
    """Poll the stub until it has recorded ``count`` analytics requests.

    The chart fetch is issued by Alpine, so there is no DOM event to await for
    the *request* itself. The visible chart state is a separate assertion; this
    only waits for the server side of the round trip.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        calls = stub.calls("GET", ANALYTICS_PATH)
        if len(calls) >= count:
            return calls
        time.sleep(0.05)
    raise AssertionError(
        f"expected at least {count} analytics calls, "
        f"saw {len(stub.calls('GET', ANALYTICS_PATH))}"
    )


class TestAnalyticsPage:
    def test_redirects_when_not_authenticated(self, page: Page, mock_drf):
        page.goto("/budget/analytics/")
        expect_path(page, "/auth/login")

    def test_renders_live_bar_and_pie_charts(self, page: Page, authed: Page, mock_drf):
        page.goto("/budget/analytics/")
        wait_for_chart(page, "barChart")
        wait_for_chart(page, "pieChart")

        bar = chart_snapshot(page, "barChart")
        assert bar is not None
        assert bar["type"] == "bar"
        # Monthly bucket labels are formatted for humans on the axis.
        assert bar["labels"] == ["Aug 2026", "Sep 2026"]
        assert bar["datasetLabels"] == ["Income", "Expense", "Investment", "Savings"]
        # First bucket: income 1000, expense 400, investment 50, savings 100.
        assert bar["firstData"] == [1000.0, 400.0, 50.0, 100.0]

        pie = chart_snapshot(page, "pieChart")
        assert pie is not None
        assert pie["type"] == "pie"
        # The adapter sorts categories by descending total before rendering.
        assert pie["labels"] == ["Housing", "Food", "Transport"]
        assert pie["datasetLabels"] == ["Expenses by category"]

        # The type panel is the default; the category panel stays hidden until
        # its toggle is selected.
        expect(page.get_by_test_id("bar-chart")).to_be_visible()
        expect(page.get_by_test_id("pie-chart")).to_be_hidden()

    def test_switching_grouping_shows_the_pie_panel(
        self, page: Page, authed: Page, mock_drf
    ):
        page.goto("/budget/analytics/")
        wait_for_chart(page, "barChart")

        page.get_by_test_id("group-category").click()

        expect(page.get_by_test_id("pie-chart")).to_be_visible()
        expect(page.get_by_test_id("bar-chart")).to_be_hidden()

    def test_switching_period_refetches_both_series(
        self, page: Page, authed: Page, mock_drf
    ):
        # A day-shaped label only parses as daily, so the redraw is observable:
        # monthly passes it through unchanged, daily formats it.
        mock_drf_analytics(
            mock_drf,
            "type",
            [
                {
                    "label": "2026-09-26",
                    "income": "1.00",
                    "expense": "2.00",
                    "investment": "0.00",
                    "savings": "0.00",
                }
            ],
        )
        mock_drf_analytics(mock_drf, "category", [{"label": "Food", "total": "2.00"}])

        page.goto("/budget/analytics/")
        wait_for_analytics_calls(mock_drf, 2)
        page.wait_for_function(
            """() => Chart.getChart(document.getElementById('barChart'))
                     ?.data.labels[0] === '2026-09-26'"""
        )

        page.get_by_test_id("period-daily").click()
        wait_for_analytics_calls(mock_drf, 4)
        page.wait_for_function(
            """() => Chart.getChart(document.getElementById('barChart'))
                     ?.data.labels[0] === 'Sep 26'"""
        )

        calls = mock_drf.calls("GET", ANALYTICS_PATH)
        # One fetch per grouping, both for the newly selected period.
        assert [call.query["period"] for call in calls[-2:]] == ["daily", "daily"]
        assert {call.query["group_by"] for call in calls[-2:]} == {"type", "category"}

    def test_reports_a_backend_failure(self, page: Page, authed: Page, mock_drf):
        mock_drf.on(
            "GET", ANALYTICS_PATH, StubResponse(500, {"detail": "Analytics down."})
        )

        page.goto("/budget/analytics/")

        error = page.get_by_test_id("analytics-error")
        expect(error).to_be_visible()
        expect(error).to_have_text("Could not load analytics. Please try again.")


class TestFundingAccountsPage:
    def test_redirects_when_not_authenticated(self, page: Page, mock_drf):
        page.goto("/budget/accounts/")
        expect_path(page, "/auth/login")

    def test_lists_the_accounts_the_backend_has(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_funding_accounts(
            mock_drf,
            [
                {
                    "id": "acc-1",
                    "name": "Visa Rewards",
                    "account_type": "credit_card",
                    "is_active": True,
                },
                {
                    "id": "acc-2",
                    "name": "Wallet",
                    "account_type": "cash",
                    "is_active": True,
                },
            ],
        )

        page.goto("/budget/accounts/")

        rows = page.get_by_test_id("funding-account")
        expect(rows).to_have_count(2)
        expect(rows.filter(has_text="Visa Rewards")).to_contain_text("Credit card")
        expect(rows.filter(has_text="Wallet")).to_contain_text("Cash")

    def test_shows_the_empty_state(self, page: Page, authed: Page, mock_drf):
        mock_drf_funding_accounts(mock_drf, [])

        page.goto("/budget/accounts/")

        expect(page.get_by_text("No funding accounts yet")).to_be_visible()

    def test_creating_an_account_updates_the_list(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_funding_accounts(mock_drf, [])
        page.goto("/budget/accounts/")
        expect(page.get_by_text("No funding accounts yet")).to_be_visible()

        page.get_by_label("Name").fill("Visa Rewards")
        page.get_by_label("Type").select_option("credit_card")
        page.get_by_role("button", name="Create account").click()

        expect_path(page, "/budget/accounts/")
        expect(
            page.get_by_text("Funding account created successfully!")
        ).to_be_visible()

        row = page.get_by_test_id("funding-account")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("Visa Rewards")
        expect(row).to_contain_text("Credit card")

        calls = mock_drf.calls("POST", "/transactions/accounts/")
        assert len(calls) == 1
        # Only the writable fields: the id and is_active are server-owned.
        assert calls[0].json == {"name": "Visa Rewards", "account_type": "credit_card"}

    def test_a_deactivated_account_is_not_listed(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_funding_accounts(
            mock_drf,
            [
                {
                    "id": "acc-1",
                    "name": "Everyday",
                    "account_type": "debit",
                    "is_active": True,
                },
                {
                    "id": "acc-2",
                    "name": "Dormant",
                    "account_type": "cash",
                    "is_active": False,
                },
            ],
        )

        page.goto("/budget/accounts/")

        expect(page.get_by_test_id("funding-account")).to_have_count(1)
        expect(page.get_by_text("Dormant")).to_have_count(0)


class TestTransactionAccountSelector:
    def test_the_create_modal_offers_active_accounts(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_funding_accounts(
            mock_drf,
            [
                {
                    "id": "acc-1",
                    "name": "Visa Rewards",
                    "account_type": "credit_card",
                    "is_active": True,
                }
            ],
        )

        page.goto("/transactions/")
        page.get_by_role("button", name="New Transaction").click()

        selector = page.get_by_label("Funding account")
        expect(selector).to_be_visible()
        options = selector.locator("option").evaluate_all(
            "options => options.map((option) => option.textContent.trim())"
        )
        # The blank option is how "not linked" is expressed.
        assert options[0] == "No funding account"
        assert "Visa Rewards (Credit card)" in options

    def test_selecting_an_account_links_the_transaction(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_drf_transactions_list(mock_drf, [])
        mock_drf_funding_accounts(
            mock_drf,
            [
                {
                    "id": "acc-1",
                    "name": "Visa Rewards",
                    "account_type": "credit_card",
                    "is_active": True,
                }
            ],
        )

        page.goto("/transactions/")
        page.get_by_role("button", name="New Transaction").click()

        page.get_by_label("Type").select_option("expense")
        page.get_by_label("Amount").fill("42.50")
        page.get_by_label("Description").fill("Coffee run")
        page.get_by_label("Category").fill("Food")
        page.get_by_label("Funding account").select_option("acc-1")
        page.get_by_role("button", name="Create", exact=True).click()

        expect_path(page, "/transactions/")
        calls = mock_drf.calls("POST", "/transactions/")
        assert len(calls) == 1
        assert calls[0].json["account_id"] == "acc-1"
