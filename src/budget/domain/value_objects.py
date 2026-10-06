"""Value objects for the budget domain.

Pure Python: dataclasses, `Decimal`, `enum`, `NewType`. No framework, no I/O,
no import from another bounded context.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import NewType

TransactionId = NewType("TransactionId", str)

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
