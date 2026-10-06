"""Integration tests for transaction repository."""

import httpx
import pytest
import respx

from src.budget.domain.entities import Transaction
from src.budget.domain.value_objects import TransactionId
from src.budget.infrastructure.repositories.transaction_repository import (
    DRFTransactionRepository,
)
from src.shared.http.drf_client import DRFAPIClient


@pytest.fixture
def transaction_repository():
    api_client = DRFAPIClient(base_url="http://testserver/api/v1")
    return DRFTransactionRepository(api_client)


class TestDRFTransactionRepository:
    @pytest.mark.asyncio
    @respx.mock
    async def test_list_transactions_returns_domain_entities(
        self, transaction_repository
    ):
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
                            "amount": "42.50",
                            "description": "Coffee",
                            "date": "2026-09-26",
                        }
                    ],
                },
            )
        )
        transactions = await transaction_repository.list_transactions("access-token")
        assert len(transactions) == 1
        tx = transactions[0]
        assert isinstance(tx, Transaction)
        assert tx.id == "txn-1"
        assert str(tx.amount) == "42.50"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_transaction_returns_none_on_404(self, transaction_repository):
        respx.get("http://testserver/api/v1/transactions/missing/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found"})
        )
        result = await transaction_repository.get_transaction(
            "access-token", TransactionId("missing")
        )
        assert result is None
