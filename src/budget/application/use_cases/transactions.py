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
        self,
        access_token: str,
        page: int = 1,
        page_size: int = 20,
        *,
        account_id: str | None = None,
        account_type: str | None = None,
        transaction_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[Transaction]:
        """Fetch user's transactions."""
        return await self.transaction_repository.list_transactions(
            access_token,
            page,
            page_size,
            account_id=account_id,
            account_type=account_type,
            transaction_type=transaction_type,
            date_from=date_from,
            date_to=date_to,
        )


@dataclass
class FetchTransactionsPageUseCase:
    """Use case for fetching one page of transactions and its total count."""

    transaction_repository: TransactionRepositoryPort

    async def execute(
        self,
        access_token: str,
        page: int = 1,
        page_size: int = 20,
        *,
        account_id: str | None = None,
        account_type: str | None = None,
        transaction_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[Transaction], int]:
        """Fetch one page of transactions and its total count."""
        return await self.transaction_repository.list_transactions_page(
            access_token,
            page,
            page_size,
            account_id=account_id,
            account_type=account_type,
            transaction_type=transaction_type,
            date_from=date_from,
            date_to=date_to,
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
    #: Optional frozen "today" for tests. When omitted, the date is resolved per
    #: execution. A value captured at app startup goes stale in a long-running
    #: production process, which makes today's date look like the future after
    #: midnight.
    current_date: date | None = None

    async def execute(self, access_token: str, transaction: Transaction) -> Transaction:
        """Create a new transaction after validation."""
        transaction.validate(self.current_date or date.today())
        return await self.transaction_repository.create_transaction(
            access_token, transaction
        )


@dataclass
class UpdateTransactionUseCase:
    """Use case for updating a transaction."""

    transaction_repository: TransactionRepositoryPort
    #: See `CreateTransactionUseCase.current_date`: resolved per execution so a
    #: long-running process never validates against a stale calendar day.
    current_date: date | None = None

    async def execute(self, access_token: str, transaction: Transaction) -> Transaction:
        """Update an existing transaction after validation."""
        transaction.validate(self.current_date or date.today())
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
