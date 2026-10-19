"""Budget domain: pure Python entities and value objects.

MUST NOT import from `application`, `infrastructure`, `interfaces`, another
bounded context, or any third-party framework.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .value_objects import (
    AccountId,
    AccountType,
    AnalyticsGroupBy,
    AnalyticsPeriod,
    Money,
    SignedMoney,
    TransactionId,
    TransactionType,
    UserId,
)

DEFAULT_CATEGORY = "General"


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
    #: Optional link to the funding account the money came from. `None` means
    #: the transaction is unlinked; the backend rejects `null` on update, so a
    #: cleared field must be omitted rather than sent empty.
    account_id: AccountId | None = None

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


@dataclass(slots=True)
class FundingAccount:
    """A funding source a transaction can be linked to.

    An entity rather than a value object: it has an identity (`id`) that
    survives a rename, and the backend owns its lifecycle. `id` is `None` for
    an account that has not been created yet.
    """

    id: AccountId | None
    name: str
    account_type: AccountType
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class AnalyticsRow:
    """One flat, chart-ready analytics row.

    The backend serialises two mutually exclusive row shapes, so the entity
    models both rather than splitting into a union: a ``type`` row carries the
    four type amounts (zero-filled when a bucket had no activity of a type),
    and a ``category`` row carries a single ``total``. The unused side stays
    `None`, which keeps "the backend did not send this" distinguishable from
    "the backend sent zero".
    """

    label: str
    income: Decimal | None = None
    expense: Decimal | None = None
    investment: Decimal | None = None
    savings: Decimal | None = None
    total: Decimal | None = None


@dataclass(frozen=True, slots=True)
class TransactionAnalytics:
    """A bounded, chart-ready aggregation result."""

    period: AnalyticsPeriod
    group_by: AnalyticsGroupBy
    rows: list[AnalyticsRow]
