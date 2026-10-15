"""One helper both use-case modules use to label validation failures.

Kept in its own module rather than defined twice, because two copies of a
translation like this is how the two start disagreeing -- and the disagreement
would be invisible, because both versions look correct and both produce a
flash.

**Why the two exception types are kept apart at all**, given that a view's
``except DomainException`` catches either and the user sees the same words:
the type is what a caller branches on to decide whether a failure happened
*before* or *after* I/O. Getting that wrong is how a retryable backend failure
ends up reported to the user as a typo in their form. `InvalidPurchaseError`
means "the domain refused to build this"; `InvalidPurchasesInputError` means "this
form post is not acceptable"; both are total failures, and neither is a transport
error.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from src.purchases.domain.exceptions import (
    InvalidPurchaseError,
    InvalidPurchasesInputError,
)

T = TypeVar("T")


def normalize_or_raise(build: Callable[[], T]) -> T:
    """Run a domain validation step, re-labelling its failure as input error."""
    try:
        return build()
    except InvalidPurchasesInputError:
        raise
    except InvalidPurchaseError as exc:
        raise InvalidPurchasesInputError(str(exc)) from exc


__all__ = ["normalize_or_raise"]
