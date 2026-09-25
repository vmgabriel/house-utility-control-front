"""Integration tests for dashboard repository."""

import httpx
import pytest
import respx

from src.domain.entities import DashboardOverview, DashboardSummary
from src.infrastructure.api.drf_client import DRFAPIClient
from src.infrastructure.repositories.dashboard_repository import (
    DRFDashboardRepository,
)

SUMMARY_JSON = {
    "period": "daily",
    "total_income": "100.00",
    "total_expense": "50.00",
    "total_investment": "10.00",
    "total_savings": "5.00",
    "net_balance": "35.00",
    "start_date": "2026-09-26",
    "end_date": "2026-09-26",
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
            return_value=httpx.Response(200, json=[SUMMARY_JSON, SUMMARY_JSON])
        )
        summaries = await getattr(dashboard_repository, f"get_{period}_summary")(
            "access-token"
        )

        assert len(summaries) == 2
        assert all(isinstance(s, DashboardSummary) for s in summaries)
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_summary_list(self, dashboard_repository):
        respx.get("http://testserver/api/v1/dashboard/daily/").mock(
            return_value=httpx.Response(200, json=[])
        )
        assert await dashboard_repository.get_daily_summary("access-token") == []
