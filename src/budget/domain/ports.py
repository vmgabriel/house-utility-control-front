"""Repository protocols (ports) for the budget context."""

from typing import Protocol

from .entities import DashboardOverview, Transaction
from .value_objects import TransactionId


class TransactionRepositoryPort(Protocol):
    """Port for transaction operations."""

    async def list_transactions(
        self, access_token: str, page: int = 1, page_size: int = 20
    ) -> list[Transaction]:
        """List user's transactions."""
        ...

    async def get_transaction(
        self, access_token: str, transaction_id: TransactionId
    ) -> Transaction | None:
        """Get a specific transaction."""
        ...

    async def create_transaction(
        self, access_token: str, transaction: Transaction
    ) -> Transaction:
        """Create a new transaction."""
        ...

    async def update_transaction(
        self, access_token: str, transaction: Transaction
    ) -> Transaction:
        """Update an existing transaction."""
        ...

    async def delete_transaction(
        self, access_token: str, transaction_id: TransactionId
    ) -> None:
        """Delete a transaction."""
        ...


class DashboardRepositoryPort(Protocol):
    """Port for dashboard operations."""

    async def get_overview(self, access_token: str) -> DashboardOverview:
        """Get dashboard overview."""
        ...

    async def get_daily_summary(self, access_token: str) -> list:
        """Get daily summaries."""
        ...

    async def get_weekly_summary(self, access_token: str) -> list:
        """Get weekly summaries."""
        ...

    async def get_monthly_summary(self, access_token: str) -> list:
        """Get monthly summaries."""
        ...
