"""Value objects for the domain."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import NewType

# Type aliases for clarity
UserId = NewType("UserId", str)
TransactionId = NewType("TransactionId", str)


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
