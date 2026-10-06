"""Integration tests for DRF API client using respx to mock HTTP."""

import httpx
import pytest
import respx

from src.shared.domain.exceptions import AuthenticationError
from src.shared.http.drf_client import (
    DRFAPIClient,
    NotFoundError,
    UnauthorizedError,
)


@pytest.fixture
def api_client():
    return DRFAPIClient(base_url="http://testserver/api/v1")


class TestDRFAPIClientLogin:
    @pytest.mark.asyncio
    @respx.mock
    async def test_login_success(self, api_client):
        respx.post("http://testserver/api/v1/users/auth/login/").mock(
            return_value=httpx.Response(
                200, json={"access": "acc-123", "refresh": "ref-456"}
            )
        )
        result = await api_client.login("user@example.com", "password")
        assert result == {"access": "acc-123", "refresh": "ref-456"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_login_invalid_credentials(self, api_client):
        respx.post("http://testserver/api/v1/users/auth/login/").mock(
            return_value=httpx.Response(401, json={"detail": "Invalid credentials"})
        )
        with pytest.raises(AuthenticationError):
            await api_client.login("user@example.com", "wrong")


class TestDRFAPIClientTransactions:
    @pytest.mark.asyncio
    @respx.mock
    async def test_list_transactions_success(self, api_client):
        respx.get("http://testserver/api/v1/transactions/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": "txn-1",
                            "type": "expense",
                            "amount": "50.00",
                            "description": "Groceries",
                            "date": "2026-09-26",
                            "created_at": "2026-09-26T10:00:00Z",
                            "updated_at": "2026-09-26T10:00:00Z",
                        }
                    ],
                },
            )
        )
        result = await api_client.list_transactions("access-token")
        assert result["count"] == 1
        assert result["results"][0]["amount"] == "50.00"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_transactions_unauthorized(self, api_client):
        respx.get("http://testserver/api/v1/transactions/").mock(
            return_value=httpx.Response(401, json={"detail": "Unauthorized"})
        )
        with pytest.raises(UnauthorizedError):
            await api_client.list_transactions("bad-token")

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_transaction_not_found(self, api_client):
        respx.get("http://testserver/api/v1/transactions/missing/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found"})
        )
        with pytest.raises(NotFoundError):
            await api_client.get_transaction("access-token", "missing")

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_transaction_sends_bearer_header(self, api_client):
        route = respx.post("http://testserver/api/v1/transactions/").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "txn-new",
                    "type": "income",
                    "amount": "100.00",
                    "description": "Salary",
                    "date": "2026-09-26",
                },
            )
        )
        await api_client.create_transaction(
            "my-access-token",
            {
                "type": "income",
                "amount": "100.00",
                "description": "Salary",
                "date": "2026-09-26",
            },
        )
        assert (
            route.calls[0].request.headers["Authorization"] == "Bearer my-access-token"
        )


class TestDRFAPIClientDashboard:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_dashboard_overview_with_null_slots(self, api_client):
        respx.get("http://testserver/api/v1/dashboard/overview/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "today": None,
                    "this_week": {
                        "period": "weekly",
                        "total_income": "100.00",
                        "total_expense": "50.00",
                        "total_investment": "10.00",
                        "total_savings": "5.00",
                        "net_balance": "35.00",
                        "start_date": "2026-09-21",
                        "end_date": "2026-09-27",
                    },
                    "this_month": None,
                },
            )
        )
        result = await api_client.get_dashboard_overview("access-token")
        assert result["today"] is None
        assert result["this_week"]["net_balance"] == "35.00"
