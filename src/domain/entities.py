"""Domain entities."""

from dataclasses import dataclass
from datetime import date, datetime

from .value_objects import Money, TransactionId, TransactionType, UserId


@dataclass(frozen=True, slots=True)
class User:
    """User entity."""

    id: UserId
    email: str
    name: str
    plan: str  # "free", "pro", or "premium"
    is_active: bool = True


@dataclass(slots=True)
class Transaction:
    """Transaction entity."""

    id: TransactionId | None
    user_id: UserId
    type: TransactionType
    amount: Money
    description: str
    date: date
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def validate(self, current_date: date) -> None:
        """Validate transaction rules."""
        from .exceptions import (
            InvalidTransactionAmountError,
            InvalidTransactionDateError,
        )

        if self.amount.amount <= 0:
            raise InvalidTransactionAmountError("Transaction amount must be positive")

        if self.date > current_date:
            raise InvalidTransactionDateError(
                "Transaction date cannot be in the future"
            )


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    """Dashboard summary value object."""

    period: str  # "daily", "weekly", "monthly"
    total_income: Money
    total_expense: Money
    total_investment: Money
    total_savings: Money
    net_balance: Money
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class DashboardOverview:
    """Dashboard overview with current snapshots."""

    today: DashboardSummary | None
    this_week: DashboardSummary | None
    this_month: DashboardSummary | None
