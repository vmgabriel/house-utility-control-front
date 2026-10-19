"""Transaction-analytics use case."""

from dataclasses import dataclass
from datetime import date

from src.budget.domain.entities import TransactionAnalytics
from src.budget.domain.ports import AnalyticsRepositoryPort
from src.budget.domain.value_objects import AnalyticsGroupBy, AnalyticsPeriod


@dataclass
class GetTransactionAnalyticsUseCase:
    """Use case for chart-ready transaction analytics over a bounded range."""

    analytics_repository: AnalyticsRepositoryPort

    async def execute(
        self,
        access_token: str,
        start_date: date,
        end_date: date,
        group_by: AnalyticsGroupBy,
        period: AnalyticsPeriod,
    ) -> TransactionAnalytics:
        """Aggregate a bounded date range into chart-ready rows."""
        return await self.analytics_repository.get_analytics(
            access_token, start_date, end_date, group_by, period
        )
