"""Immutable value objects for the purchases domain.

Pure Python: no Flask, no httpx, no pydantic, no DRF (AGENTS.md, constraint 2).

**The enums mirror the backend's wire values, not its class names.** The DRF
serialisers declare ``ITEM_STATUS_CHOICES`` (``pending`` / ``purchased``) and
``WISHLIST_PRIORITY_CHOICES`` (``must_have`` / ``wanted`` / ``nice_to_have``) --
see `apps/purchases/domain/value_objects.py` in the backend. An earlier draft of
this context modelled the item flag as ``is_purchased: bool`` on the assumption
that the payload was a boolean. It is not: the API sends a ``status`` *string*,
so a boolean field here would have had every item arrive as ``False`` and every
checklist render as "nothing bought yet". The mapping happens once, in
`src/purchases/infrastructure/mappers.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from uuid import UUID

from src.purchases.domain.exceptions import InvalidPurchaseError


class ItemStatus(str, Enum):
    """Whether something is still to be bought.

    ``str, Enum`` rather than ``StrEnum`` deliberately. Ruff's ``UP042`` rewrite
    targets exactly this shape, and the resulting member serialises differently
    to the DRF API in some Python versions; the AGENTS.md rule for
    ``TransactionType`` applies here for the same reason. The wire value is what
    goes to and from the backend, so ``str`` is what these are.
    """

    PENDING = "pending"
    PURCHASED = "purchased"

    @property
    def is_purchased(self) -> bool:
        """Whether the item has been bought."""
        return self is ItemStatus.PURCHASED

    @property
    def label(self) -> str:
        return "Purchased" if self.is_purchased else "Pending"


class ListPriority(str, Enum):
    """Importance assigned to a shopping list."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class ItemPriority(str, Enum):
    """Desire level assigned to a wishlist item.

    Named ``ItemPriority`` rather than ``WishlistPriority`` because the backend
    calls it ``WishlistPriority`` on its *item* entity and this context's
    shopping-list items carry no priority at all -- `ListPriority` does that.
    Two similarly-shaped enums is the price of naming things for what they are
    rather than for the aggregate they hang off.
    """

    MUST_HAVE = "must_have"
    WANTED = "wanted"
    NICE_TO_HAVE = "nice_to_have"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


#: Mirrors the backend's `MAX_TAG_NAME_LENGTH`.
MAX_TAG_NAME_LENGTH = 50
#: Mirrors the backend's `MAX_LIST_NAME_LENGTH`.
MAX_LIST_NAME_LENGTH = 200
#: Mirrors the backend's `MAX_ITEM_NAME_LENGTH`.
MAX_ITEM_NAME_LENGTH = 200
#: Mirrors the backend's `MAX_ITEM_DETAILS_LENGTH` (shopping-list items).
MAX_ITEM_DETAILS_LENGTH = 1000
#: Mirrors the backend's `MAX_ITEM_DESCRIPTION_LENGTH` (wishlist items).
MAX_ITEM_DESCRIPTION_LENGTH = 2000
#: Mirrors the backend's `MAX_URL_LENGTH`, for both `link` and `image_link`.
MAX_URL_LENGTH = 500
#: Mirrors the backend's `MAX_QUANTITY`.
MAX_QUANTITY = 9999
#: Mirrors the backend's `MAX_ESTIMATED_PRICE`, and its
#: `DecimalField(max_digits=12, decimal_places=2)`.
MAX_ESTIMATED_PRICE = Decimal("9999999999.99")


def _to_decimal(raw: object, label: str) -> Decimal:
    """Coerce `raw` to a finite `Decimal`.

    DRF renders decimals as JSON strings (``COERCE_DECIMAL_TO_STRING`` is the
    default), so the mappers hand over strings; accepting an existing `Decimal`
    or `int` as well keeps the value object usable from plain Python callers.
    """
    if isinstance(raw, Decimal):
        candidate = raw
    elif isinstance(raw, bool):
        # bool is an int subclass; True would otherwise become Decimal(1).
        raise InvalidPurchaseError(f"{label} must be a decimal value, not a boolean.")
    else:
        try:
            candidate = Decimal(str(raw).strip())
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise InvalidPurchaseError(
                f"{label} must be a valid decimal value."
            ) from exc
    if not candidate.is_finite():
        raise InvalidPurchaseError(f"{label} must be finite.")
    return candidate


@dataclass(frozen=True, slots=True)
class EstimatedPrice:
    """A finite, non-negative estimate of what a wishlist item costs.

    Non-negative because the backend's `AddWishlistItemSerializer` declares
    ``DecimalField(min_value=0)`` and answers 400 for a negative price. The
    check is duplicated here so the refusal happens before the round trip, with
    a message naming the field the user actually typed into.

    Zero is allowed and means "free", which is a real answer -- a gift someone
    is getting rather than buying is legitimately 0.00. It is distinct from
    *absent*: the API allows ``estimated_price: null``, and the two render
    differently, so ``None`` is modelled as the absence of this object rather
    than as a sentinel amount.
    """

    amount: Decimal

    def __post_init__(self) -> None:
        amount = _to_decimal(self.amount, "Estimated price")
        if amount < 0:
            raise InvalidPurchaseError("Estimated price cannot be negative.")
        if amount > MAX_ESTIMATED_PRICE:
            raise InvalidPurchaseError(
                f"Estimated price cannot exceed {MAX_ESTIMATED_PRICE}."
            )
        object.__setattr__(self, "amount", amount)

    def __str__(self) -> str:
        return f"{self.amount:f}"


@dataclass(frozen=True, slots=True)
class Quantity:
    """A strictly positive item quantity.

    Bounded at the backend's ``MAX_QUANTITY``, so a tampered form cannot produce
    a request the API will reject with a message naming a limit the user has
    never seen.
    """

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise InvalidPurchaseError("Quantity must be a whole number.")
        if self.value < 1:
            raise InvalidPurchaseError("Quantity cannot be less than one.")
        if self.value > MAX_QUANTITY:
            raise InvalidPurchaseError(f"Quantity cannot exceed {MAX_QUANTITY}.")

    def __int__(self) -> int:
        return self.value


@dataclass(frozen=True, slots=True)
class ShoppingListId:
    """Identity of a persisted shopping list."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _coerce_uuid(self.value, "Shopping list id"))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ShoppingListItemId:
    """Identity of a persisted shopping list item."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _coerce_uuid(self.value, "Shopping list item id")
        )

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class WishlistId:
    """Identity of a persisted wishlist."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _coerce_uuid(self.value, "Wishlist id"))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class WishlistItemId:
    """Identity of a persisted wishlist item."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _coerce_uuid(self.value, "Wishlist item id"))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class TagId:
    """Identity of a tag in the shared catalogue."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _coerce_uuid(self.value, "Tag id"))

    def __str__(self) -> str:
        return str(self.value)


def _coerce_uuid(raw: object, label: str) -> UUID:
    """Coerce `raw` to a `UUID`, rejecting anything that is not one.

    A malformed id means the template and the domain have drifted -- a UUID that
    stops parsing is a bug, not a user mistake, and guessing would silently
    address whichever list the string happened to parse as.
    """
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw).strip())
    except (AttributeError, TypeError, ValueError) as exc:
        raise InvalidPurchaseError(f"{label} must be a UUID.") from exc


def normalize_text(raw: object, *, limit: int, label: str) -> str:
    """Collapse whitespace and bound the length of a required name.

    The backend does the same thing (``_normalize_name`` runs `" ".join(
    value.split())``), so doing it here means the value rendered on a card is
    the value that was stored rather than a differently-spaced echo of it.
    """
    text = " ".join(str(raw).split())
    if not text:
        raise InvalidPurchaseError(f"{label} cannot be empty.")
    if len(text) > limit:
        raise InvalidPurchaseError(f"{label} cannot exceed {limit} characters.")
    return text


def optional_text(raw: object | None, *, limit: int, label: str) -> str | None:
    """Normalise an optional free-text field, mapping blank to `None`.

    A blank form input posts `""`, which means "cleared" rather than "empty
    string", and the backend's ``_optional_str`` agrees: it strips and maps an
    empty result to `None`.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > limit:
        raise InvalidPurchaseError(f"{label} cannot exceed {limit} characters.")
    return text


def optional_url(raw: object | None, *, label: str) -> str | None:
    """Normalise an optional http(s) URL.

    Refuses anything that is not ``http``/``https``, because the field is
    rendered into an ``href`` and an ``<img src>``: a ``javascript:`` or
    ``data:`` value that reached either attribute would be an injection the
    browser would happily execute. The backend validates with ``URLField``,
    which is stricter about some shapes, but this is the check that keeps
    untrusted text out of a URL attribute in the first place.
    """
    text = optional_text(raw, limit=MAX_URL_LENGTH, label=label)
    if text is None:
        return None
    lowered = text.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise InvalidPurchaseError(f"{label} must be an http or https URL.")
    return text


def parse_completed_at(raw: object | None) -> datetime | None:
    """Read the backend's ``completed_at`` stamp.

    `ShoppingListSerializer.completed_at` is ``allow_null=True``, and it *is*
    null on a freshly created list -- the aggregate only stamps it once every
    item is purchased, and an empty list is never completed. So `None` is a
    normal answer here and the schema below models it as nullable rather than
    defaulting it, for the reason AGENTS.md rule 7 records about this backend's
    spec/runtime divergence.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InvalidPurchaseError("Completion timestamp is not a valid date.") from exc


__all__ = [
    "MAX_ESTIMATED_PRICE",
    "MAX_ITEM_DESCRIPTION_LENGTH",
    "MAX_ITEM_DETAILS_LENGTH",
    "MAX_ITEM_NAME_LENGTH",
    "MAX_LIST_NAME_LENGTH",
    "MAX_QUANTITY",
    "MAX_TAG_NAME_LENGTH",
    "MAX_URL_LENGTH",
    "EstimatedPrice",
    "ItemPriority",
    "ItemStatus",
    "ListPriority",
    "Quantity",
    "ShoppingListId",
    "ShoppingListItemId",
    "TagId",
    "WishlistId",
    "WishlistItemId",
    "normalize_text",
    "optional_text",
    "optional_url",
    "parse_completed_at",
]
