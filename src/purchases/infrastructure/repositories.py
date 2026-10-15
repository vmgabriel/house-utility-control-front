"""DRF adapters implementing the three purchases ports.

Three classes for the three resources, for the reason the ports are three: the
routing is genuinely different. A shopping-list item is addressed *under a list*
(`/shopping-lists/<list>/items/<item>/`), so its port methods take both ids and
a mismatched pair is a 404; a wishlist item is addressed by id alone, because
there is only ever one wishlist per user; and the tag catalogue is shared and
idempotent, which is a third thing again.

Each adapter does exactly three things, following the arrangement
`src/tasks/infrastructure/drf_client.py` documents:

1. delegates the round trip to the shared :class:`DRFAPIClient`, so the base URL,
   headers, transport-error contract, and `ServiceUnavailableError` behaviour are
   identical to every other context;
2. validates the parsed body against the hand-written schemas here, so the
   mappers stay free of both I/O and "what did DRF actually send";
3. turns transport statuses into domain errors, so a view catches
   `ShoppingListNotFoundError` and never learns that DRF speaks in 404s.

**404 handling is deliberately per-intent**, because the same status means
different things on different routes here:

* on a **read**, a 404 becomes ``None`` -- "not yours" and "does not exist" are
  indistinguishable and the BFF must not pretend otherwise;
* on a **mutation**, a 404 raises -- the user was looking at a row that has
  stopped existing, and reporting "done" would hide that their view was stale;
* a 400 becomes `InvalidPurchasesInputError` carrying the backend's own `detail`,
  because a rejection here is a fact about the user's input worth showing
  verbatim rather than a generic "request failed".

`ServiceUnavailableError` is never caught here. It means no response existed at
all, and the global 503 handler is the right owner of that -- see AGENTS.md,
"No Generic 500 Handlers".
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from logging import getLogger

from pydantic import ValidationError as PydanticValidationError

from src.purchases.domain.entities import (
    ShoppingList,
    ShoppingListItem,
    Tag,
    Wishlist,
    WishlistItem,
)
from src.purchases.domain.exceptions import (
    InvalidPurchasesInputError,
    ShoppingListItemNotFoundError,
    ShoppingListNotFoundError,
    WishlistItemNotFoundError,
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
from src.purchases.infrastructure.mappers import (
    map_shopping_list,
    map_shopping_list_item,
    map_tag,
    map_wishlist,
    map_wishlist_item,
)
from src.purchases.infrastructure.schemas import (
    DRFShoppingListItemResponse,
    DRFShoppingListResponse,
    DRFTagResponse,
    DRFWishlistItemResponse,
    DRFWishlistResponse,
)
from src.shared.http.drf_client import (
    DRFAPIClient,
    DRFAPIClientError,
    NotFoundError,
    ValidationError,
)

logger = getLogger()


def _as_list(body: object, entity: str) -> list:
    """Assert a list endpoint really returned a list.

    A DRF list view that starts paginating would answer ``{"count": ..,
    "results": [...]}`` instead, and validating that against a schema would fail
    with a message about a missing ``id`` on the key ``"results"`` -- accurate,
    and useless. The shape is checked first so the failure names the real cause.
    """
    if body is None:
        return []
    if isinstance(body, list):
        return body
    raise InvalidPurchasesInputError(
        f"{entity} endpoint did not return a list, got {type(body).__name__}."
    )


def _as_mapping(body: object, entity: str) -> dict:
    """Assert a detail endpoint really returned an object."""
    if isinstance(body, dict):
        return body
    raise InvalidPurchasesInputError(
        f"{entity} endpoint did not return an object, got {type(body).__name__}."
    )


def _parse(schema: type, body: object, entity: str):
    """Validate a body against `schema`, naming the entity on failure.

    Pydantic's own message for a missing field is precise but mentions the
    schema, which is infrastructure vocabulary. Rewriting it as an
    `InvalidPurchasesInputError` names the resource instead, and it is a
    `DomainException`, so the view's usual handler turns it into a flash rather
    than letting it become a 500.
    """
    if not isinstance(body, dict):
        raise InvalidPurchasesInputError(
            f"{entity} endpoint did not return an object, got {type(body).__name__}."
        )
    try:
        return schema.model_validate(body)
    except PydanticValidationError as exc:
        raise InvalidPurchasesInputError(
            f"The purchases API sent a {entity.lower()} this app cannot read."
        ) from exc


def _detail_from(exc: DRFAPIClientError) -> str:
    """Pull the backend's own message out of a 400.

    DRF answers ``{"detail": "..."}`` for the purchases views (they translate
    domain errors through ``ValidationError({"detail": str(exc)})``) and a
    field-keyed object for serialiser failures. The first is the useful one and
    is what the user is shown; the second has no single sentence, so it falls
    back to the generic message.
    """
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        text = detail.get("detail")
        if isinstance(text, str) and text.strip():
            return text
    return "The purchases service rejected that value."


class DRFShoppingListRepository:
    """Adapter for the shopping-list ports, over the shared DRF client."""

    def __init__(self, api_client: DRFAPIClient) -> None:
        self._api = api_client

    # -- reads -------------------------------------------------------------

    async def get_active_lists(self, access_token: str) -> list[ShoppingList]:
        body = await self._api.list_shopping_lists(access_token)
        rows = _as_list(body, "Shopping list")
        return [
            map_shopping_list(_parse(DRFShoppingListResponse, row, "Shopping list"))
            for row in rows
        ]

    async def get_inactive_lists(self, access_token: str) -> list[ShoppingList]:
        body = await self._api.list_inactive_shopping_lists(access_token)
        rows = _as_list(body, "Shopping list")
        return [
            map_shopping_list(_parse(DRFShoppingListResponse, row, "Shopping list"))
            for row in rows
        ]

    async def get_list(
        self, access_token: str, list_id: ShoppingListId
    ) -> ShoppingList | None:
        """Return the list, or `None` for a 404.

        `None` rather than a raise, because the two reasons -- the list does not
        exist, the list is not the caller's -- arrive as the same 404 and the
        BFF is not entitled to tell them apart.
        """
        try:
            body = await self._api.get_shopping_list(access_token, str(list_id))
        except NotFoundError:
            return None
        return map_shopping_list(
            _parse(
                DRFShoppingListResponse,
                _as_mapping(body, "Shopping list"),
                "Shopping list",
            )
        )

    # -- list mutations ----------------------------------------------------

    async def create_list(
        self, access_token: str, name: str, priority: ListPriority
    ) -> ShoppingList:
        """POST `{"name": ..., "priority": ...}`.

        `priority` is always sent, never omitted: the API defaults it to `medium`
        and sending it explicitly means the stored value and the badge rendered
        on the card come from the same decision rather than from two defaults
        that happen to agree.
        """
        try:
            body = await self._api.create_shopping_list(
                access_token,
                {"name": name, "priority": ListPriority(priority).value},
            )
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_shopping_list(
            _parse(
                DRFShoppingListResponse,
                _as_mapping(body, "Shopping list"),
                "Shopping list",
            )
        )

    async def update_list(
        self,
        access_token: str,
        list_id: ShoppingListId,
        *,
        name: str | None = None,
        priority: ListPriority | None = None,
    ) -> ShoppingList:
        """PATCH the fields that were supplied, and only those.

        An **absent key** is what PATCH means by "leave this alone" here, so
        `None` arguments are omitted from the body rather than sent as `null`.
        Sending `null` would clear the column, and `UpdateShoppingListSerializer`
        would reject it anyway -- a nullable column is not the same as a
        nullable *input*.
        """
        payload: dict[str, object] = {}
        if name is not None:
            payload["name"] = name
        if priority is not None:
            payload["priority"] = ListPriority(priority).value
        if not payload:
            # The API answers 400 for an empty body ("Provide name or
            # priority."), so refusing here gives the user a message that names
            # the problem instead of a 400 about a field they cannot see.
            raise InvalidPurchasesInputError("Provide a name or a priority to change.")

        try:
            body = await self._api.update_shopping_list(
                access_token, str(list_id), payload
            )
        except NotFoundError as exc:
            raise ShoppingListNotFoundError(
                "That shopping list is not available."
            ) from exc
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_shopping_list(
            _parse(
                DRFShoppingListResponse,
                _as_mapping(body, "Shopping list"),
                "Shopping list",
            )
        )

    async def soft_delete_list(
        self, access_token: str, list_id: ShoppingListId
    ) -> ShoppingList:
        """Archive a list. The response is the archived list, not a 204."""
        try:
            body = await self._api.delete_shopping_list(access_token, str(list_id))
        except NotFoundError as exc:
            raise ShoppingListNotFoundError(
                "That shopping list is not available."
            ) from exc
        return map_shopping_list(
            _parse(
                DRFShoppingListResponse,
                _as_mapping(body, "Shopping list"),
                "Shopping list",
            )
        )

    async def restore_list(
        self, access_token: str, list_id: ShoppingListId
    ) -> ShoppingList:
        """POST `restore/`, which needs no body -- an empty one is sent anyway.

        The backend's view takes no request data, and `_request` always sets a
        JSON content type, so an explicit `{}` is sent rather than leaving
        `json_data=None`, which would send `null` as the body.
        """
        try:
            body = await self._api.restore_shopping_list(access_token, str(list_id))
        except NotFoundError as exc:
            raise ShoppingListNotFoundError(
                "That shopping list is not available."
            ) from exc
        return map_shopping_list(
            _parse(
                DRFShoppingListResponse,
                _as_mapping(body, "Shopping list"),
                "Shopping list",
            )
        )

    # -- item mutations ----------------------------------------------------

    async def add_item(
        self,
        access_token: str,
        list_id: ShoppingListId,
        *,
        name: str,
        quantity: int = 1,
        details: str | None = None,
    ) -> ShoppingList:
        """POST `add-item/`, which answers 201 with the **whole reloaded list**.

        `details` is omitted when blank rather than sent as `""`: the field is
        `required=False` and the backend's `_optional_str` maps an empty string
        to `None` anyway, so omitting it saves a field and means the same thing.
        """
        payload: dict[str, object] = {"name": name, "quantity": int(quantity)}
        if details is not None:
            payload["details"] = details
        try:
            body = await self._api.add_shopping_list_item(
                access_token, str(list_id), payload
            )
        except NotFoundError as exc:
            raise ShoppingListNotFoundError(
                "That shopping list is not available."
            ) from exc
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_shopping_list(
            _parse(
                DRFShoppingListResponse,
                _as_mapping(body, "Shopping list"),
                "Shopping list",
            )
        )

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
        """PATCH one item; answers 200 with the **item**, not the list."""
        payload: dict[str, object] = {}
        if name is not None:
            payload["name"] = name
        if quantity is not None:
            payload["quantity"] = int(quantity)
        if details is not None:
            payload["details"] = details
        if not payload:
            raise InvalidPurchasesInputError(
                "Provide a name, quantity, or details to change."
            )
        try:
            body = await self._api.update_shopping_list_item(
                access_token, str(list_id), str(item_id), payload
            )
        except NotFoundError as exc:
            raise ShoppingListItemNotFoundError(
                "That item no longer exists on this list."
            ) from exc
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_shopping_list_item(
            _parse(
                DRFShoppingListItemResponse,
                _as_mapping(body, "Shopping list item"),
                "Shopping list item",
            )
        )

    async def delete_item(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
    ) -> ShoppingList:
        """DELETE one item; answers 200 with the reloaded list."""
        try:
            body = await self._api.delete_shopping_list_item(
                access_token, str(list_id), str(item_id)
            )
        except NotFoundError as exc:
            raise ShoppingListItemNotFoundError(
                "That item no longer exists on this list."
            ) from exc
        return map_shopping_list(
            _parse(
                DRFShoppingListResponse,
                _as_mapping(body, "Shopping list"),
                "Shopping list",
            )
        )

    async def mark_item_purchased(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
    ) -> ShoppingList:
        """POST `mark-purchased/`; answers 200 with the **whole reloaded list**.

        The response is why this returns the aggregate rather than a flag: it
        carries the item's new `status` *and* the list's recomputed `status` and
        `completed_at`, so the redirect-back page knows whether that tick
        completed the list without a second read.
        """
        try:
            body = await self._api.mark_shopping_list_item_purchased(
                access_token, str(list_id), str(item_id)
            )
        except NotFoundError as exc:
            raise ShoppingListItemNotFoundError(
                "That item no longer exists on this list."
            ) from exc
        return map_shopping_list(
            _parse(
                DRFShoppingListResponse,
                _as_mapping(body, "Shopping list"),
                "Shopping list",
            )
        )

    async def add_tags_to_item(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
        tag_names: list[str],
    ) -> ShoppingListItem:
        """Attach tags by name; the API creates any that do not exist yet."""
        try:
            body = await self._api.add_shopping_list_item_tags(
                access_token, str(list_id), str(item_id), list(tag_names)
            )
        except NotFoundError as exc:
            raise ShoppingListItemNotFoundError(
                "That item no longer exists on this list."
            ) from exc
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_shopping_list_item(
            _parse(
                DRFShoppingListItemResponse,
                _as_mapping(body, "Shopping list item"),
                "Shopping list item",
            )
        )

    async def remove_tags_from_item(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
        tag_ids: list[TagId],
    ) -> ShoppingListItem:
        """Detach tags by id. `DELETE` carries a JSON body, as the API expects."""
        try:
            body = await self._api.remove_shopping_list_item_tags(
                access_token,
                str(list_id),
                str(item_id),
                [str(tag_id) for tag_id in tag_ids],
            )
        except NotFoundError as exc:
            raise ShoppingListItemNotFoundError(
                "That item no longer exists on this list."
            ) from exc
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_shopping_list_item(
            _parse(
                DRFShoppingListItemResponse,
                _as_mapping(body, "Shopping list item"),
                "Shopping list item",
            )
        )


class DRFWishlistRepository:
    """Adapter for the wishlist ports, over the shared DRF client."""

    def __init__(self, api_client: DRFAPIClient) -> None:
        self._api = api_client

    async def get_or_create_wishlist(self, access_token: str) -> Wishlist:
        """GET `/wishlist/`, which creates the list on first access.

        Never `None` and never a not-found: the backend's `GetOrCreateWishlist`
        answers 200 whether it made the list or found it, so there is no branch
        here for a user who has never had one.
        """
        body = await self._api.get_wishlist(access_token)
        return map_wishlist(
            _parse(DRFWishlistResponse, _as_mapping(body, "Wishlist"), "Wishlist")
        )

    async def update_wishlist(self, access_token: str, name: str) -> Wishlist:
        """PATCH the name. Addressed by the caller, not by uuid."""
        try:
            body = await self._api.update_wishlist(access_token, {"name": name})
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_wishlist(
            _parse(DRFWishlistResponse, _as_mapping(body, "Wishlist"), "Wishlist")
        )

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
        """POST `add-item/`, which answers 201 with the whole reloaded wishlist.

        `estimated_price` is serialised with ``f"{amount:f}"`` rather than
        `str(amount)`: the API's own responses render it that way, and the
        two-decimal form is what `DecimalField(decimal_places=2)` will quantise
        it back to anyway.

        The three optional text fields are **omitted** when `None` rather than
        sent as `null`, because on a create `null` and absent are the same thing
        to DRF and one body field fewer is one less way for the two paths to
        drift.
        """
        payload: dict[str, object] = {
            "name": name,
            "priority": ItemPriority(priority).value,
        }
        if link is not None:
            payload["link"] = link
        if image_link is not None:
            payload["image_link"] = image_link
        if description is not None:
            payload["description"] = description
        if estimated_price is not None:
            payload["estimated_price"] = f"{estimated_price.amount:f}"

        try:
            body = await self._api.add_wishlist_item(access_token, payload)
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_wishlist(
            _parse(DRFWishlistResponse, _as_mapping(body, "Wishlist"), "Wishlist")
        )

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
        """PATCH one item; answers 200 with the item, not the wishlist.

        Only the supplied keys are sent. Unlike a create, an *update* does treat
        ``null`` as a real instruction on these fields -- that is how a price is
        cleared -- but the callers here pass `None` to mean "leave alone", so the
        distinction is the caller's to make by building an explicit payload
        rather than something this adapter guesses.
        """
        payload: dict[str, object] = {}
        if name is not None:
            payload["name"] = name
        if link is not None:
            payload["link"] = link
        if image_link is not None:
            payload["image_link"] = image_link
        if description is not None:
            payload["description"] = description
        if estimated_price is not None:
            payload["estimated_price"] = f"{estimated_price.amount:f}"
        if priority is not None:
            payload["priority"] = ItemPriority(priority).value
        if not payload:
            raise InvalidPurchasesInputError("Provide something to change.")

        try:
            body = await self._api.update_wishlist_item(
                access_token, str(item_id), payload
            )
        except NotFoundError as exc:
            raise WishlistItemNotFoundError(
                "That wishlist item no longer exists."
            ) from exc
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_wishlist_item(
            _parse(
                DRFWishlistItemResponse,
                _as_mapping(body, "Wishlist item"),
                "Wishlist item",
            )
        )

    async def delete_item(self, access_token: str, item_id: WishlistItemId) -> Wishlist:
        """DELETE one item; answers 200 with the reloaded wishlist."""
        try:
            body = await self._api.delete_wishlist_item(access_token, str(item_id))
        except NotFoundError as exc:
            raise WishlistItemNotFoundError(
                "That wishlist item no longer exists."
            ) from exc
        return map_wishlist(
            _parse(DRFWishlistResponse, _as_mapping(body, "Wishlist"), "Wishlist")
        )

    async def mark_item_purchased(
        self, access_token: str, item_id: WishlistItemId
    ) -> Wishlist:
        """POST `mark-purchased/`; answers 200 with the **whole reloaded wishlist**.

        Same reasoning as the shopping-list equivalent: the response is the only
        place the wishlist's recomputed `status` and `completed_at` appear.
        """
        try:
            body = await self._api.mark_wishlist_item_purchased(
                access_token, str(item_id)
            )
        except NotFoundError as exc:
            raise WishlistItemNotFoundError(
                "That wishlist item no longer exists."
            ) from exc
        return map_wishlist(
            _parse(DRFWishlistResponse, _as_mapping(body, "Wishlist"), "Wishlist")
        )

    async def add_tags_to_item(
        self, access_token: str, item_id: WishlistItemId, tag_names: list[str]
    ) -> WishlistItem:
        """Attach tags by name; the API creates any that do not exist yet."""
        try:
            body = await self._api.add_wishlist_item_tags(
                access_token, str(item_id), list(tag_names)
            )
        except NotFoundError as exc:
            raise WishlistItemNotFoundError(
                "That wishlist item no longer exists."
            ) from exc
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_wishlist_item(
            _parse(
                DRFWishlistItemResponse,
                _as_mapping(body, "Wishlist item"),
                "Wishlist item",
            )
        )

    async def remove_tags_from_item(
        self, access_token: str, item_id: WishlistItemId, tag_ids: list[TagId]
    ) -> WishlistItem:
        """Detach tags by id. `DELETE` carries a JSON body, as the API expects."""
        try:
            body = await self._api.remove_wishlist_item_tags(
                access_token, str(item_id), [str(tag_id) for tag_id in tag_ids]
            )
        except NotFoundError as exc:
            raise WishlistItemNotFoundError(
                "That wishlist item no longer exists."
            ) from exc
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_wishlist_item(
            _parse(
                DRFWishlistItemResponse,
                _as_mapping(body, "Wishlist item"),
                "Wishlist item",
            )
        )


class DRFTagRepository:
    """Adapter for the shared tag catalogue, over the shared DRF client."""

    def __init__(self, api_client: DRFAPIClient) -> None:
        self._api = api_client

    async def get_all_tags(self, access_token: str) -> list[Tag]:
        """Every tag in the catalogue.

        Global, not owner-scoped, so this is a shared suggestion source rather
        than the caller's own vocabulary. An empty catalogue answers `[]`, which
        is why `_as_list` maps `None` to an empty list rather than raising.
        """
        body = await self._api.list_purchases_tags(access_token)
        return [
            map_tag(_parse(DRFTagResponse, row, "Tag")) for row in _as_list(body, "Tag")
        ]

    async def create_or_get_tag(self, access_token: str, name: str) -> Tag:
        """POST a name and return the tag, created or found.

        **The 201-vs-200 difference is intentionally not reported.** The
        backend's `CreateTag` is idempotent by name, so a caller that needed to
        know whether it had just created an entry would have to compare the
        catalogue before and after -- two reads to learn one fact the API already
        encoded in a status code that this adapter does not surface. Nothing in
        the UI needs it: tags are created as a side effect of tagging an item.
        """
        try:
            body = await self._api.create_purchases_tag(access_token, name)
        except ValidationError as exc:
            raise InvalidPurchasesInputError(_detail_from(exc)) from exc
        return map_tag(_parse(DRFTagResponse, _as_mapping(body, "Tag"), "Tag"))


def estimated_price_from_form(raw: str | None) -> EstimatedPrice | None:
    """Parse the wishlist form's price field into an `EstimatedPrice`, or `None`.

    Lives here, next to the only caller that needs it, rather than in the view,
    because it is a *transport-shape* decision -- DRF wants a decimal string, and
    an empty input means "no estimate" -- rather than presentation.

    `None` for a blank field, never `EstimatedPrice("0.00")`: the two render
    differently on the card, and a user who did not write a price did not write
    zero.

    A negative figure raises `InvalidPurchaseError` from the value object, and a
    non-numeric one raises `InvalidPurchasesInputError` here -- both are
    `DomainException`s, so the view's usual handler turns either into a flash.
    Letting `Decimal("abc")` raise its own `InvalidOperation` would not be
    caught by that handler and would take the page down for a mistyped field.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise InvalidPurchasesInputError(
            "Estimated price must be a number, such as 399.90."
        ) from exc
    return EstimatedPrice(amount)


__all__ = [
    "DRFShoppingListRepository",
    "DRFTagRepository",
    "DRFWishlistRepository",
    "estimated_price_from_form",
]
