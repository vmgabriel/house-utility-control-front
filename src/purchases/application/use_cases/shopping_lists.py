"""Use cases for shopping lists and their items.

Thin on purpose. Every rule that decides *what* a list may contain and *when* it
is complete is enforced by the backend (`apps/purchases/domain/entities.py`), and
the owner-scoping that enforces "whose list is this" is enforced by the JWT on
every route. What is left here is the checks the BFF can make without a round
trip -- blank names, quantities below one -- so the user gets a message that
names the field they typed into instead of a DRF 400 they cannot trace.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.purchases.application.validation import normalize_or_raise
from src.purchases.domain.entities import ShoppingList
from src.purchases.domain.exceptions import (
    InvalidPurchasesInputError,
    ShoppingListNotFoundError,
)
from src.purchases.domain.ports import ShoppingListRepositoryPort
from src.purchases.domain.value_objects import (
    MAX_ITEM_DETAILS_LENGTH,
    MAX_ITEM_NAME_LENGTH,
    MAX_LIST_NAME_LENGTH,
    ListPriority,
    Quantity,
    ShoppingListId,
    ShoppingListItemId,
    normalize_text,
    optional_text,
)


@dataclass(frozen=True, slots=True)
class GetActiveShoppingListsUseCase:
    """List the caller's active shopping lists."""

    shopping_lists: ShoppingListRepositoryPort

    async def execute(self, access_token: str) -> list[ShoppingList]:
        """Return the active lists, or an empty list when there are none.

        An empty list is a page to render, not a failure, so there is no `None`
        on this path -- the same reading `src/tasks` applies to a day with no
        plan.
        """
        return await self.shopping_lists.get_active_lists(access_token)


@dataclass(frozen=True, slots=True)
class GetArchivedShoppingListsUseCase:
    """List the caller's soft-deleted lists, which the overview links to."""

    shopping_lists: ShoppingListRepositoryPort

    async def execute(self, access_token: str) -> list[ShoppingList]:
        """Return the archived lists.

        A separate read from :class:`GetActiveShoppingListsUseCase` because the
        backend serves them from a different endpoint
        (`GET /shopping-lists/inactive/`): `GET /shopping-lists/` is filtered to
        active rows server-side and there is no query parameter that widens it.
        """
        return await self.shopping_lists.get_inactive_lists(access_token)


@dataclass(frozen=True, slots=True)
class GetShoppingListUseCase:
    """Fetch one list, translated so a missing list raises rather than 500s."""

    shopping_lists: ShoppingListRepositoryPort

    async def execute(self, access_token: str, list_id: ShoppingListId) -> ShoppingList:
        """Return the list, or raise :class:`ShoppingListNotFoundError`.

        The port answers `None` for a list that is missing *or* not the
        caller's, and the two are not separable: the backend answers 404 for
        both. Raising here is what lets the view word the flash "that list is no
        longer available" instead of rendering an empty checklist under a
        heading the user never had -- and, crucially, without ever telling them
        the difference between the two cases.
        """
        found = await self.shopping_lists.get_list(access_token, list_id)
        if found is None:
            raise ShoppingListNotFoundError("That shopping list is not available.")
        return found


@dataclass(frozen=True, slots=True)
class CreateShoppingListUseCase:
    """Create a shopping list from the overview's form."""

    shopping_lists: ShoppingListRepositoryPort

    async def execute(
        self,
        access_token: str,
        *,
        name: str,
        priority: ListPriority = ListPriority.MEDIUM,
    ) -> ShoppingList:
        """Validate the name locally, then create.

        `priority` defaults to ``medium`` because that is the backend's own
        default (`CreateShoppingListSerializer`), so a form that omits the select
        produces exactly what the API would have produced.
        """
        if not isinstance(priority, ListPriority):
            raise InvalidPurchasesInputError("Priority must be a known level.")
        cleaned = normalize_or_raise(
            lambda: normalize_text(
                name, limit=MAX_LIST_NAME_LENGTH, label="Shopping list name"
            )
        )
        return await self.shopping_lists.create_list(access_token, cleaned, priority)


@dataclass(frozen=True, slots=True)
class AddItemToShoppingListUseCase:
    """Add one item to a shopping list from its detail page's form."""

    shopping_lists: ShoppingListRepositoryPort

    async def execute(
        self,
        access_token: str,
        list_id: ShoppingListId,
        *,
        name: str,
        quantity: int = 1,
        details: str | None = None,
    ) -> ShoppingList:
        """Validate the item locally, then add it.

        `quantity` is coerced through :class:`Quantity`, which is what rejects a
        zero or negative figure before the request. The backend's
        `AddShoppingListItemSerializer` declares ``min_value=1``, so without this
        the user would get a 400 for typing 0 -- and 0 is a plausible thing to
        type, since it looks like "not counting this one".

        The return value is the **whole reloaded list**, because that is what the
        endpoint answers with and it is the only place the new item's id
        appears. See the port.
        """
        if not isinstance(list_id, ShoppingListId):
            raise InvalidPurchasesInputError("list_id must be a ShoppingListId.")

        cleaned_name, parsed_quantity, cleaned_details = normalize_or_raise(
            lambda: (
                normalize_text(name, limit=MAX_ITEM_NAME_LENGTH, label="Item name"),
                Quantity(quantity),
                optional_text(
                    details, limit=MAX_ITEM_DETAILS_LENGTH, label="Item details"
                ),
            )
        )

        return await self.shopping_lists.add_item(
            access_token,
            list_id,
            name=cleaned_name,
            quantity=int(parsed_quantity),
            details=cleaned_details,
        )


@dataclass(frozen=True, slots=True)
class MarkShoppingListItemPurchasedUseCase:
    """Tick one item off a shopping list."""

    shopping_lists: ShoppingListRepositoryPort

    async def execute(
        self,
        access_token: str,
        list_id: ShoppingListId,
        item_id: ShoppingListItemId,
    ) -> ShoppingList:
        """Mark the item and return the list as the backend reloaded it.

        The **reloaded list** is the return value rather than a flag, and it is
        the only authoritative answer: the same response carries the item's new
        ``status`` *and* the list's recomputed ``status``/``completed_at``, so
        the redirect-back page can say "list complete" without a second read and
        cannot get it wrong by recomputing completion locally.

        Both ids are required even though the item id alone identifies the row.
        The backend's nested item routes carry the list uuid as well and reject a
        mismatched pair with 404, so the view has to pass the id the page was
        rendered under.
        """
        if not isinstance(list_id, ShoppingListId):
            raise InvalidPurchasesInputError("list_id must be a ShoppingListId.")
        if not isinstance(item_id, ShoppingListItemId):
            raise InvalidPurchasesInputError("item_id must be a ShoppingListItemId.")
        return await self.shopping_lists.mark_item_purchased(
            access_token, list_id, item_id
        )


__all__ = [
    "AddItemToShoppingListUseCase",
    "CreateShoppingListUseCase",
    "GetActiveShoppingListsUseCase",
    "GetArchivedShoppingListsUseCase",
    "GetShoppingListUseCase",
    "MarkShoppingListItemPurchasedUseCase",
]
