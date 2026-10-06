"""Integration tests for dashboard repository."""

from datetime import date

import httpx
import pytest
import respx

from src.budget.domain.entities import DashboardOverview, DashboardSummary
from src.budget.infrastructure.repositories.dashboard_repository import (
    DRFDashboardRepository,
)
from src.shared.http.drf_client import DRFAPIClient

SUMMARY_JSON = {
    "id": "5a0b1c22-0000-4000-8000-000000000001",
    "period": "daily",
    "date": "2026-09-26",
    "total_income": "100.00",
    "total_expense": "50.00",
    "net_balance": "35.00",
    "generated_at": "2026-09-26T10:00:00Z",
    "is_stale": False,
    "stale_at": None,
    "status": "fresh",
}

#: The real GET /dashboard/{period}/ envelope (DashboardListSerializer).
LIST_ENVELOPE_JSON = {
    "period": "daily",
    "start_date": "2026-09-01",
    "end_date": "2026-09-30",
    "summary_count": 2,
    "is_empty": False,
    "has_stale_data": False,
    "summaries": [SUMMARY_JSON, SUMMARY_JSON],
}


@pytest.fixture
def dashboard_repository():
    api_client = DRFAPIClient(base_url="http://testserver/api/v1")
    return DRFDashboardRepository(api_client)


class TestDRFDashboardRepository:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_overview_maps_null_slots_to_none(self, dashboard_repository):
        respx.get("http://testserver/api/v1/dashboard/overview/").mock(
            return_value=httpx.Response(
                200,
                json={"today": None, "this_week": SUMMARY_JSON, "this_month": None},
            )
        )
        overview = await dashboard_repository.get_overview("access-token")

        assert isinstance(overview, DashboardOverview)
        assert overview.today is None
        assert overview.this_month is None
        assert isinstance(overview.this_week, DashboardSummary)
        assert overview.this_week.net_balance.amount == 35
        assert str(overview.this_week.total_income) == "100.00"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_overview_preserves_all_slots(self, dashboard_repository):
        respx.get("http://testserver/api/v1/dashboard/overview/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "today": {**SUMMARY_JSON, "period": "daily"},
                    "this_week": {**SUMMARY_JSON, "period": "weekly"},
                    "this_month": {**SUMMARY_JSON, "period": "monthly"},
                },
            )
        )
        overview = await dashboard_repository.get_overview("access-token")
        assert [overview.today.period, overview.this_week.period] == [
            "daily",
            "weekly",
        ]
        assert overview.this_month.period == "monthly"

    @pytest.mark.asyncio
    @respx.mock
    @pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
    async def test_period_summaries_hit_expected_endpoint(
        self, dashboard_repository, period
    ):
        route = respx.get(f"http://testserver/api/v1/dashboard/{period}/").mock(
            return_value=httpx.Response(200, json=LIST_ENVELOPE_JSON)
        )
        summaries = await getattr(dashboard_repository, f"get_{period}_summary")(
            "access-token"
        )

        # The dict envelope must be unwrapped into the two summaries it carries.
        assert len(summaries) == 2
        assert all(isinstance(s, DashboardSummary) for s in summaries)
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_period_summaries_accept_a_bare_list_too(self, dashboard_repository):
        respx.get("http://testserver/api/v1/dashboard/daily/").mock(
            return_value=httpx.Response(200, json=[SUMMARY_JSON])
        )
        assert len(await dashboard_repository.get_daily_summary("access-token")) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_envelope_yields_no_summaries(self, dashboard_repository):
        respx.get("http://testserver/api/v1/dashboard/daily/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "period": "daily",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-30",
                    "summary_count": 0,
                    "is_empty": True,
                    "has_stale_data": False,
                    "summaries": [],
                },
            )
        )
        assert await dashboard_repository.get_daily_summary("access-token") == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_summary_without_investment_totals_degrades_to_zero(
        self, dashboard_repository
    ):
        respx.get("http://testserver/api/v1/dashboard/weekly/").mock(
            return_value=httpx.Response(200, json=LIST_ENVELOPE_JSON)
        )
        summary = (await dashboard_repository.get_weekly_summary("access-token"))[0]
        assert str(summary.total_investment) == "0.00"
        assert str(summary.total_savings) == "0.00"
        assert summary.start_date == date(2026, 9, 26)

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_summary_list(self, dashboard_repository):
        respx.get("http://testserver/api/v1/dashboard/daily/").mock(
            return_value=httpx.Response(200, json=[])
        )
        assert await dashboard_repository.get_daily_summary("access-token") == []
