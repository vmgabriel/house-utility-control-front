"""DRF payloads -> purchases domain entities.

This is the **only** place that knows a purchases item is purchased because its
``status`` says so rather than because a boolean says so. Every other layer sees
`ItemStatus.PURCHASED` and never the wire string, which is what makes a future
change to the backend's vocabulary a one-file change.

Three conversions are worth calling out, because each has a failure mode that a
naive mapper would turn into a 500 on a page a user just created data on:

**Status strings.** `ItemStatus(raw)` raises a bare ``ValueError`` for anything
unrecognised, which is *not* a `DomainException` and would therefore escape a
view's `except DomainException` and surface as a 500. An unrecognised status is
re-labelled here as `InvalidPurchaseError` instead. Unknown values are treated as
``PENDING``, never as ``PURCHASED``: guessing "bought" would render a checklist
ticking itself off, and guessing wrong in that direction is the one that
destroys data the user still has to do.

**`estimated_price` as a string.** DRF renders decimals as JSON strings, so the
schema types it as `str | None`. A blank or absent figure becomes `None` rather
than zero, because "no estimate" and "estimated nothing" are different answers
and the card renders them differently.

**Nullability.** `completed_at`, `link`, `image_link`, `description`, and
`details` are all nullable on the live API (see the schemas module), and every one
of them is modelled as optional rather than defaulted to a plausible-looking
value.
"""

from __future__ import annotations

from src.purchases.domain.entities import (
    ShoppingList,
    ShoppingListItem,
    Tag,
    Wishlist,
    WishlistItem,
)
from src.purchases.domain.exceptions import InvalidPurchaseError
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
    normalize_text,
    optional_text,
    optional_url,
    parse_completed_at,
)
from src.purchases.infrastructure.schemas import (
    DRFShoppingListItemResponse,
    DRFShoppingListResponse,
    DRFTagResponse,
    DRFWishlistItemResponse,
    DRFWishlistResponse,
)


def _parse_status(raw: str, label: str) -> ItemStatus:
    """Read a wire status string into `ItemStatus`.

    Unknown values fall back to ``PENDING`` and are logged by the caller's
    adapter rather than raised, for the reason in the module docstring: a status
    this BFF has not heard of must never read as "already bought".
    """
    try:
        return ItemStatus(raw)
    except ValueError:
        return ItemStatus.PENDING


def _parse_list_priority(raw: str) -> ListPriority:
    """Read a list priority, defaulting to ``medium``.

    ``medium`` rather than a raise: the backend's `CreateShoppingListSerializer`
    defaults to it, and a card that rendered a blank priority badge over an
    unrecognised value would be worse than one that shows the default. This
    value is also the one the form's ``<select>`` offers first.
    """
    try:
        return ListPriority(raw)
    except ValueError:
        return ListPriority.MEDIUM


def _parse_item_priority(raw: str) -> ItemPriority:
    """Read a wishlist item priority, defaulting to ``wanted``.

    ``wanted`` because that is `AddWishlistItemSerializer`'s own default, so an
    item the backend stored without an explicit choice still gets the badge the
    form would have given it.
    """
    try:
        return ItemPriority(raw)
    except ValueError:
        return ItemPriority.WANTED


def map_tag(payload: DRFTagResponse) -> Tag:
    """Map one catalogue tag."""
    return Tag(
        id=TagId(payload.id),
        name=normalize_text(payload.name, limit=200, label="Tag name"),
        slug=payload.slug,
    )


def map_shopping_list_item(payload: DRFShoppingListItemResponse) -> ShoppingListItem:
    """Map one item of a shopping list.

    `quantity` goes through :class:`Quantity` so a stored zero (which the API
    would never send) cannot reach the page as "0" on a checklist row.
    """
    return ShoppingListItem(
        id=ShoppingListItemId(payload.id),
        name=normalize_text(payload.name, limit=200, label="Item name"),
        quantity=Quantity(payload.quantity),
        status=_parse_status(payload.status, "Shopping list item"),
        details=optional_text(payload.details, limit=1000, label="Item details"),
        tags=[map_tag(tag) for tag in payload.tags],
    )


def map_shopping_list(payload: DRFShoppingListResponse) -> ShoppingList:
    """Map one shopping list, with its nested items."""
    return ShoppingList(
        id=ShoppingListId(payload.id),
        name=normalize_text(payload.name, limit=200, label="Shopping list name"),
        priority=_parse_list_priority(payload.priority),
        status=_parse_status(payload.status, "Shopping list"),
        is_active=payload.is_active,
        created_at=parse_completed_at(payload.created_at),
        completed_at=parse_completed_at(payload.completed_at),
        items=[map_shopping_list_item(item) for item in payload.items],
        tags=[map_tag(tag) for tag in payload.tags],
    )


def map_wishlist_item(payload: DRFWishlistItemResponse) -> WishlistItem:
    """Map one wishlist item.

    The two URLs pass through `optional_url`, which refuses anything that is not
    ``http``/``https``. That check belongs here rather than only in the template:
    these values are attacker-influenced text that ends up in an ``href`` and an
    ``<img src>``, and a ``javascript:`` URL surviving into either attribute is an
    injection the browser executes on render. A stored value that somehow fails
    the check becomes `None` (image falls back to a placeholder) rather than
    raising, because a bad link on one card must not blank the whole wishlist.
    """
    try:
        cleaned_link = optional_url(payload.link, label="Link")
        cleaned_image = optional_url(payload.image_link, label="Image link")
    except InvalidPurchaseError:
        cleaned_link = None
        cleaned_image = None

    return WishlistItem(
        id=WishlistItemId(payload.id),
        name=normalize_text(payload.name, limit=200, label="Item name"),
        link=cleaned_link,
        image_link=cleaned_image,
        description=optional_text(payload.description, limit=2000, label="Description"),
        estimated_price=_parse_estimated_price(payload.estimated_price),
        priority=_parse_item_priority(payload.priority),
        status=_parse_status(payload.status, "Wishlist item"),
        tags=[map_tag(tag) for tag in payload.tags],
    )


def map_wishlist(payload: DRFWishlistResponse) -> Wishlist:
    """Map the wishlist, with its nested items.

    `GET /wishlist/` answers the whole aggregate with `items` nested, so there is
    no second read for the items and no need to join them by hand.
    """
    return Wishlist(
        id=WishlistId(payload.id),
        name=normalize_text(payload.name, limit=200, label="Wishlist name"),
        status=_parse_status(payload.status, "Wishlist"),
        is_active=payload.is_active,
        created_at=parse_completed_at(payload.created_at),
        completed_at=parse_completed_at(payload.completed_at),
        items=[map_wishlist_item(item) for item in payload.items],
    )


def _parse_estimated_price(raw: str | None) -> EstimatedPrice | None:
    """Turn the wire decimal string into an `EstimatedPrice`, or `None`.

    `None` -- not zero -- for an absent figure. `AddWishlistItemSerializer`
    declares the field ``required=False, allow_null=True``, so "no estimate" is a
    value the API expresses and the card has to distinguish it from ``"0.00"``.

    A malformed string also becomes `None` rather than raising: the price is
    decoration on a card, and one unreadable figure must not take the wishlist
    page down with it.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return EstimatedPrice(text)
    except InvalidPurchaseError:
        return None


__all__ = [
    "map_shopping_list",
    "map_shopping_list_item",
    "map_tag",
    "map_wishlist",
    "map_wishlist_item",
]
