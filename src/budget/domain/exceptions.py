"""Exceptions raised by the budget domain.

Every rule violation derives from the shared :class:`DomainException`, so a view
can catch one type and render a friendly flash (AGENTS.md, "Error Handling in
Views") while unexpected errors still surface as bugs.
"""

from src.shared.domain.exceptions import DomainException


class InvalidTransactionAmountError(DomainException):
    """Raised when transaction amount is invalid."""


class InvalidTransactionDateError(DomainException):
    """Raised when transaction date is in the future."""


class InvalidTransactionCategoryError(DomainException):
    """Raised when transaction category is missing or blank."""


class TransactionNotFoundError(DomainException):
    """Raised when a transaction is not found."""
