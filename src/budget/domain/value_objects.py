"""Value objects for the budget domain.

Pure Python: dataclasses, `Decimal`, `enum`, `NewType`. No framework, no I/O,
no import from another bounded context.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import NewType

TransactionId = NewType("TransactionId", str)

#: Identity of a persisted funding account. A `NewType` like `TransactionId`,
#: so a raw string from the wire has to be named before it can reach an entity.
AccountId = NewType("AccountId", str)

#: Declared here rather than imported from `src.identity` because a context must
#: not import another (AGENTS.md, constraint 1). `rentals` likewise defines its
#: own `UserId` in `rentals/domain/value_objects.py`, so this is the established
#: arrangement rather than a workaround.
#:
#: In practice the backend derives ownership from the access token and omits
#: `user_id` from transaction payloads, so this is always empty when a
#: transaction arrives over the wire. It is kept because the entity models
#: ownership, and a future endpoint that does return it would need the field.
UserId = NewType("UserId", str)


class TransactionType(str, Enum):
    """Types of transactions."""

    INCOME = "income"
    EXPENSE = "expense"
    INVESTMENT = "investment"
    SAVINGS = "savings"


class AccountType(str, Enum):
    """Kinds of funding source a transaction can be paid from.

    Mirrors the backend's ``AccountType`` wire values exactly, so a member
    serialises straight into the create-account payload.
    """

    CREDIT_CARD = "credit_card"
    DEBIT = "debit"
    CASH = "cash"
    SAVINGS = "savings"

    @property
    def label(self) -> str:
        """Human-readable label for templates ("Credit card", ...)."""
        return self.value.replace("_", " ").capitalize()


class AnalyticsPeriod(str, Enum):
    """Granularity of a transaction analytics aggregation."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AnalyticsGroupBy(str, Enum):
    """How analytics rows are grouped.

    ``TYPE`` produces one row per period with an income/expense/investment/
    savings breakdown; ``CATEGORY`` produces one row per expense category with
    a single total. The two are mutually exclusive, which is why the mapper
    branches on this rather than on the presence of fields.
    """

    TYPE = "type"
    CATEGORY = "category"


@dataclass(frozen=True, slots=True)
class Money:
    """Value object representing a monetary amount."""

    amount: Decimal

    def __post_init__(self):
        if self.amount < 0:
            raise ValueError("Money amount cannot be negative")
        # Ensure max 2 decimal places
        if self.amount.as_tuple().exponent < -2:
            raise ValueError("Money amount cannot have more than 2 decimal places")

    @classmethod
    def from_string(cls, amount_str: str) -> "Money":
        """Create Money from a string representation."""
        return cls(Decimal(amount_str))

    def __str__(self) -> str:
        return f"{self.amount:.2f}"


@dataclass(frozen=True, slots=True)
class SignedMoney:
    """A monetary amount that may be negative.

    Balances are a different concept from amounts: a transaction amount is
    always positive (enforced by :class:`Transaction.validate`), but a net
    balance is negative whenever spending exceeds income. Modelling that with
    :class:`Money` would force callers to hide real overspending behind an
    absolute value, so balances get their own value object.
    """

    amount: Decimal

    def __post_init__(self):
        if self.amount.as_tuple().exponent < -2:
            raise ValueError("Money amount cannot have more than 2 decimal places")

    @classmethod
    def from_string(cls, amount_str: str) -> "SignedMoney":
        return cls(Decimal(amount_str))

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def __str__(self) -> str:
        return f"{self.amount:.2f}"
