"""Repository protocols (ports) for dependency inversion."""

from typing import Protocol

from .entities import DashboardOverview, Transaction, User
from .value_objects import TransactionId


class AuthRepositoryPort(Protocol):
    """Port for authentication operations."""

    async def login(self, email: str, password: str) -> tuple[str, str]:
        """Authenticate user and return (access_token, refresh_token)."""
        ...

    async def refresh_token(self, refresh_token: str) -> tuple[str, str]:
        """Refresh access token and return (new_access_token, new_refresh_token)."""
        ...

    async def logout(self, access_token: str) -> None:
        """Logout user."""
        ...

    async def get_current_user(self, access_token: str) -> User:
        """Get current authenticated user."""
        ...


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
