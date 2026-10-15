"""Exceptions raised by the purchases domain.

Rooted on the shared kernel's :class:`DomainException`, for the reason
`src/tasks/domain/exceptions.py` documents: AGENTS.md tells views to
``except DomainException`` when they want a friendly flash, so a context-local
root would not be caught and every user-facing failure would degrade into
"An unexpected error occurred".

The two categories here are the two the backend itself distinguishes, and the
split is load-bearing in the views:

* :class:`InvalidPurchasesInputError` -- the BFF refused the input before any
  request was made. The message is written for the user and is safe to show
  verbatim.
* :class:`PurchasesNotFoundError` and its subclasses -- the backend answered 404.
  Owner-scoping is the reason these exist at all: the purchases API returns 404
  for *both* "no such row" and "not yours", and the BFF must not turn that into
  a page that reveals a list exists but belongs to somebody else.
"""

from src.shared.domain.exceptions import DomainException


class InvalidPurchasesInputError(DomainException):
    """User-supplied input failed validation before any I/O happened."""


class InvalidPurchaseError(DomainException):
    """An entity broke an invariant the backend would reject."""


class PurchasesNotFoundError(DomainException):
    """The requested resource does not exist within the caller's scope."""


class ShoppingListNotFoundError(PurchasesNotFoundError):
    """The shopping list is missing, soft-deleted, or owned by somebody else."""


class ShoppingListItemNotFoundError(PurchasesNotFoundError):
    """The item is missing, or does not belong to the addressed list."""


class WishlistNotFoundError(PurchasesNotFoundError):
    """The caller's wishlist could not be resolved."""


class WishlistItemNotFoundError(PurchasesNotFoundError):
    """The wishlist item is missing or owned by somebody else."""


class TagNotFoundError(PurchasesNotFoundError):
    """A tag id does not resolve in the shared catalogue."""
