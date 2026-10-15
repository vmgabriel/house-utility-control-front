"""Use cases for the caller's single wishlist.

There is no "does a wishlist exist" question anywhere in this context, and that
is the backend's design rather than a shortcut: `GET /wishlist/` is routed to
`GetOrCreateWishlist` and answers 200 with the list whether it created it or
found it. Every use case here therefore starts from that read rather than
handling a missing-wishlist branch.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.purchases.application.validation import normalize_or_raise
from src.purchases.domain.entities import Wishlist
from src.purchases.domain.exceptions import InvalidPurchasesInputError
from src.purchases.domain.ports import WishlistRepositoryPort
from src.purchases.domain.value_objects import (
    MAX_ITEM_DESCRIPTION_LENGTH,
    MAX_ITEM_NAME_LENGTH,
    EstimatedPrice,
    ItemPriority,
    WishlistItemId,
    normalize_text,
    optional_text,
    optional_url,
)


@dataclass(frozen=True, slots=True)
class GetWishlistUseCase:
    """Fetch the caller's wishlist, creating it if this is their first visit."""

    wishlist: WishlistRepositoryPort

    async def execute(self, access_token: str) -> Wishlist:
        """Return the wishlist, never `None`.

        A brand-new user gets an empty list rather than a 404 or an empty-state
        error, which is what makes the wishlist page renderable without a
        "create" route existing anywhere in the BFF.
        """
        return await self.wishlist.get_or_create_wishlist(access_token)


@dataclass(frozen=True, slots=True)
class AddItemToWishlistUseCase:
    """Add one wanted item to the wishlist."""

    wishlist: WishlistRepositoryPort

    async def execute(
        self,
        access_token: str,
        *,
        name: str,
        link: str | None = None,
        image_link: str | None = None,
        description: str | None = None,
        estimated_price: EstimatedPrice | None = None,
        priority: ItemPriority = ItemPriority.WANTED,
    ) -> Wishlist:
        """Validate the item locally, then add it.

        The two URLs are checked by :func:`optional_url`, which refuses anything
        that is not ``http``/``https``. That check is not a courtesy: both values
        are rendered into an ``href`` and an ``<img src>``, so a ``javascript:``
        or ``data:`` URL reaching either attribute is an injection the browser
        would execute. The backend's ``URLField`` would also reject it, but only
        after the round trip, and this context treats an untrusted string on its
        way to a URL attribute as worth refusing at the edge.

        `estimated_price` is passed through as an ``EstimatedPrice``, never as a
        raw string: the non-negativity rule belongs to the value object, and
        letting the form hand a plain decimal past it would make that rule
        optional.
        """
        if not isinstance(priority, ItemPriority):
            raise InvalidPurchasesInputError("Priority must be a known level.")
        if estimated_price is not None and not isinstance(
            estimated_price, EstimatedPrice
        ):
            raise InvalidPurchasesInputError(
                "estimated_price must be an EstimatedPrice or None."
            )

        cleaned_name = normalize_or_raise(
            lambda: normalize_text(name, limit=MAX_ITEM_NAME_LENGTH, label="Item name")
        )
        cleaned_link = normalize_or_raise(lambda: optional_url(link, label="Link"))
        cleaned_image = normalize_or_raise(
            lambda: optional_url(image_link, label="Image link")
        )
        cleaned_description = normalize_or_raise(
            lambda: optional_text(
                description, limit=MAX_ITEM_DESCRIPTION_LENGTH, label="Description"
            )
        )

        return await self.wishlist.add_item(
            access_token,
            name=cleaned_name,
            link=cleaned_link,
            image_link=cleaned_image,
            description=cleaned_description,
            estimated_price=estimated_price,
            priority=priority,
        )


@dataclass(frozen=True, slots=True)
class MarkWishlistItemPurchasedUseCase:
    """Tick one wanted item off as bought."""

    wishlist: WishlistRepositoryPort

    async def execute(self, access_token: str, item_id: WishlistItemId) -> Wishlist:
        """Mark the item and return the wishlist as the backend reloaded it.

        The reloaded wishlist rather than a flag, for the same reason the
        shopping-list equivalent does it: the response carries the item's new
        ``status`` and the wishlist's recomputed ``status``/``completed_at``, so
        the page can say when the last item was bought without a second read.
        """
        if not isinstance(item_id, WishlistItemId):
            raise InvalidPurchasesInputError("item_id must be a WishlistItemId.")
        return await self.wishlist.mark_item_purchased(access_token, item_id)


__all__ = [
    "AddItemToWishlistUseCase",
    "GetWishlistUseCase",
    "MarkWishlistItemPurchasedUseCase",
]
