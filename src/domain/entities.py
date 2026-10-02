"""Domain entities."""

from dataclasses import dataclass
from datetime import date, datetime

from .value_objects import (
    Money,
    SignedMoney,
    TransactionId,
    TransactionType,
    UserId,
)

DEFAULT_CATEGORY = "General"


@dataclass(frozen=True, slots=True)
class User:
    """User entity."""

    id: UserId
    email: str
    name: str
    plan: str  # "free", "pro", or "premium"
    is_active: bool = True
    is_staff: bool = False


@dataclass(slots=True)
class Transaction:
    """Transaction entity."""

    id: TransactionId | None
    user_id: UserId
    type: TransactionType
    amount: Money
    description: str
    date: date
    # The DRF backend requires a category on create; the default keeps
    # programmatic construction convenient without weakening the API contract.
    category: str = DEFAULT_CATEGORY
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def validate(self, current_date: date) -> None:
        """Validate transaction rules."""
        from .exceptions import (
            InvalidTransactionAmountError,
            InvalidTransactionCategoryError,
            InvalidTransactionDateError,
        )

        if self.amount.amount <= 0:
            raise InvalidTransactionAmountError("Transaction amount must be positive")

        if not self.category.strip():
            raise InvalidTransactionCategoryError(
                "Transaction category cannot be blank"
            )

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
    # A balance, not an amount: negative whenever spending exceeds income.
    net_balance: SignedMoney
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class DashboardOverview:
    """Dashboard overview with current snapshots."""

    today: DashboardSummary | None
    this_week: DashboardSummary | None
    this_month: DashboardSummary | None
