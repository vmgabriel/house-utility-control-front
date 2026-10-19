"""Repository protocols (ports) for the budget context."""

from datetime import date
from typing import Protocol

from .entities import (
    DashboardOverview,
    FundingAccount,
    Transaction,
    TransactionAnalytics,
)
from .value_objects import (
    AnalyticsGroupBy,
    AnalyticsPeriod,
    TransactionId,
)


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


class FundingAccountRepositoryPort(Protocol):
    """Port for funding-account operations."""

    async def list_accounts(self, access_token: str) -> list[FundingAccount]:
        """List the caller's active funding accounts."""
        ...

    async def create_account(
        self, access_token: str, account: FundingAccount
    ) -> FundingAccount:
        """Create a funding account and return the persisted result."""
        ...


class AnalyticsRepositoryPort(Protocol):
    """Port for the bounded transaction-analytics aggregation."""

    async def get_analytics(
        self,
        access_token: str,
        start_date: date,
        end_date: date,
        group_by: AnalyticsGroupBy,
        period: AnalyticsPeriod,
    ) -> TransactionAnalytics:
        """Aggregate a bounded date range into chart-ready rows."""
        ...
