"""Composition root for the purchases context.

The single place allowed to construct use cases around concrete adapters
(AGENTS.md, constraint 3). Views read the ready-made ones off `current_app` and
never assemble anything.

The auto-refresh wrapping is **not** here -- it lives in
`src/interfaces/web/app.py` beside the equivalent wrapping for every other
context, because `refreshing()` there closes over the request-scoped cookie
manager. This module's job is only to build use cases around an already-wrapped
adapter.

`attach_to` exists rather than a pile of assignments in the app factory for the
same reason `src/tasks/application/wiring.py` has one: the mapping from app
attribute to use case is a table, and a table is checkable. A view that reads
`current_app.mark_item_purchased_use_case` and an entry that says
`("mark_item_purchased_use_case", "mark_item_purchased")` cannot drift apart
without the name in one of them changing.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.purchases.application.use_cases.shopping_lists import (
    AddItemToShoppingListUseCase,
    CreateShoppingListUseCase,
    GetActiveShoppingListsUseCase,
    GetArchivedShoppingListsUseCase,
    GetShoppingListUseCase,
    MarkShoppingListItemPurchasedUseCase,
)
from src.purchases.application.use_cases.tags import ListTagsUseCase
from src.purchases.application.use_cases.wishlist import (
    AddItemToWishlistUseCase,
    GetWishlistUseCase,
    MarkWishlistItemPurchasedUseCase,
)


@dataclass(frozen=True, slots=True)
class PurchasesUseCases:
    """Every purchases use case, wired to three adapters."""

    get_active_lists: GetActiveShoppingListsUseCase
    get_archived_lists: GetArchivedShoppingListsUseCase
    get_shopping_list: GetShoppingListUseCase
    create_shopping_list: CreateShoppingListUseCase
    add_shopping_list_item: AddItemToShoppingListUseCase
    mark_shopping_list_item_purchased: MarkShoppingListItemPurchasedUseCase
    get_wishlist: GetWishlistUseCase
    add_wishlist_item: AddItemToWishlistUseCase
    mark_wishlist_item_purchased: MarkWishlistItemPurchasedUseCase
    list_tags: ListTagsUseCase

    #: App attribute name -> field name here. Views read
    #: `current_app.get_wishlist_use_case`, matching every other context.
    APP_ATTRIBUTES = (
        ("get_active_lists_use_case", "get_active_lists"),
        ("get_archived_lists_use_case", "get_archived_lists"),
        ("get_shopping_list_use_case", "get_shopping_list"),
        ("create_shopping_list_use_case", "create_shopping_list"),
        ("add_shopping_list_item_use_case", "add_shopping_list_item"),
        (
            "mark_shopping_list_item_purchased_use_case",
            "mark_shopping_list_item_purchased",
        ),
        ("get_wishlist_use_case", "get_wishlist"),
        ("add_wishlist_item_use_case", "add_wishlist_item"),
        ("mark_wishlist_item_purchased_use_case", "mark_wishlist_item_purchased"),
        ("list_tags_use_case", "list_tags"),
    )

    def attach_to(self, target: object) -> None:
        """Expose each use case as an attribute on `target` (the Flask app)."""
        for app_name, field_name in self.APP_ATTRIBUTES:
            setattr(target, app_name, getattr(self, field_name))


def build_purchases_use_cases(
    shopping_lists: object, wishlist: object, tags: object
) -> PurchasesUseCases:
    """Build the purchases use cases around three adapters.

    All three adapters are typed loosely because the caller passes the
    ``AutoRefreshingRepository`` proxy, not the adapters themselves. Typing them
    as the port protocols would be true of the adapters and false of the proxy,
    whose methods are synthesised at runtime by ``__getattr__`` -- and a static
    type that the production wiring does not satisfy is a type that gets
    suppressed rather than believed. See `src/tasks/application/wiring.py`, which
    makes the same trade for the same reason.

    Three adapters rather than one because three protocols were declared: the
    three resources have genuinely different routing (see
    `src/purchases/domain/ports.py`). Merging them would mean one class holding
    three unrelated sets of endpoints and a port that declared all of them.
    """
    return PurchasesUseCases(
        get_active_lists=GetActiveShoppingListsUseCase(shopping_lists=shopping_lists),
        get_archived_lists=GetArchivedShoppingListsUseCase(
            shopping_lists=shopping_lists
        ),
        get_shopping_list=GetShoppingListUseCase(shopping_lists=shopping_lists),
        create_shopping_list=CreateShoppingListUseCase(shopping_lists=shopping_lists),
        add_shopping_list_item=AddItemToShoppingListUseCase(
            shopping_lists=shopping_lists
        ),
        mark_shopping_list_item_purchased=MarkShoppingListItemPurchasedUseCase(
            shopping_lists=shopping_lists
        ),
        get_wishlist=GetWishlistUseCase(wishlist=wishlist),
        add_wishlist_item=AddItemToWishlistUseCase(wishlist=wishlist),
        mark_wishlist_item_purchased=MarkWishlistItemPurchasedUseCase(
            wishlist=wishlist
        ),
        list_tags=ListTagsUseCase(tags=tags),
    )


__all__ = [
    "PurchasesUseCases",
    "build_purchases_use_cases",
]
