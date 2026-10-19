"""Transaction-analytics repository implementation."""

from datetime import date

from src.budget.domain.entities import TransactionAnalytics
from src.budget.domain.ports import AnalyticsRepositoryPort
from src.budget.domain.value_objects import AnalyticsGroupBy, AnalyticsPeriod
from src.budget.infrastructure.mappers import map_analytics_response
from src.budget.infrastructure.schemas import DRFAnalyticsResponse
from src.shared.http.drf_client import DRFAPIClient


class DRFAnalyticsRepository:
    """DRF-backed implementation of AnalyticsRepositoryPort."""

    def __init__(self, api_client: DRFAPIClient):
        self.api_client = api_client

    async def get_analytics(
        self,
        access_token: str,
        start_date: date,
        end_date: date,
        group_by: AnalyticsGroupBy,
        period: AnalyticsPeriod,
    ) -> TransactionAnalytics:
        data = await self.api_client.get_transaction_analytics(
            access_token,
            start_date.isoformat(),
            end_date.isoformat(),
            group_by.value,
            period.value,
        )
        return map_analytics_response(DRFAnalyticsResponse.model_validate(data))


# Structural check: the adapter must satisfy the port it claims to implement.
_: type[AnalyticsRepositoryPort] = DRFAnalyticsRepository
