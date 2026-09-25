"""Domain-specific exceptions."""


class DomainException(Exception):
    """Base exception for domain errors."""

    pass


class InvalidTransactionAmountError(DomainException):
    """Raised when transaction amount is invalid."""

    pass


class InvalidTransactionDateError(DomainException):
    """Raised when transaction date is in the future."""

    pass


class TransactionNotFoundError(DomainException):
    """Raised when a transaction is not found."""

    pass


class AuthenticationError(DomainException):
    """Raised when authentication fails."""

    pass
