"""ViewModel tests: the decisions that are presentation, not domain.

Pure functions over frozen domain objects, so each one is checkable without a
request context. The rules asserted here are the ones a template cannot express
and cannot be trusted to: a badge colour, a currency format, the difference
between "no estimate" and "zero", and the grouping of a checklist.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

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
    ItemStatus,
    ListPriority,
    Quantity,
    ShoppingListId,
    ShoppingListItemId,
    TagId,
    WishlistId,
    WishlistItemId,
)
from src.purchases.interfaces.viewmodels import (
    ITEM_PRIORITY_STYLES,
    LIST_PRIORITY_STYLES,
    NO_ESTIMATE_LABEL,
    ShoppingListCardViewModel,
    ShoppingListDetailViewModel,
    ShoppingListItemViewModel,
    ShoppingListsViewModel,
    WishlistItemViewModel,
    WishlistViewModel,
)
from src.shared.utils.currency import CurrencyPreference

USD_EN = CurrencyPreference(code="USD", language="en")
BRL_PT = CurrencyPreference(code="BRL", language="pt-BR")


def _item(purchased: bool = False, quantity: int = 1, name: str = "Milk"):
    return ShoppingListItem(
        id=ShoppingListItemId(uuid4()),
        name=name,
        quantity=Quantity(quantity),
        status=ItemStatus.PURCHASED if purchased else ItemStatus.PENDING,
        details="Semi-skimmed" if not purchased else None,
        tags=[Tag(id=TagId(uuid4()), name="Home", slug="home")],
    )


def _list(items=None, status=ItemStatus.PENDING, **overrides):
    fields = {
        "id": ShoppingListId(uuid4()),
        "name": "Groceries",
        "priority": ListPriority.MEDIUM,
        "status": status,
        "items": list(items or []),
    }
    fields.update(overrides)
    return ShoppingList(**fields)


def _wish_item(price=None, priority=ItemPriority.WANTED, purchased=False):
    return WishlistItem(
        id=WishlistItemId(uuid4()),
        name="Espresso machine",
        priority=priority,
        status=ItemStatus.PURCHASED if purchased else ItemStatus.PENDING,
        link="https://example.com/m",
        image_link="https://example.com/m.png",
        description="For the kitchen",
        estimated_price=None if price is None else EstimatedPrice(price),
    )


def _wishlist(items=None, status=ItemStatus.PENDING):
    return Wishlist(
        id=WishlistId(uuid4()),
        name="My Wishlist",
        status=status,
        items=list(items or []),
    )


# --------------------------------------------------------------------------- #
# Checklist rows
# --------------------------------------------------------------------------- #
class TestShoppingListItemViewModel:
    def test_carries_the_mark_purchased_url_it_was_given(self):
        url = "/purchases/lists/abc/items/def/mark-purchased/"
        vm = ShoppingListItemViewModel.from_domain(_item(), url)
        assert vm.mark_purchased_url == url

    def test_exposes_the_quantity_as_a_plain_int(self):
        # The template multiplies it with `&times;`, which needs a number rather
        # than a value object's `__str__`.
        assert (
            ShoppingListItemViewModel.from_domain(_item(quantity=3), "/x").quantity == 3
        )

    def test_absent_details_are_flagged_rather_than_rendered_as_none(self):
        # A bought row is the `_item(purchased=True)` fixture, whose `details` is
        # None; the template branches on the flag so it never renders the word
        # "None" or an empty paragraph.
        with_details = ShoppingListItemViewModel.from_domain(_item(), "/x")
        without = ShoppingListItemViewModel.from_domain(_item(purchased=True), "/x")

        assert with_details.has_details is True
        assert with_details.details == "Semi-skimmed"
        assert without.has_details is False
        assert without.details is None

    def test_tags_are_flattened_to_id_and_name(self):
        # The id travels as text because that is what goes into a form field and
        # a `data-` attribute; parsing it back is what proves it survived.
        vm = ShoppingListItemViewModel.from_domain(_item(), "/x")
        assert vm.tags[0].name == "Home"
        assert UUID(vm.tags[0].id)


# --------------------------------------------------------------------------- #
# Overview cards
# --------------------------------------------------------------------------- #
class TestShoppingListCardViewModel:
    def test_counts_pending_and_purchased_from_the_items(self):
        card = ShoppingListCardViewModel.from_domain(
            _list([_item(True), _item(), _item()]), "/detail"
        )
        assert card.item_count == 3
        assert card.purchased_count == 1
        assert card.pending_count == 2

    def test_progress_percent_is_the_share_of_items_bought(self):
        # Derived in one pass from the same item list the page renders, so the
        # bar and the counts beside it cannot disagree.
        card = ShoppingListCardViewModel.from_domain(
            _list([_item(True), _item()]), "/detail"
        )
        assert card.progress_percent == 50

    def test_an_empty_list_reports_no_progress_rather_than_a_division_error(self):
        card = ShoppingListCardViewModel.from_domain(_list([]), "/detail")
        assert card.progress_percent == 0
        assert card.item_count == 0

    def test_completion_is_read_from_the_backend_status(self):
        card = ShoppingListCardViewModel.from_domain(
            _list([_item(True)], status=ItemStatus.PURCHASED), "/detail"
        )
        assert card.is_completed is True

    def test_the_priority_badge_label_and_colour_come_from_one_table(self):
        card = ShoppingListCardViewModel.from_domain(
            _list(priority=ListPriority.HIGH), "/detail"
        )
        assert card.priority_label == "High"
        assert card.priority_style == LIST_PRIORITY_STYLES[ListPriority.HIGH]

    @pytest.mark.parametrize(
        ("priority", "colour"),
        [
            (ListPriority.HIGH, "bg-red-100 text-red-700"),
            (ListPriority.MEDIUM, "bg-yellow-100 text-yellow-800"),
            (ListPriority.LOW, "bg-gray-100 text-gray-700"),
        ],
    )
    def test_each_list_priority_gets_its_own_colour(self, priority, colour):
        card = ShoppingListCardViewModel.from_domain(
            _list(priority=priority), "/detail"
        )
        assert card.priority_style == colour

    def test_a_completion_date_is_rendered_as_an_iso_date(self):
        from datetime import UTC, datetime

        card = ShoppingListCardViewModel.from_domain(
            _list(
                status=ItemStatus.PURCHASED,
                completed_at=datetime(2026, 10, 5, 14, 31, tzinfo=UTC),
            ),
            "/detail",
        )
        assert card.completed_on == "2026-10-05"


class TestShoppingListsViewModel:
    def test_an_empty_page_says_so(self):
        vm = ShoppingListsViewModel.build([], [], detail_url_for=lambda s: "/x")
        assert vm.is_empty is True
        assert vm.is_archived_empty is True

    def test_active_total_counts_items_not_lists(self):
        # The only figure on the overview the user can act on: "do I have to go
        # to the shop?" is about items.
        vm = ShoppingListsViewModel.build(
            [_list([_item(), _item()]), _list([_item()])],
            [],
            detail_url_for=lambda s: "/x",
        )
        assert vm.active_total == 3
        assert len(vm.cards) == 2

    def test_archived_lists_are_a_separate_list_not_mixed_in(self):
        vm = ShoppingListsViewModel.build(
            [_list()], [_list(is_active=False)], detail_url_for=lambda s: "/x"
        )
        assert len(vm.cards) == 1
        assert len(vm.archived) == 1
        assert vm.is_archived_empty is False

    def test_offers_every_priority_to_the_create_form(self):
        vm = ShoppingListsViewModel.build([], [], detail_url_for=lambda s: "/x")
        assert [value for value, _ in vm.priorities] == ["high", "medium", "low"]


# --------------------------------------------------------------------------- #
# Checklist page
# --------------------------------------------------------------------------- #
class TestShoppingListDetailViewModel:
    @staticmethod
    def _build(shopping_list):
        return ShoppingListDetailViewModel.build(
            shopping_list,
            item_view_model_for=lambda item: ShoppingListItemViewModel.from_domain(
                item, "/mark"
            ),
            back_url="/purchases/",
        )

    def test_groups_pending_first_and_bought_second(self):
        vm = self._build(_list([_item(True), _item(), _item(True)]))
        assert [row.name for row in vm.pending_items] == ["Milk"]
        assert len(vm.purchased_items) == 2

    def test_the_counts_agree_with_the_rows_above_them(self):
        # One derivation, one pass: a summary that said "2 left" above three
        # pending rows would be the exact bug the shared entity prevents.
        vm = self._build(_list([_item(True), _item(), _item()]))
        assert vm.purchased_count == 1
        assert vm.pending_count == 2
        assert len(vm.pending_items) == 2

    def test_an_empty_list_renders_its_empty_state(self):
        vm = self._build(_list([]))
        assert vm.is_empty is True
        assert vm.pending_items == []

    def test_completion_is_read_from_the_backend_status(self):
        vm = self._build(_list([_item(True)], status=ItemStatus.PURCHASED))
        assert vm.is_completed is True


# --------------------------------------------------------------------------- #
# Wishlist cards
# --------------------------------------------------------------------------- #
class TestWishlistItemViewModel:
    def test_formats_the_price_in_the_profile_currency(self):
        vm = WishlistItemViewModel.from_domain(
            _wish_item(price="399.90"), currency=USD_EN, mark_purchased_url="/x"
        )
        assert vm.estimated_price_label == "$399.90"

    def test_uses_the_profile_locale_rather_than_the_invariant_form(self):
        # pt-BR groups with a period and separates the fraction with a comma, so
        # a wishlist card and a rentals bill cannot disagree about money.
        vm = WishlistItemViewModel.from_domain(
            _wish_item(price="1234.50"), currency=BRL_PT, mark_purchased_url="/x"
        )
        # BRL is not in the "trailing space" set, so the glyph sits flush against
        # the figure -- "R$1.234,50". The test pins the shared kernel's rule
        # rather than re-deciding it here.
        assert vm.estimated_price_label == "R$1.234,50"

    def test_an_absent_price_renders_as_a_dash_not_as_zero(self):
        vm = WishlistItemViewModel.from_domain(
            _wish_item(price=None), currency=USD_EN, mark_purchased_url="/x"
        )
        assert vm.estimated_price_label == NO_ESTIMATE_LABEL
        assert "$0.00" not in vm.estimated_price_label

    def test_a_zero_price_still_renders_as_a_price(self):
        # "Free" is a real answer; collapsing it into the dash would lose it.
        vm = WishlistItemViewModel.from_domain(
            _wish_item(price="0.00"), currency=USD_EN, mark_purchased_url="/x"
        )
        assert vm.estimated_price_label == "$0.00"

    def test_the_input_value_is_a_bare_two_decimal_number(self):
        # `str(Decimal("1234.50"))` normalises to "1.2345E+3", which an
        # `<input type="number">` rejects outright.
        vm = WishlistItemViewModel.from_domain(
            _wish_item(price="1234.50"), currency=USD_EN, mark_purchased_url="/x"
        )
        assert vm.estimated_price_value == "1234.50"

    def test_an_absent_price_leaves_the_input_empty(self):
        vm = WishlistItemViewModel.from_domain(
            _wish_item(price=None), currency=USD_EN, mark_purchased_url="/x"
        )
        assert vm.estimated_price_value == ""

    @pytest.mark.parametrize(
        ("priority", "label", "colour"),
        [
            # The three colours the brief specifies, pinned so a rebrand cannot
            # recolour one priority and quietly leave the other two.
            (ItemPriority.MUST_HAVE, "Must have", "bg-red-100 text-red-700"),
            (ItemPriority.WANTED, "Wanted", "bg-yellow-100 text-yellow-800"),
            (
                ItemPriority.NICE_TO_HAVE,
                "Nice to have",
                "bg-blue-100 text-blue-800",
            ),
        ],
    )
    def test_each_priority_gets_its_label_and_colour(self, priority, label, colour):
        vm = WishlistItemViewModel.from_domain(
            _wish_item(priority=priority), currency=USD_EN, mark_purchased_url="/x"
        )
        assert vm.priority_label == label
        assert vm.priority_style == colour
        assert vm.priority_style == ITEM_PRIORITY_STYLES[priority]

    def test_a_missing_image_is_flagged_rather_than_rendered_as_none(self):
        item = WishlistItem(
            id=WishlistItemId(uuid4()),
            name="Lamp",
            image_link=None,
        )
        vm = WishlistItemViewModel.from_domain(
            item, currency=USD_EN, mark_purchased_url="/x"
        )
        assert vm.has_image is False
        assert vm.image_link is None

    def test_a_bought_item_is_dimmed(self):
        vm = WishlistItemViewModel.from_domain(
            _wish_item(purchased=True), currency=USD_EN, mark_purchased_url="/x"
        )
        assert vm.is_purchased is True
        assert vm.card_style != ""

    def test_a_wanted_item_is_not_dimmed(self):
        vm = WishlistItemViewModel.from_domain(
            _wish_item(), currency=USD_EN, mark_purchased_url="/x"
        )
        assert vm.card_style == ""


class TestWishlistViewModel:
    @staticmethod
    def _build(wishlist):
        return WishlistViewModel.build(
            wishlist,
            item_view_model_for=lambda item: WishlistItemViewModel.from_domain(
                item, currency=USD_EN, mark_purchased_url="/x"
            ),
        )

    def test_groups_pending_first_and_bought_second(self):
        vm = self._build(_wishlist([_wish_item(purchased=True), _wish_item()]))
        assert len(vm.pending_items) == 1
        assert len(vm.purchased_items) == 1

    def test_the_counts_agree_with_the_cards_above_them(self):
        vm = self._build(_wishlist([_wish_item(), _wish_item(), _wish_item()]))
        assert vm.item_count == 3
        assert vm.pending_count == 3
        assert len(vm.pending_items) == 3

    def test_an_empty_wishlist_renders_its_empty_state(self):
        vm = self._build(_wishlist([]))
        assert vm.is_empty is True
        assert vm.is_completed is False

    def test_offers_every_priority_to_the_add_form(self):
        vm = self._build(_wishlist([]))
        assert [value for value, _ in vm.priorities] == [
            "must_have",
            "wanted",
            "nice_to_have",
        ]


def test_currency_formatting_does_not_go_through_a_float():
    """The whole reason `CurrencyPreference` exists for this context.

    `EstimatedPrice` is a `Decimal` and a float round trip would turn 399.90 into
    399.8999999... before the formatting ever saw it. Asserting the price the card
    renders is the price the API sent is the check.
    """
    price = EstimatedPrice("399.90")
    assert price.amount == Decimal("399.90")
    assert USD_EN.format(price.amount) == "$399.90"
