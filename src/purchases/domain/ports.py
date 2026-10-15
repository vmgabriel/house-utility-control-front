"""Repository protocols (ports) for the purchases context.

Three protocols, because the backend exposes three genuinely different
resources with three different sets of operations: shopping lists (with items
addressed *under a list*, so an item id alone does not identify a row), the
wishlist (items addressed by id alone, since there is only ever one wishlist),
and the tag catalogue (shared, and idempotent by name).

**The access token is the first positional argument of every method.** This is a
hard requirement, not a style choice. ``AutoRefreshingRepository`` -- the proxy
that refreshes an expired JWT and retries once on ``401`` -- identifies the token
by position and substitutes the refreshed one into ``args[0]``. A port that put a
``ShoppingListId`` first would have a refreshed JWT silently written into that
slot. ``tests/integration/test_port_conformance.py`` compares parameter *names*
and *order* for exactly this reason.

**Ownership is never passed.** The backend derives the caller from the JWT on
every purchases endpoint; sending a user id would be ignored, so it is not part
of the contract here either.

**Ports speak domain, never wire.** ``create_list`` takes a name and a
``ListPriority`` and returns a ``ShoppingList`` -- not a
``ShoppingListSerializer`` payload. The adapter owns the field-name mapping; a
use case that had to know the serialiser's shape would put DRF's contract in the
inner layers.

**404 means two things and both are `None` here.** Every purchases route is
owner-scoped and answers 404 for "no such row" *and* for "not yours" (see
`tests/integration/purchases/test_api.py::test_wishlist_items_are_owner_scoped`
on the backend). So a read returns ``None`` rather than distinguishing, and a
mutation raises :class:`~src.purchases.domain.exceptions.PurchasesNotFoundError`
rather than reporting a 403 -- which would confirm the resource exists.
"""

from __future__ import annotations

from typing import Protocol

from src.purchases.domain.entities import (
    ShoppingList,
    ShoppingListItem,
    Tag,
    Wishlist,
    WishlistItem,
)
from src.purchases.domain.value_objects import (
    EstimatedPrice,
    ItemPriority,
    ListPriority,
    ShoppingListId,
    ShoppingListItemId,
    TagId,
    WishlistItemId,
)


class ShoppingListRepositoryPort(Protocol):
    """Port for shopping lists and the items nested inside them."""

    async def get_active_lists(self, access_token: str) -> list[ShoppingList]:
        """List the caller's active lists.

        Only active ones: `GET /shopping-lists/` is filtered server-side by
        `ListShoppingLists` and a soft-deleted list is simply not in it. The
        archived set is a *different* endpoint
        (`GET /shopping-lists/inactive/`), which is why this is not a flag
        argument.
        """
        ...

    async def get_inactive_lists(self, access_token: str) -> list[ShoppingList]:
        """List the caller's soft-deleted lists, which they can restore."""
        ...

    async def get_list(
        self, access_token: str, list_id: ShoppingListId
    ) -> ShoppingList | None:
        """Fetch one list with its items nested, or `None` when it is not the caller's.

        `None` rather than a raise: the detail page renders "not found" and the
        overview simply never links a list it could not read.
        """
        ...

    async def create_list(
        self, access_token: str, name: str, priority: ListPriority
    ) -> ShoppingList:
        """Create a list and return it as the backend stored it."""
        ...

    async def update_list(
        self,
        access_token: str,
        list_id: ShoppingListId,
        *,
        name: str | None = None,
        priority: ListPriority | None = None,
    ) -> ShoppingList:
        """Apply a partial update.

        Raises :class:`ShoppingListNotFoundError` on the backend's 404. Both
        arguments are optional because the backend's `UpdateShoppingListSerializer`
        rejects an empty body outright, so "change nothing" is a 400 rather than
        a no-op and the caller has to send something.
        """
        ...

    async def soft_delete_list(
        self, access_token: str, list_id: ShoppingListId
    ) -> ShoppingList:
        """Archive a list.

        A soft delete: `DELETE /shopping-lists/<pk>/` sets ``is_active = false``
        and answers **200 with the list**, not 204. The rows and their items
        survive and `restore_list` brings them back, which is why this is named
        for what it does rather than `delete_list`.
        """
        ...

    async def restore_list(
        self, access_token: str, list_id: ShoppingListId
    ) -> ShoppingList:
        """Bring an archived list back, with its items intact."""
        ...

    async def add_item(
        self,
        access_token: str,
        list_id: ShoppingListId,
        *,
        name: str,
        quantity: int = 1,
        details: str | None = None,
    ) -> ShoppingList:
        """Add an item and return the **whole reloaded list**.

        The return value is the point, not a convenience:
        `POST /shopping-lists/<pk>/add-item/` answers **201** with the entire
        `ShoppingListSerializer`. That is the only place the new item's id
        appears, so a redirect-back page has exactly one read to render from
        rather than a second request for the list.
        """
        ...

    async def update_item(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
        *,
        name: str | None = None,
        quantity: int | None = None,
        details: str | None = None,
    ) -> ShoppingListItem:
        """Update one item. Answers 200 with the item alone, not the list."""
        ...

    async def delete_item(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
    ) -> ShoppingList:
        """Remove an item and return the reloaded list (200, not 204)."""
        ...

    async def mark_item_purchased(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
    ) -> ShoppingList:
        """Tick an item off and return the reloaded list.

        The whole list, because the same response carries the list's recomputed
        ``status`` and ``completed_at``: ticking the last item off is what
        completes the list, and the page has to say so without a second read.
        """
        ...

    async def add_tags_to_item(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
        tag_names: list[str],
    ) -> ShoppingListItem:
        """Attach tags by name. The backend creates any that do not exist yet."""
        ...

    async def remove_tags_from_item(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
        tag_ids: list[TagId],
    ) -> ShoppingListItem:
        """Detach tags by id.

        By id rather than by name, because `RemoveTagsSerializer` declares
        ``tag_ids``. That is the right key for a *removal*: a name identifies a
        catalogue entry (which the POST endpoint creates on demand), while an id
        identifies a specific attachment on this item, and detaching is a
        statement about the attachment.
        """
        ...


class WishlistRepositoryPort(Protocol):
    """Port for the caller's single wishlist and its items."""

    async def get_or_create_wishlist(self, access_token: str) -> Wishlist:
        """Fetch the wishlist, creating it on first access.

        Never returns `None` and never raises for a missing wishlist: the
        backend's `GET /wishlist/` is routed to `GetOrCreateWishlist` and
        answers 200 with the list whether it made it or found it. The BFF
        therefore never has to ask whether one exists, and cannot tell the two
        cases apart -- which is the backend's choice and is documented rather
        than worked around.
        """
        ...

    async def update_wishlist(self, access_token: str, name: str) -> Wishlist:
        """Rename the wishlist.

        Takes a name rather than an id: `PATCH /wishlist/` is not addressed by
        uuid, it resolves the caller's own list, and it answers 200 with the
        whole wishlist.
        """
        ...

    async def add_item(
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
        """Add a wanted item and return the whole reloaded wishlist (201).

        `estimated_price=None` means "no estimate" and is **omitted** from the
        request body rather than sent as ``null``: `AddWishlistItemSerializer`
        declares the field ``required=False, allow_null=True``, and omitting it
        is what "no estimate" means on a create.
        """
        ...

    async def update_item(
        self,
        access_token: str,
        item_id: WishlistItemId,
        *,
        name: str | None = None,
        link: str | None = None,
        image_link: str | None = None,
        description: str | None = None,
        estimated_price: EstimatedPrice | None = None,
        priority: ItemPriority | None = None,
    ) -> WishlistItem:
        """Update one wishlist item. Answers 200 with the item alone."""
        ...

    async def delete_item(self, access_token: str, item_id: WishlistItemId) -> Wishlist:
        """Remove an item and return the reloaded wishlist (200, not 204)."""
        ...

    async def mark_item_purchased(
        self, access_token: str, item_id: WishlistItemId
    ) -> Wishlist:
        """Tick an item off and return the reloaded wishlist.

        The whole wishlist, for the same reason as the shopping list: the
        response carries the recomputed ``status`` and ``completed_at``.
        """
        ...

    async def add_tags_to_item(
        self, access_token: str, item_id: WishlistItemId, tag_names: list[str]
    ) -> WishlistItem:
        """Attach tags by name, creating any that do not exist."""
        ...

    async def remove_tags_from_item(
        self, access_token: str, item_id: WishlistItemId, tag_ids: list[TagId]
    ) -> WishlistItem:
        """Detach tags by id. See the shopping-list port for why removal is by id."""
        ...


class TagRepositoryPort(Protocol):
    """Port for the shared tag catalogue."""

    async def get_all_tags(self, access_token: str) -> list[Tag]:
        """List every tag in the catalogue.

        The catalogue is **global, not owner-scoped** -- `TagListView` takes no
        user and `Tag` has no owner column. So this is a shared autocomplete
        source rather than the caller's own vocabulary, and the BFF must not
        present it as "your tags".
        """
        ...

    async def create_or_get_tag(self, access_token: str, name: str) -> Tag:
        """Add a tag to the catalogue, or return the existing one with that name.

        Idempotent by name, which is what the backend's `CreateTag` use case
        guarantees. It is worth having as its own port method rather than
        relying on it implicitly: the same endpoint answers 201 for a new tag and
        200 for an existing one, and a caller that assumed 201 would misreport
        every second tag it created.
        """
        ...
