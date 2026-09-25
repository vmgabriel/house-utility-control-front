"""Dashboard use cases."""

from dataclasses import dataclass

from ...domain.entities import DashboardOverview
from ...domain.ports import DashboardRepositoryPort


@dataclass
class GetDashboardOverviewUseCase:
    """Use case for getting dashboard overview."""

    dashboard_repository: DashboardRepositoryPort

    async def execute(self, access_token: str) -> DashboardOverview:
        """Get dashboard overview."""
        return await self.dashboard_repository.get_overview(access_token)


@dataclass
class GetDashboardSummariesUseCase:
    """Use case for getting dashboard summaries by period."""

    dashboard_repository: DashboardRepositoryPort

    async def execute_daily(self, access_token: str) -> list:
        """Get daily summaries."""
        return await self.dashboard_repository.get_daily_summary(access_token)

    async def execute_weekly(self, access_token: str) -> list:
        """Get weekly summaries."""
        return await self.dashboard_repository.get_weekly_summary(access_token)

    async def execute_monthly(self, access_token: str) -> list:
        """Get monthly summaries."""
        return await self.dashboard_repository.get_monthly_summary(access_token)
