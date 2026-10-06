"""Dashboard repository implementation."""

from src.budget.domain.entities import DashboardOverview, DashboardSummary
from src.budget.domain.ports import DashboardRepositoryPort
from src.budget.infrastructure.mappers import (
    map_dashboard_overview_response,
    map_dashboard_summary_response,
)
from src.budget.infrastructure.schemas import (
    DRFDashboardOverviewResponse,
    DRFDashboardSummaryResponse,
)
from src.shared.http.drf_client import DRFAPIClient


class DRFDashboardRepository:
    """DRF-backed implementation of DashboardRepositoryPort."""

    def __init__(self, api_client: DRFAPIClient):
        self.api_client = api_client

    async def get_overview(self, access_token: str) -> DashboardOverview:
        data = await self.api_client.get_dashboard_overview(access_token)
        overview_data = DRFDashboardOverviewResponse.model_validate(data)
        return map_dashboard_overview_response(overview_data)

    async def get_daily_summary(self, access_token: str) -> list[DashboardSummary]:
        return await self._get_summaries(access_token, "daily")

    async def get_weekly_summary(self, access_token: str) -> list[DashboardSummary]:
        return await self._get_summaries(access_token, "weekly")

    async def get_monthly_summary(self, access_token: str) -> list[DashboardSummary]:
        return await self._get_summaries(access_token, "monthly")

    async def _get_summaries(
        self, access_token: str, period: str
    ) -> list[DashboardSummary]:
        data = await self.api_client.get_dashboard_summaries(access_token, period)
        return [
            map_dashboard_summary_response(
                DRFDashboardSummaryResponse.model_validate(item)
            )
            for item in data
        ]


# Structural check: the adapter must satisfy the port it claims to implement.
_: type[DashboardRepositoryPort] = DRFDashboardRepository
