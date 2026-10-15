"""Purchases use cases, re-exported for the composition root and the tests."""

from src.purchases.application.use_cases.shopping_lists import (
    AddItemToShoppingListUseCase,
    CreateShoppingListUseCase,
    GetActiveShoppingListsUseCase,
    GetShoppingListUseCase,
    MarkShoppingListItemPurchasedUseCase,
)
from src.purchases.application.use_cases.tags import ListTagsUseCase
from src.purchases.application.use_cases.wishlist import (
    AddItemToWishlistUseCase,
    GetWishlistUseCase,
    MarkWishlistItemPurchasedUseCase,
)

__all__ = [
    "AddItemToShoppingListUseCase",
    "AddItemToWishlistUseCase",
    "CreateShoppingListUseCase",
    "GetActiveShoppingListsUseCase",
    "GetShoppingListUseCase",
    "GetWishlistUseCase",
    "ListTagsUseCase",
    "MarkShoppingListItemPurchasedUseCase",
    "MarkWishlistItemPurchasedUseCase",
]
