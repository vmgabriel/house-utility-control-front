"""Transaction use cases."""

from dataclasses import dataclass
from datetime import date

from src.budget.domain.entities import Transaction
from src.budget.domain.ports import TransactionRepositoryPort
from src.budget.domain.value_objects import TransactionId


@dataclass
class FetchTransactionsUseCase:
    """Use case for fetching transactions."""

    transaction_repository: TransactionRepositoryPort

    async def execute(
        self, access_token: str, page: int = 1, page_size: int = 20
    ) -> list[Transaction]:
        """Fetch user's transactions."""
        return await self.transaction_repository.list_transactions(
            access_token, page, page_size
        )


@dataclass
class GetTransactionUseCase:
    """Use case for getting a specific transaction."""

    transaction_repository: TransactionRepositoryPort

    async def execute(
        self, access_token: str, transaction_id: TransactionId
    ) -> Transaction | None:
        """Get a specific transaction."""
        return await self.transaction_repository.get_transaction(
            access_token, transaction_id
        )


@dataclass
class CreateTransactionUseCase:
    """Use case for creating a transaction."""

    transaction_repository: TransactionRepositoryPort
    current_date: date

    async def execute(self, access_token: str, transaction: Transaction) -> Transaction:
        """Create a new transaction after validation."""
        transaction.validate(self.current_date)
        return await self.transaction_repository.create_transaction(
            access_token, transaction
        )


@dataclass
class UpdateTransactionUseCase:
    """Use case for updating a transaction."""

    transaction_repository: TransactionRepositoryPort
    current_date: date

    async def execute(self, access_token: str, transaction: Transaction) -> Transaction:
        """Update an existing transaction after validation."""
        transaction.validate(self.current_date)
        return await self.transaction_repository.update_transaction(
            access_token, transaction
        )


@dataclass
class DeleteTransactionUseCase:
    """Use case for deleting a transaction."""

    transaction_repository: TransactionRepositoryPort

    async def execute(self, access_token: str, transaction_id: TransactionId) -> None:
        """Delete a transaction."""
        await self.transaction_repository.delete_transaction(
            access_token, transaction_id
        )
