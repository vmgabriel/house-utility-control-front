"""Integration tests for the transaction-analytics repository."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from src.budget.domain.value_objects import AnalyticsGroupBy, AnalyticsPeriod
from src.budget.infrastructure.repositories.analytics_repository import (
    DRFAnalyticsRepository,
)
from src.shared.http.drf_client import DRFAPIClient, DRFAPIClientError

ANALYTICS_URL = "http://testserver/api/v1/transactions/analytics/"

TYPE_RESPONSE = {
    "period": "monthly",
    "group_by": "type",
    "data": [
        {
            "label": "2026-08",
            "income": "1000.00",
            "expense": "400.00",
            "investment": "50.00",
            "savings": "100.00",
        },
        {
            "label": "2026-09",
            "income": "2000.00",
            "expense": "600.00",
            "investment": "0.00",
            "savings": "250.00",
        },
    ],
}


@pytest.fixture
def analytics_repository():
    api_client = DRFAPIClient(base_url="http://testserver/api/v1")
    return DRFAnalyticsRepository(api_client)


class TestGetAnalytics:
    @pytest.mark.asyncio
    @respx.mock
    async def test_forwards_the_bounded_query(self, analytics_repository):
        route = respx.get(ANALYTICS_URL).mock(
            return_value=httpx.Response(200, json=TYPE_RESPONSE)
        )

        result = await analytics_repository.get_analytics(
            "access-token",
            date(2025, 10, 1),
            date(2026, 9, 30),
            AnalyticsGroupBy.TYPE,
            AnalyticsPeriod.MONTHLY,
        )

        params = route.calls[0].request.url.params
        assert params["start_date"] == "2025-10-01"
        assert params["end_date"] == "2026-09-30"
        assert params["group_by"] == "type"
        assert params["period"] == "monthly"
        assert route.calls[0].request.headers["Authorization"] == "Bearer access-token"

        assert result.period is AnalyticsPeriod.MONTHLY
        assert result.group_by is AnalyticsGroupBy.TYPE
        assert [row.label for row in result.rows] == ["2026-08", "2026-09"]
        assert result.rows[1].income == Decimal("2000.00")

    @pytest.mark.asyncio
    @respx.mock
    async def test_category_rows_come_back_sorted(self, analytics_repository):
        respx.get(ANALYTICS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "period": "monthly",
                    "group_by": "category",
                    "data": [
                        {"label": "Food", "total": "10.00"},
                        {"label": "Housing", "total": "30.00"},
                        {"label": "Transport", "total": "20.00"},
                    ],
                },
            )
        )

        result = await analytics_repository.get_analytics(
            "access-token",
            date(2026, 1, 1),
            date(2026, 9, 30),
            AnalyticsGroupBy.CATEGORY,
            AnalyticsPeriod.MONTHLY,
        )

        assert result.group_by is AnalyticsGroupBy.CATEGORY
        assert [row.label for row in result.rows] == [
            "Housing",
            "Transport",
            "Food",
        ]
        assert result.rows[0].total == Decimal("30.00")

    @pytest.mark.asyncio
    @respx.mock
    async def test_an_empty_payload_yields_no_rows(self, analytics_repository):
        respx.get(ANALYTICS_URL).mock(
            return_value=httpx.Response(
                200,
                json={"period": "daily", "group_by": "type", "data": []},
            )
        )

        result = await analytics_repository.get_analytics(
            "access-token",
            date(2026, 9, 1),
            date(2026, 9, 30),
            AnalyticsGroupBy.TYPE,
            AnalyticsPeriod.DAILY,
        )

        assert result.rows == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_backend_error_raises(self, analytics_repository):
        respx.get(ANALYTICS_URL).mock(
            return_value=httpx.Response(500, json={"detail": "boom"})
        )

        with pytest.raises(DRFAPIClientError):
            await analytics_repository.get_analytics(
                "access-token",
                date(2026, 9, 1),
                date(2026, 9, 30),
                AnalyticsGroupBy.TYPE,
                AnalyticsPeriod.DAILY,
            )
