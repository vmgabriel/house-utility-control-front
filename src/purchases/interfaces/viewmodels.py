"""ViewModels for the purchases templates.

The only place purchases domain objects become strings, labels, and CSS classes.
Templates never touch domain objects, which keeps presentation out of the inner
layers and makes every rendering decision testable without a request context --
the same arrangement as `src/tasks/interfaces/viewmodels.py` and
`src/rentals/interfaces/viewmodels.py`.

Three decisions worth stating, because each is a place the obvious version is
wrong:

**Money goes through the profile's currency.** `EstimatedPrice` is a bare amount
with no currency attached, and the currency is a *profile* preference that
`purchases` may not import (AGENTS.md, constraint 1). So a
:class:`CurrencyPreference` from the shared kernel is passed in, exactly as
`src/rentals` does. Formatting here rather than in the template means a wishlist
card and a rentals bill cannot disagree about how money is written.

**An absent price is not zero.** `estimated_price` is `None` when the user gave
no figure, and the card renders a dash for it. Rendering ``0.00`` would claim
the user estimated the item at nothing, which is a different statement and a
false one.

**Badge colours live in one table.** `must_have` is red, `wanted` is yellow,
`nice_to_have` is blue -- specified once here so a priority cannot be
recoloured on one card and not another, and so an unrecognised priority falls
back to the neutral badge instead of rendering nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.purchases.domain.entities import (
    ShoppingList,
    ShoppingListItem,
    Tag,
    Wishlist,
    WishlistItem,
)
from src.purchases.domain.value_objects import ItemPriority, ItemStatus, ListPriority
from src.shared.utils.currency import CurrencyPreference

LIST_PRIORITY_LABELS: dict[ListPriority, str] = {
    ListPriority.HIGH: "High",
    ListPriority.MEDIUM: "Medium",
    ListPriority.LOW: "Low",
}

LIST_PRIORITY_STYLES: dict[ListPriority, str] = {
    ListPriority.HIGH: "bg-red-100 text-red-700",
    ListPriority.MEDIUM: "bg-yellow-100 text-yellow-800",
    ListPriority.LOW: "bg-gray-100 text-gray-700",
}

ITEM_PRIORITY_LABELS: dict[ItemPriority, str] = {
    ItemPriority.MUST_HAVE: "Must have",
    ItemPriority.WANTED: "Wanted",
    ItemPriority.NICE_TO_HAVE: "Nice to have",
}

#: Red / yellow / blue, as the brief specifies. Red for `must_have` because a
#: must-have that reads as calm is not a must-have; blue for `nice_to_have`
#: because it is the only one of the three with nothing to act on.
ITEM_PRIORITY_STYLES: dict[ItemPriority, str] = {
    ItemPriority.MUST_HAVE: "bg-red-100 text-red-700",
    ItemPriority.WANTED: "bg-yellow-100 text-yellow-800",
    ItemPriority.NICE_TO_HAVE: "bg-blue-100 text-blue-800",
}

#: Applied to a purchased card so a bought item reads as done at a glance
#: without hiding the text it contains.
PURCHASED_STYLE = "opacity-60"

#: Rendered in place of an absent `estimated_price`. A dash reads as "absent" in
#: one character; "Not estimated" costs the width the priority badge needs.
NO_ESTIMATE_LABEL = "—"

#: The glyph on a card with no `image_link`. The API's `image_link` is nullable
#: and most wishlist items will not have one, so this is the common case and not
#: a fallback worth styling as an error.
NO_IMAGE_GLYPH = "🛍"


def _format_timestamp(value: datetime | None) -> str:
    """Render a backend timestamp as an ISO date.

    Deliberately not locale-formatted, matching every other viewmodel in this
    app: the rest of the product renders ISO strings and a mixed format here
    would be the only one of its kind.
    """
    return value.date().isoformat() if value is not None else ""


def _format_price(price, currency: CurrencyPreference) -> str:
    """Format an `EstimatedPrice` for a card, or a dash when there is none.

    `None` becomes :data:`NO_ESTIMATE_LABEL` and **never** ``0.00``: the API's
    `estimated_price` is nullable, and "not estimated" and "estimated at
    nothing" are different claims about an item.
    """
    if price is None:
        return NO_ESTIMATE_LABEL
    return currency.format(price.amount)


@dataclass(frozen=True, slots=True)
class TagViewModel:
    """One catalogue tag on an item."""

    id: str
    name: str

    @classmethod
    def from_domain(cls, tag: Tag) -> TagViewModel:
        return cls(id=str(tag.id), name=tag.name)


def _tags(tags: list[Tag]) -> list[TagViewModel]:
    return [TagViewModel.from_domain(tag) for tag in tags]


@dataclass(frozen=True, slots=True)
class ShoppingListItemViewModel:
    """One checklist row on the list detail page.

    `mark_purchased_url` is built here rather than in the template so the form
    action and the row's state come from one place -- a route built in Jinja and
    a route built in Python are two definitions that can disagree.
    """

    id: str
    name: str
    quantity: int
    details: str | None
    is_purchased: bool
    has_details: bool
    tags: list[TagViewModel]
    mark_purchased_url: str

    @classmethod
    def from_domain(
        cls, item: ShoppingListItem, mark_purchased_url: str
    ) -> ShoppingListItemViewModel:
        return cls(
            id=str(item.id),
            name=item.name,
            quantity=int(item.quantity),
            details=item.details,
            is_purchased=item.is_purchased,
            has_details=bool(item.details),
            tags=_tags(item.tags),
            mark_purchased_url=mark_purchased_url,
        )


@dataclass(frozen=True, slots=True)
class ShoppingListCardViewModel:
    """One list, as a card on the overview."""

    id: str
    name: str
    priority_label: str
    priority_style: str
    item_count: int
    purchased_count: int
    pending_count: int
    is_completed: bool
    #: 0-100, for a progress bar's `width` style. An integer rather than the raw
    #: ratio so the template never does arithmetic, and so the bar and the counts
    #: beside it are derived from the same item list in the same pass.
    progress_percent: int
    detail_url: str
    completed_on: str

    @classmethod
    def from_domain(
        cls, shopping_list: ShoppingList, detail_url: str
    ) -> ShoppingListCardViewModel:
        purchased = len(shopping_list.purchased_items)
        total = len(shopping_list.items)
        return cls(
            id=str(shopping_list.id),
            name=shopping_list.name,
            priority_label=LIST_PRIORITY_LABELS[shopping_list.priority],
            priority_style=LIST_PRIORITY_STYLES[shopping_list.priority],
            item_count=total,
            purchased_count=purchased,
            pending_count=total - purchased,
            # The backend's own recomputation, read off the list's `status`
            # rather than re-derived from the items: it applies the rule that an
            # empty list is never completed, and a locally derived figure would
            # have to re-implement it to agree.
            is_completed=shopping_list.status.is_purchased,
            progress_percent=round(shopping_list.progress_ratio * 100),
            detail_url=detail_url,
            completed_on=_format_timestamp(shopping_list.completed_at),
        )


@dataclass(frozen=True, slots=True)
class ShoppingListsViewModel:
    """The overview page: active lists, and the archived ones behind a link."""

    cards: list[ShoppingListCardViewModel]
    archived: list[ShoppingListCardViewModel]
    is_empty: bool
    is_archived_empty: bool
    active_total: int
    priorities: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        lists: list[ShoppingList],
        archived: list[ShoppingList],
        detail_url_for,
    ) -> ShoppingListsViewModel:
        cards = [
            ShoppingListCardViewModel.from_domain(
                shopping_list, detail_url_for(shopping_list)
            )
            for shopping_list in lists
        ]
        archived_cards = [
            ShoppingListCardViewModel.from_domain(
                shopping_list, detail_url_for(shopping_list)
            )
            for shopping_list in archived
        ]
        return cls(
            cards=cards,
            archived=archived_cards,
            is_empty=not cards,
            is_archived_empty=not archived_cards,
            active_total=sum(card.item_count for card in cards),
            priorities=[
                (member.value, LIST_PRIORITY_LABELS[member]) for member in ListPriority
            ],
        )


@dataclass(frozen=True, slots=True)
class ShoppingListDetailViewModel:
    """The list detail page: a checklist, and the form that adds to it."""

    id: str
    name: str
    priority_label: str
    priority_style: str
    is_completed: bool
    item_count: int
    purchased_count: int
    pending_count: int
    #: Pending first, then purchased. The regrouping is a *presentation* choice
    #: and it is the only ordering the page applies: within each group the
    #: backend's own order is kept, so nothing here can silently re-sort the
    #: user's list.
    pending_items: list[ShoppingListItemViewModel]
    purchased_items: list[ShoppingListItemViewModel]
    is_empty: bool
    completed_on: str
    back_url: str

    @classmethod
    def build(
        cls,
        shopping_list: ShoppingList,
        *,
        item_view_model_for,
        back_url: str,
    ) -> ShoppingListDetailViewModel:
        pending = [item_view_model_for(item) for item in shopping_list.pending_items]
        purchased = [
            item_view_model_for(item) for item in shopping_list.purchased_items
        ]
        total = len(shopping_list.items)
        done = len(purchased)
        return cls(
            id=str(shopping_list.id),
            name=shopping_list.name,
            priority_label=LIST_PRIORITY_LABELS[shopping_list.priority],
            priority_style=LIST_PRIORITY_STYLES[shopping_list.priority],
            is_completed=shopping_list.status.is_purchased,
            item_count=total,
            purchased_count=done,
            pending_count=total - done,
            pending_items=pending,
            purchased_items=purchased,
            is_empty=total == 0,
            completed_on=_format_timestamp(shopping_list.completed_at),
            back_url=back_url,
        )


@dataclass(frozen=True, slots=True)
class WishlistItemViewModel:
    """One wishlist card.

    `image_link` is already http(s)-validated by the domain, and is `None` for
    most items -- the API's field is nullable and a user typing a wishlist does
    not usually have a product photo to hand.
    """

    id: str
    name: str
    link: str | None
    image_link: str | None
    has_image: bool
    description: str | None
    has_description: bool
    estimated_price_label: str
    #: The raw figure, for the form inputs and for tests. `None` when there is no
    #: estimate -- which is why the label above may be a dash.
    estimated_price_value: str
    priority_label: str
    priority_style: str
    is_purchased: bool
    card_style: str
    tags: list[TagViewModel]
    mark_purchased_url: str

    @classmethod
    def from_domain(
        cls,
        item: WishlistItem,
        *,
        currency: CurrencyPreference,
        mark_purchased_url: str,
    ) -> WishlistItemViewModel:
        raw = item.estimated_price_amount
        return cls(
            id=str(item.id),
            name=item.name,
            link=item.link,
            image_link=item.image_link,
            has_image=item.image_link is not None,
            description=item.description,
            has_description=bool(item.description),
            estimated_price_label=_format_price(item.estimated_price, currency),
            # `format(..., "f")` and not `str()`: the normalized form of 1000 is
            # "1E+3", which is not a value an `<input type="number">` will accept.
            estimated_price_value="" if raw is None else f"{raw:f}",
            priority_label=ITEM_PRIORITY_LABELS[item.priority],
            priority_style=ITEM_PRIORITY_STYLES[item.priority],
            is_purchased=item.is_purchased,
            card_style=PURCHASED_STYLE if item.is_purchased else "",
            tags=_tags(item.tags),
            mark_purchased_url=mark_purchased_url,
        )


@dataclass(frozen=True, slots=True)
class WishlistViewModel:
    """The wishlist page: a card grid, grouped pending-first."""

    name: str
    is_completed: bool
    item_count: int
    purchased_count: int
    pending_count: int
    pending_items: list[WishlistItemViewModel]
    purchased_items: list[WishlistItemViewModel]
    is_empty: bool
    completed_on: str
    priorities: list[tuple[str, str]]

    @classmethod
    def build(cls, wishlist: Wishlist, *, item_view_model_for) -> WishlistViewModel:
        pending = [item_view_model_for(item) for item in wishlist.pending_items]
        purchased = [item_view_model_for(item) for item in wishlist.purchased_items]
        total = len(wishlist.items)
        done = len(purchased)
        return cls(
            name=wishlist.name,
            is_completed=wishlist.status.is_purchased,
            item_count=total,
            purchased_count=done,
            pending_count=total - done,
            pending_items=pending,
            purchased_items=purchased,
            is_empty=total == 0,
            completed_on=_format_timestamp(wishlist.completed_at),
            priorities=[
                (member.value, ITEM_PRIORITY_LABELS[member]) for member in ItemPriority
            ],
        )


__all__ = [
    "ITEM_PRIORITY_LABELS",
    "ITEM_PRIORITY_STYLES",
    "LIST_PRIORITY_LABELS",
    "LIST_PRIORITY_STYLES",
    "NO_ESTIMATE_LABEL",
    "NO_IMAGE_GLYPH",
    "PURCHASED_STYLE",
    "ItemStatus",
    "ShoppingListCardViewModel",
    "ShoppingListDetailViewModel",
    "ShoppingListItemViewModel",
    "ShoppingListsViewModel",
    "TagViewModel",
    "WishlistItemViewModel",
    "WishlistViewModel",
]
