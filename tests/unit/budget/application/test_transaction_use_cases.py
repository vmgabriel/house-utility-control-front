"""Tests for transaction use cases."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.budget.application.use_cases.transactions import (
    CreateTransactionUseCase,
    FetchTransactionsUseCase,
)
from src.budget.domain.entities import Transaction
from src.budget.domain.value_objects import (
    Money,
    TransactionId,
    TransactionType,
    UserId,
)


class TestCreateTransactionUseCase:
    @pytest.mark.asyncio
    async def test_create_transaction_success(self):
        mock_transaction_repo = AsyncMock()
        transaction = Transaction(
            id=None,
            user_id=UserId("user-123"),
            type=TransactionType.EXPENSE,
            amount=Money(Decimal("50.00")),
            description="Groceries",
            date=date.today(),
        )
        mock_transaction_repo.create_transaction.return_value = Transaction(
            id=TransactionId("txn-123"),
            user_id=UserId("user-123"),
            type=TransactionType.EXPENSE,
            amount=Money(Decimal("50.00")),
            description="Groceries",
            date=date.today(),
        )

        use_case = CreateTransactionUseCase(
            transaction_repository=mock_transaction_repo,
            current_date=date.today(),
        )
        result = await use_case.execute("access-token", transaction)

        assert result.id == "txn-123"
        mock_transaction_repo.create_transaction.assert_called_once()


class TestFetchTransactionsUseCase:
    @pytest.mark.asyncio
    async def test_fetch_transactions_success(self):
        mock_transaction_repo = AsyncMock()
        mock_transaction_repo.list_transactions.return_value = [
            Transaction(
                id=TransactionId("txn-1"),
                user_id=UserId("user-123"),
                type=TransactionType.EXPENSE,
                amount=Money(Decimal("50.00")),
                description="Groceries",
                date=date.today(),
            )
        ]

        use_case = FetchTransactionsUseCase(
            transaction_repository=mock_transaction_repo
        )
        result = await use_case.execute("access-token", page=1, page_size=20)

        assert len(result) == 1
        assert result[0].id == "txn-1"
