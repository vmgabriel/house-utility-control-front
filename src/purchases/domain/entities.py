"""Purchases aggregates.

Two roots and their children: a shopping list owns its items, and the single
per-user wishlist owns its own. Tags live in a catalogue shared across both and
are only ever attached to or detached from an item.

**These are read models here.** Every entity in this context is produced by a
mapper from a backend payload, and the backend owns every rule that matters --
ownership, completion, archival, the tag catalogue. The factories below exist
for the construction paths (a use case validating input before a round trip) and
the properties exist for the read paths (the templates). No entity decides
business outcomes; that would be a second implementation of rules the API
already enforces, free to disagree with it.

Note `status: ItemStatus`, not `is_purchased: bool`. The DRF payloads carry a
``status`` string, and the aggregate's own completion state is a *list*-level
fact derived from its items -- so the boolean belongs on the list, not smeared
across every item as a second source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from src.purchases.domain.value_objects import (
    EstimatedPrice,
    ItemPriority,
    ItemStatus,
    ListPriority,
    Quantity,
    ShoppingListId,
    ShoppingListItemId,
    TagId,
    WishlistId,
    WishlistItemId,
)


@dataclass(frozen=True, slots=True)
class Tag:
    """One label from the shared catalogue.

    Frozen because a tag is an identity plus a name, and the backend mints the
    slug. The catalogue is shared across every list in the product, so two tags
    with the same name are the same tag; giving each caller its own copy would
    invite the two to drift apart.
    """

    id: TagId
    name: str
    slug: str


@dataclass(frozen=True, slots=True)
class ShoppingListItem:
    """One thing to buy, belonging to exactly one shopping list."""

    id: ShoppingListItemId
    name: str
    quantity: Quantity
    status: ItemStatus = ItemStatus.PENDING
    details: str | None = None
    tags: list[Tag] = field(default_factory=list)

    @property
    def is_purchased(self) -> bool:
        """Whether this item has been ticked off."""
        return self.status.is_purchased


@dataclass(frozen=True, slots=True)
class ShoppingList:
    """A named group of things to buy, owned by a single user."""

    id: ShoppingListId
    name: str
    priority: ListPriority = ListPriority.MEDIUM
    status: ItemStatus = ItemStatus.PENDING
    is_active: bool = True
    created_at: datetime | None = None
    completed_at: datetime | None = None
    items: list[ShoppingListItem] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)

    @property
    def is_completed(self) -> bool:
        """Whether the backend considers this list complete.

        Reads the list's own ``status``, which the backend recomputes on every
        response from its items, rather than re-deriving it here. Re-deriving
        would be a second implementation of the same rule -- and the backend's
        has a clause this BFF would have to copy exactly: **an empty list is
        never completed**, because there is no completion date to age out, so a
        list the user has just created must not read as finished. Reading the
        authority is both shorter and impossible to disagree with.
        """
        return self.status.is_purchased

    @property
    def pending_items(self) -> list[ShoppingListItem]:
        """Items still to be bought, in the backend's order."""
        return [item for item in self.items if not item.status.is_purchased]

    @property
    def purchased_items(self) -> list[ShoppingListItem]:
        """Items already bought, in the backend's order."""
        return [item for item in self.items if item.status.is_purchased]

    @property
    def progress_ratio(self) -> float:
        """Share of items bought, 0.0 for an empty list.

        A property rather than a stored figure because the backend recomputes it
        on every response and there is no endpoint that reports it: deriving it
        from the same item list the page renders is the only way the progress bar
        cannot disagree with the rows beneath it.
        """
        if not self.items:
            return 0.0
        return len(self.purchased_items) / len(self.items)


@dataclass(frozen=True, slots=True)
class WishlistItem:
    """One wanted product, belonging to the caller's single wishlist."""

    id: WishlistItemId
    name: str
    priority: ItemPriority = ItemPriority.WANTED
    status: ItemStatus = ItemStatus.PENDING
    link: str | None = None
    image_link: str | None = None
    description: str | None = None
    #: ``None`` when the user gave no figure -- the API's ``estimated_price`` is
    #: nullable, and "no estimate" and "estimated zero" are different answers.
    estimated_price: EstimatedPrice | None = None
    tags: list[Tag] = field(default_factory=list)

    @property
    def is_purchased(self) -> bool:
        """Whether this item has been bought."""
        return self.status.is_purchased

    @property
    def estimated_price_amount(self) -> Decimal | None:
        """The raw figure, or `None`. Templates format money through a ViewModel."""
        return self.estimated_price.amount if self.estimated_price is not None else None


@dataclass(frozen=True, slots=True)
class Wishlist:
    """The single wishlist owned by a user.

    One per user, created on first `GET`. The BFF therefore never has to ask
    "does this user have a wishlist yet?" -- the `GET` endpoint is a get-or-create
    and always answers 200, which is why `get_or_create_wishlist` is the only
    read port on this side.
    """

    id: WishlistId
    name: str
    status: ItemStatus = ItemStatus.PENDING
    is_active: bool = True
    created_at: datetime | None = None
    completed_at: datetime | None = None
    items: list[WishlistItem] = field(default_factory=list)

    @property
    def is_completed(self) -> bool:
        """Whether the backend considers this wishlist complete.

        Read off the aggregate's own ``status`` for the same reason as
        `ShoppingList.is_completed`: the backend recomputes it, and its rule that
        an empty list is never complete is not one this BFF should restate.
        """
        return self.status.is_purchased

    @property
    def pending_items(self) -> list[WishlistItem]:
        """Items still wanted, in the backend's order."""
        return [item for item in self.items if not item.status.is_purchased]

    @property
    def purchased_items(self) -> list[WishlistItem]:
        """Items already bought, in the backend's order."""
        return [item for item in self.items if item.status.is_purchased]


__all__ = [
    "ShoppingList",
    "ShoppingListItem",
    "Tag",
    "Wishlist",
    "WishlistItem",
]
