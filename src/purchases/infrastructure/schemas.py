"""Hand-written pydantic schemas for the DRF purchases API.

**Authoritative, not the drf-spectacular OpenAPI schema** (AGENTS.md, rule 7).
These are written against `apps/purchases/interfaces/serializers.py` in the
backend, read rather than inferred.

The contracts that matter, and that were checked against the serialisers rather
than assumed:

``status``, not ``is_purchased``
    `ShoppingListItemSerializer`, `WishlistItemSerializer`, `ShoppingListSerializer`
    and `WishlistSerializer` all declare ``status = ChoiceField(choices=
    ITEM_STATUS_CHOICES)`` -- a **string** that is ``"pending"`` or
    ``"purchased"``. There is no ``is_purchased`` field on any purchases
    payload. Modelling one as a boolean would have made every item read as
    unbought.

``estimated_price`` is nullable
    `WishlistItemSerializer` declares ``DecimalField(max_digits=12,
    decimal_places=2, allow_null=True)``, so a user who did not estimate a price
    gets ``null``. Declaring it a plain string would raise inside a mapper on the
    very first wishlist page a new user opens.

``completed_at`` is nullable on both lists
    A list is completed only once every item is purchased, and an empty list is
    never completed, so a freshly created list carries ``null``. Same rule-7
    hazard as above, and the reason `completed_at` is optional here with an
    explicit ``None`` rather than defaulted.

``link`` and ``image_link`` are nullable URLs
    Declared ``allow_null=True`` on both the item and the add/update serialisers.
    Both end up in a template ``href``/``src``, so the domain's `optional_url`
    is what guarantees they are ``http``/``https`` before rendering.

``details`` on a shopping-list item is nullable; ``description`` on a wishlist
item is nullable. Both are separate fields, not one shared "notes" column, and
their limits differ (1000 vs 2000 characters in the backend).

`extra="allow"` everywhere, matching the other contexts: the backend adds fields
without a BFF release, and an unknown field must not fail the page.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DRFTagResponse(BaseModel):
    """One catalogue tag, exactly as `TagSerializer` renders it."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    slug: str


class DRFShoppingListItemResponse(BaseModel):
    """One item of a shopping list (`ShoppingListItemSerializer`)."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    quantity: int = 1
    #: Nullable on the model column, so the live API sends `null` when the user
    #: left the note blank.
    details: str | None = None
    #: `"pending"` or `"purchased"`. See the module docstring.
    status: str = "pending"
    tags: list[DRFTagResponse] = []


class DRFShoppingListResponse(BaseModel):
    """One shopping list with its items nested (`ShoppingListSerializer`)."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    #: `"high"`, `"medium"`, or `"low"`.
    priority: str = "medium"
    #: The list's own completion state, recomputed by the backend from its items.
    status: str = "pending"
    #: `False` after a soft delete. A soft-deleted list is absent from
    #: `GET /shopping-lists/` and present in `GET /shopping-lists/inactive/`.
    is_active: bool = True
    created_at: str | None = None
    #: `null` until every item is purchased -- and forever on an empty list.
    completed_at: str | None = None
    #: The list nests its items; there is no separate item-collection endpoint for
    #: shopping lists, which is why `add_item` returns the whole list.
    items: list[DRFShoppingListItemResponse] = []
    tags: list[DRFTagResponse] = []


class DRFWishlistItemResponse(BaseModel):
    """One item of the wishlist (`WishlistItemSerializer`)."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    #: Both URLs are nullable and both reach a template `href`/`src`.
    link: str | None = None
    image_link: str | None = None
    description: str | None = None
    #: **A decimal rendered as a string**, because DRF's `COERCE_DECIMAL_TO_STRING`
    #: is on by default: the API sends `"399.90"`, never `399.9`. Modelling it as
    #: `float` would lose the two-decimal precision the serialiser guarantees.
    #: Nullable, see the module docstring.
    estimated_price: str | None = None
    #: `"must_have"`, `"wanted"`, or `"nice_to_have"`.
    priority: str = "wanted"
    status: str = "pending"
    tags: list[DRFTagResponse] = []


class DRFWishlistResponse(BaseModel):
    """The wishlist with its items nested (`WishlistSerializer`)."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    status: str = "pending"
    is_active: bool = True
    created_at: str | None = None
    completed_at: str | None = None
    items: list[DRFWishlistItemResponse] = []


__all__ = [
    "DRFShoppingListItemResponse",
    "DRFShoppingListResponse",
    "DRFTagResponse",
    "DRFWishlistItemResponse",
    "DRFWishlistResponse",
]
