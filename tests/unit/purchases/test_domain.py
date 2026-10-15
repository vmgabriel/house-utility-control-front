"""Domain-layer tests: value objects and the entity properties.

Everything here is pure Python with no mocks, which is the point of the
framework-free core (AGENTS.md, constraint 2). If these need a fixture to run,
the domain has started depending on something it should not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.purchases.domain.entities import (
    ShoppingList,
    ShoppingListItem,
    Tag,
    Wishlist,
    WishlistItem,
)
from src.purchases.domain.exceptions import InvalidPurchaseError
from src.purchases.domain.value_objects import (
    MAX_QUANTITY,
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


# --------------------------------------------------------------------------- #
# The wire values. These are the backend's, read off ITEM_STATUS_CHOICES and
# WISHLIST_PRIORITY_CHOICES, and asserting them is how a rename on the backend
# side fails here rather than silently changing what a checklist renders.
# --------------------------------------------------------------------------- #
class TestEnumWireValues:
    def test_item_status_values_match_the_api(self):
        assert ItemStatus.PENDING.value == "pending"
        assert ItemStatus.PURCHASED.value == "purchased"

    def test_list_priority_values_match_the_api(self):
        assert [member.value for member in ListPriority] == ["high", "medium", "low"]

    def test_item_priority_values_match_the_api(self):
        assert [member.value for member in ItemPriority] == [
            "must_have",
            "wanted",
            "nice_to_have",
        ]

    def test_status_is_purchased_only_for_purchased(self):
        assert ItemStatus.PURCHASED.is_purchased is True
        assert ItemStatus.PENDING.is_purchased is False

    def test_the_enums_are_str_subclasses_so_they_serialise_directly(self):
        # `(str, Enum)` rather than `StrEnum`, deliberately: AGENTS.md warns
        # against letting Ruff rewrite this shape, because the members go
        # straight to the DRF JSON API from the infrastructure layer.
        assert isinstance(ItemStatus.PENDING, str)
        assert ItemStatus.PURCHASED == "purchased"


# --------------------------------------------------------------------------- #
# EstimatedPrice
# --------------------------------------------------------------------------- #
class TestEstimatedPrice:
    def test_accepts_zero_because_free_is_a_real_answer(self):
        assert EstimatedPrice("0.00").amount == Decimal("0.00")

    def test_accepts_a_string_decimal_because_drf_sends_strings(self):
        # `COERCE_DECIMAL_TO_STRING` is DRF's default, so the API sends
        # "399.90" rather than 399.9. The value object has to take that as-is.
        assert EstimatedPrice("399.90").amount == Decimal("399.90")

    def test_preserves_the_two_decimal_precision_the_api_sends(self):
        # Not 399.9: quantising here would lose the guarantee the serialiser
        # gives, and the card would render 399.9 next to the API's 399.90.
        assert str(EstimatedPrice("399.90")) == "399.90"

    def test_rejects_a_negative_amount(self):
        with pytest.raises(InvalidPurchaseError, match="cannot be negative"):
            EstimatedPrice("-1.00")

    def test_rejects_a_value_above_the_backend_maximum(self):
        with pytest.raises(InvalidPurchaseError, match="cannot exceed"):
            EstimatedPrice("99999999999.99")

    def test_rejects_text(self):
        with pytest.raises(InvalidPurchaseError, match="valid decimal"):
            EstimatedPrice("not-a-price")

    def test_rejects_infinity_rather_than_storing_it(self):
        with pytest.raises(InvalidPurchaseError, match="finite"):
            EstimatedPrice("Infinity")

    def test_rejects_a_boolean(self):
        # bool is an int subclass, so without this guard `True` would silently
        # become Decimal(1) and price a wishlist item at one currency unit.
        with pytest.raises(InvalidPurchaseError, match="not a boolean"):
            EstimatedPrice(True)

    def test_is_frozen(self):
        price = EstimatedPrice("10.00")
        with pytest.raises(AttributeError):
            price.amount = Decimal("20.00")


# --------------------------------------------------------------------------- #
# Quantity
# --------------------------------------------------------------------------- #
class TestQuantity:
    def test_accepts_one(self):
        assert int(Quantity(1)) == 1

    def test_rejects_zero(self):
        # Zero is plausible to type ("not counting this one") and the backend's
        # `AddShoppingListItemSerializer` declares min_value=1, so the refusal
        # happens here with a message naming the field.
        with pytest.raises(InvalidPurchaseError, match="less than one"):
            Quantity(0)

    def test_rejects_a_negative_quantity(self):
        with pytest.raises(InvalidPurchaseError, match="less than one"):
            Quantity(-3)

    def test_rejects_above_the_backend_maximum(self):
        with pytest.raises(InvalidPurchaseError, match="cannot exceed"):
            Quantity(MAX_QUANTITY + 1)

    def test_rejects_a_non_integer(self):
        with pytest.raises(InvalidPurchaseError, match="whole number"):
            Quantity("2")

    def test_rejects_a_boolean(self):
        with pytest.raises(InvalidPurchaseError, match="whole number"):
            Quantity(True)


# --------------------------------------------------------------------------- #
# Identity value objects
# --------------------------------------------------------------------------- #
class TestIdentifiers:
    @pytest.mark.parametrize(
        "cls",
        [ShoppingListId, ShoppingListItemId, WishlistId, WishlistItemId, TagId],
    )
    def test_accepts_a_uuid_string_and_keeps_the_text_form(self, cls):
        # Flask's `<uuid:...>` converter hands over a `UUID`, the mappers hand
        # over a string, and templates need `str()`. Both paths must land on the
        # same value or a rendered URL would not match the id in the path.
        raw = str(uuid4())
        assert str(cls(raw)) == raw

    @pytest.mark.parametrize(
        "cls",
        [ShoppingListId, ShoppingListItemId, WishlistId, WishlistItemId, TagId],
    )
    def test_rejects_something_that_is_not_a_uuid(self, cls):
        with pytest.raises(InvalidPurchaseError, match="UUID"):
            cls("not-a-uuid")


# --------------------------------------------------------------------------- #
# Text and URL normalisation
# --------------------------------------------------------------------------- #
class TestNormalisation:
    def test_collapses_whitespace_so_the_render_matches_what_was_stored(self):
        assert normalize_text("  Weekly   groceries ", limit=200, label="Name") == (
            "Weekly groceries"
        )

    def test_rejects_a_blank_name(self):
        with pytest.raises(InvalidPurchaseError, match="cannot be empty"):
            normalize_text("   ", limit=200, label="Name")

    def test_rejects_a_name_over_the_limit(self):
        with pytest.raises(InvalidPurchaseError, match="cannot exceed"):
            normalize_text("x" * 201, limit=200, label="Name")

    def test_optional_text_maps_blank_to_none(self):
        # The backend's `_optional_str` does the same: a cleared form field
        # means `None`, not the empty string.
        assert optional_text("   ", limit=10, label="Notes") is None
        assert optional_text(None, limit=10, label="Notes") is None

    def test_optional_text_keeps_real_content(self):
        assert (
            optional_text(" Semi-skimmed ", limit=50, label="Notes") == "Semi-skimmed"
        )

    @pytest.mark.parametrize(
        "url", ["https://example.com/x", "http://example.com", "HTTPS://EXAMPLE.COM"]
    )
    def test_accepts_http_and_https(self, url):
        assert optional_url(url, label="Link") == url

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",  # the one that must never reach an `href`
            "data:text/html,<script>alert(1)</script>",
            "//example.com/x",  # protocol-relative, so not an http(s) URL
            "example.com",
            "ftp://example.com",
        ],
    )
    def test_refuses_anything_that_is_not_http_or_https(self, url):
        # Both values end up in a template `href` or `src`, so a URL that is not
        # http(s) is an injection the browser would execute. Refusing it here is
        # the half of the check a browser cannot be relied on to apply.
        with pytest.raises(InvalidPurchaseError, match="http or https"):
            optional_url(url, label="Link")

    def test_maps_a_blank_url_to_none(self):
        assert optional_url("  ", label="Link") is None


# --------------------------------------------------------------------------- #
# completed_at
# --------------------------------------------------------------------------- #
class TestParseCompletedAt:
    def test_none_stays_none(self):
        # A freshly created list has `completed_at: null`, and so does every
        # list that has never been completed. AGENTS.md rule 7: the spec and the
        # runtime disagree about exactly this field.
        assert parse_completed_at(None) is None

    def test_parses_a_zulu_timestamp_as_utc(self):
        parsed = parse_completed_at("2026-10-05T14:31:12.480000Z")
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.astimezone(UTC).hour == 14

    def test_rejects_nonsense_with_a_domain_error(self):
        # Not a bare ValueError: the view's `except DomainException` would not
        # catch one, so a malformed stamp would be a 500 instead of a message.
        with pytest.raises(InvalidPurchaseError, match="valid date"):
            parse_completed_at("the day before yesterday")


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #
def _tag(name: str = "Home") -> Tag:
    return Tag(id=TagId(uuid4()), name=name, slug=name.lower())


def _list_item(purchased: bool = False, quantity: int = 1) -> ShoppingListItem:
    return ShoppingListItem(
        id=ShoppingListItemId(uuid4()),
        name="Milk",
        quantity=Quantity(quantity),
        status=ItemStatus.PURCHASED if purchased else ItemStatus.PENDING,
        tags=[_tag()],
    )


def _list(status: ItemStatus, items: list[ShoppingListItem]) -> ShoppingList:
    return ShoppingList(
        id=ShoppingListId(uuid4()),
        name="Groceries",
        priority=ListPriority.MEDIUM,
        status=status,
        items=items,
        tags=[_tag()],
    )


def _wishlist_item(purchased: bool = False, price: str | None = None) -> WishlistItem:
    return WishlistItem(
        id=WishlistItemId(uuid4()),
        name="Espresso machine",
        priority=ItemPriority.MUST_HAVE,
        status=ItemStatus.PURCHASED if purchased else ItemStatus.PENDING,
        link="https://example.com/machine",
        image_link="https://example.com/machine.png",
        description="For the kitchen",
        estimated_price=None if price is None else EstimatedPrice(price),
        tags=[_tag()],
    )


class TestShoppingListItem:
    def test_is_purchased_reflects_the_status(self):
        assert _list_item(purchased=True).is_purchased is True
        assert _list_item(purchased=False).is_purchased is False


class TestShoppingList:
    def test_completion_is_read_from_the_backend_status_not_re_derived(self):
        # The backend recomputes it, including its rule that an empty list is
        # never complete. Re-deriving here would be a second implementation of
        # that rule, free to disagree with the authority.
        assert _list(ItemStatus.PURCHASED, [_list_item(True)]).is_completed is True
        assert _list(ItemStatus.PENDING, [_list_item(True)]).is_completed is False

    def test_an_empty_list_is_not_completed(self):
        assert _list(ItemStatus.PENDING, []).is_completed is False

    def test_splits_items_into_pending_and_purchased(self):
        shopping_list = _list(
            ItemStatus.PENDING, [_list_item(True), _list_item(False), _list_item(True)]
        )
        assert len(shopping_list.purchased_items) == 2
        assert len(shopping_list.pending_items) == 1

    def test_progress_ratio_is_the_share_of_items_bought(self):
        shopping_list = _list(ItemStatus.PENDING, [_list_item(True), _list_item(False)])
        assert shopping_list.progress_ratio == 0.5

    def test_progress_ratio_is_zero_for_an_empty_list(self):
        # Not a division by zero, and not 1.0: an empty list has nothing bought,
        # so the honest figure is none of it.
        assert _list(ItemStatus.PENDING, []).progress_ratio == 0.0

    def test_progress_ratio_is_one_when_everything_is_bought(self):
        assert _list(ItemStatus.PURCHASED, [_list_item(True)]).progress_ratio == 1.0


class TestWishlistItem:
    def test_exposes_the_raw_price_for_a_form_input(self):
        assert _wishlist_item(price="399.90").estimated_price_amount == Decimal(
            "399.90"
        )

    def test_an_absent_price_stays_none_rather_than_becoming_zero(self):
        # The API's `estimated_price` is nullable, and "not estimated" and
        # "estimated at nothing" are different claims about an item.
        assert _wishlist_item(price=None).estimated_price_amount is None


class TestWishlist:
    def test_completion_is_read_from_the_backend_status(self):
        wishlist = Wishlist(
            id=WishlistId(uuid4()),
            name="My Wishlist",
            status=ItemStatus.PURCHASED,
            items=[_wishlist_item(purchased=True)],
        )
        assert wishlist.is_completed is True

    def test_an_empty_wishlist_is_not_completed(self):
        wishlist = Wishlist(id=WishlistId(uuid4()), name="My Wishlist", items=[])
        assert wishlist.is_completed is False

    def test_splits_items_into_pending_and_purchased(self):
        wishlist = Wishlist(
            id=WishlistId(uuid4()),
            name="My Wishlist",
            status=ItemStatus.PENDING,
            items=[_wishlist_item(True), _wishlist_item(False)],
        )
        assert len(wishlist.purchased_items) == 1
        assert len(wishlist.pending_items) == 1


def test_entities_are_frozen_so_a_mapper_cannot_mutate_a_shared_aggregate():
    # The mappers build these once per response and the ViewModels read them.
    # A mutable entity would let a template-side helper change what a later
    # reader of the same object sees.
    item = _list_item()
    with pytest.raises(AttributeError):
        item.name = "Something else"


def test_completed_at_defaults_to_none_so_a_new_list_is_never_dated():
    # AGENTS.md rule 7 in miniature: `completed_at` is nullable on the live API
    # and the right response to that is an explicit `None`, not a plausible
    # looking default.
    shopping_list = ShoppingList(id=ShoppingListId(uuid4()), name="Groceries")
    assert shopping_list.completed_at is None
    assert shopping_list.created_at is None


def test_completed_at_can_be_stamped_by_the_mapper():
    stamped = datetime(2026, 10, 5, 14, 31, tzinfo=UTC)
    shopping_list = ShoppingList(
        id=ShoppingListId(uuid4()),
        name="Groceries",
        status=ItemStatus.PURCHASED,
        completed_at=stamped,
    )
    assert shopping_list.completed_at == stamped
