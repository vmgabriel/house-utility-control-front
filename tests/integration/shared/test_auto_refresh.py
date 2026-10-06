"""Integration tests for the automatic 401 token refresh proxy."""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from src.budget.domain.value_objects import TransactionId
from src.budget.infrastructure.repositories.transaction_repository import (
    DRFTransactionRepository,
)
from src.identity.infrastructure.repository import DRFAuthRepository
from src.shared.http.auto_refresh import AutoRefreshingRepository
from src.shared.http.drf_client import (
    DRFAPIClient,
    UnauthorizedError,
    ValidationError,
)

PAGINATED = {
    "count": 1,
    "next": None,
    "previous": None,
    "results": [
        {
            "id": "txn-1",
            "type": "expense",
            "amount": "10.00",
            "description": "Coffee",
            "date": "2026-09-26",
        }
    ],
}


def build_repository(refresh=None, on_refreshed=None):
    inner = DRFTransactionRepository(DRFAPIClient(base_url="http://testserver/api/v1"))
    return AutoRefreshingRepository(
        inner=inner,
        refresh=refresh or AsyncMock(return_value=("new-access", "new-refresh")),
        on_refreshed=on_refreshed,
    )


class TestAutoRefresh:
    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_once_with_new_token_after_401(self):
        route = respx.get("http://testserver/api/v1/transactions/").mock(
            side_effect=[
                httpx.Response(401, json={"detail": "Token expired"}),
                httpx.Response(200, json=PAGINATED),
            ]
        )
        refresh = AsyncMock(return_value=("new-access", "new-refresh"))
        on_refreshed = AsyncMock()
        repo = build_repository(refresh=refresh, on_refreshed=on_refreshed)

        transactions = await repo.list_transactions("stale-access")

        assert len(transactions) == 1
        assert route.call_count == 2
        # First call used the stale token, the retry used the refreshed one.
        assert route.calls[0].request.headers["Authorization"] == "Bearer stale-access"
        assert route.calls[1].request.headers["Authorization"] == "Bearer new-access"
        refresh.assert_awaited_once_with()
        on_refreshed.assert_awaited_once_with("new-access", "new-refresh")
        assert repo.refresh_count == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_refresh_when_first_call_succeeds(self):
        respx.get("http://testserver/api/v1/transactions/").mock(
            return_value=httpx.Response(200, json=PAGINATED)
        )
        refresh = AsyncMock(return_value=("new-access", "new-refresh"))
        on_refreshed = AsyncMock()
        repo = build_repository(refresh=refresh, on_refreshed=on_refreshed)

        await repo.list_transactions("good-access")

        refresh.assert_not_awaited()
        on_refreshed.assert_not_awaited()
        assert repo.refresh_count == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_second_401_propagates_instead_of_looping(self):
        respx.get("http://testserver/api/v1/transactions/").mock(
            return_value=httpx.Response(401, json={"detail": "Token expired"})
        )
        refresh = AsyncMock(return_value=("new-access", "new-refresh"))
        repo = build_repository(refresh=refresh)

        with pytest.raises(UnauthorizedError):
            await repo.list_transactions("stale-access")

        # Exactly one refresh attempt, no infinite retry loop.
        assert refresh.await_count == 1
        assert repo.refresh_count == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_404_is_not_retried(self):
        respx.get("http://testserver/api/v1/transactions/missing/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found"})
        )
        refresh = AsyncMock(return_value=("new-access", "new-refresh"))
        repo = build_repository(refresh=refresh)

        assert await repo.get_transaction("access", TransactionId("missing")) is None
        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    @respx.mock
    async def test_sync_callback_is_supported(self):
        respx.get("http://testserver/api/v1/transactions/").mock(
            side_effect=[
                httpx.Response(401, json={"detail": "Token expired"}),
                httpx.Response(200, json=PAGINATED),
            ]
        )
        seen: list[tuple[str, str]] = []
        repo = build_repository(on_refreshed=lambda a, r: seen.append((a, r)))

        await repo.list_transactions("stale-access")

        assert seen == [("new-access", "new-refresh")]

    @pytest.mark.asyncio
    @respx.mock
    async def test_validation_error_is_not_retried(self):
        respx.post("http://testserver/api/v1/transactions/").mock(
            return_value=httpx.Response(400, json={"amount": ["Invalid amount"]})
        )
        refresh = AsyncMock(return_value=("new-access", "new-refresh"))
        repo = build_repository(refresh=refresh)

        with pytest.raises(ValidationError):
            await repo.create_transaction("access", _pending_transaction())

        refresh.assert_not_awaited()

    def test_refresh_token_method_is_never_wrapped(self):
        # Wrapping the refresh call itself would recurse forever on a dead
        # refresh token, so the proxy must hand it through untouched.
        inner = DRFAuthRepository(DRFAPIClient(base_url="http://testserver/api/v1"))
        repo = AutoRefreshingRepository(inner=inner, refresh=AsyncMock())
        assert repo.refresh_token == inner.refresh_token
        assert repo.refresh_count == 0

    def test_unknown_attribute_raises_attribute_error(self):
        inner = DRFTransactionRepository(
            DRFAPIClient(base_url="http://testserver/api/v1")
        )
        repo = AutoRefreshingRepository(inner=inner, refresh=AsyncMock())
        with pytest.raises(AttributeError):
            _ = repo.does_not_exist

    def test_non_coroutine_attributes_pass_through(self):
        inner = DRFTransactionRepository(
            DRFAPIClient(base_url="http://testserver/api/v1")
        )
        repo = AutoRefreshingRepository(inner=inner, refresh=AsyncMock())
        assert repo.api_client is inner.api_client


def _pending_transaction():
    from datetime import date
    from decimal import Decimal

    from src.budget.domain.entities import Transaction
    from src.budget.domain.value_objects import Money, TransactionType

    return Transaction(
        id=None,
        user_id="u1",
        type=TransactionType.EXPENSE,
        amount=Money(Decimal("10.00")),
        description="Coffee",
        date=date(2026, 9, 26),
    )
