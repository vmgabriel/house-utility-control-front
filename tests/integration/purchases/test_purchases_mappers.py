"""Integration tests for the purchases mappers and hand-written schemas.

The payloads here are copied from `apps/purchases/interfaces/serializers.py` in
the backend repository, field for field, rather than from what this BFF would
like the API to send. Where a field is nullable there -- and several are -- the
`null` case is tested explicitly rather than assumed, because AGENTS.md rule 7
records exactly this failure in this product: tightening a runtime schema to
match the OpenAPI spec raised inside a mapper and surfaced as a 500 on a page a
freshly registered user had just opened.

Nothing here mocks HTTP. These are pure functions over two payloads, which is
what makes them worth their own file: they are the layer where a wire-shape
assumption would be made, and a wrong assumption here is invisible from a route
test that happens to use a well-behaved fixture.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.purchases.domain.exceptions import InvalidPurchaseError
from src.purchases.domain.value_objects import ItemPriority, ItemStatus, ListPriority
from src.purchases.infrastructure.mappers import (
    map_shopping_list,
    map_tag,
    map_wishlist,
    map_wishlist_item,
)
from src.purchases.infrastructure.schemas import (
    DRFShoppingListResponse,
    DRFTagResponse,
    DRFWishlistItemResponse,
    DRFWishlistResponse,
)


def tag_payload(name: str = "Home") -> dict:
    """One `TagSerializer` payload."""
    return {"id": str(uuid4()), "name": name, "slug": name.lower()}


def list_item_payload(**overrides) -> dict:
    """One `ShoppingListItemSerializer` payload."""
    payload = {
        "id": str(uuid4()),
        "name": "Milk",
        "quantity": 2,
        "details": "Semi-skimmed",
        "status": "pending",
        "tags": [],
    }
    payload.update(overrides)
    return payload


def shopping_list_payload(**overrides) -> dict:
    """One `ShoppingListSerializer` payload, items nested."""
    payload = {
        "id": str(uuid4()),
        "name": "Weekly groceries",
        "priority": "medium",
        "status": "pending",
        "is_active": True,
        "created_at": "2026-10-01T09:00:00Z",
        # Null on a list that has never been completed -- and forever on an
        # empty one, which is exactly the case rule 7 is about.
        "completed_at": None,
        "items": [],
        "tags": [],
    }
    payload.update(overrides)
    return payload


def wishlist_item_payload(**overrides) -> dict:
    """One `WishlistItemSerializer` payload."""
    payload = {
        "id": str(uuid4()),
        "name": "Espresso machine",
        "link": "https://example.com/machine",
        "image_link": "https://example.com/machine.png",
        "description": "For the kitchen",
        # A string, because DRF's `COERCE_DECIMAL_TO_STRING` is on by default.
        "estimated_price": "399.90",
        "priority": "must_have",
        "status": "pending",
        "tags": [],
    }
    payload.update(overrides)
    return payload


def wishlist_payload(**overrides) -> dict:
    """One `WishlistSerializer` payload."""
    payload = {
        "id": str(uuid4()),
        "name": "My Wishlist",
        "status": "pending",
        "is_active": True,
        "created_at": "2026-10-01T09:00:00Z",
        "completed_at": None,
        "items": [],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #
class TestTags:
    def test_maps_the_catalogue_entry(self):
        raw = tag_payload("Gift")
        mapped = map_tag(DRFTagResponse.model_validate(raw))
        assert mapped.name == "Gift"
        assert mapped.slug == "gift"
        assert str(mapped.id) == raw["id"]


# --------------------------------------------------------------------------- #
# Shopping lists
# --------------------------------------------------------------------------- #
class TestShoppingListSchema:
    def test_accepts_a_null_completed_at(self):
        # The rule-7 field. A schema that declared it a non-nullable datetime
        # would raise here and the overview would 500 on a brand-new list.
        parsed = DRFShoppingListResponse.model_validate(shopping_list_payload())
        assert parsed.completed_at is None

    def test_accepts_null_item_details(self):
        parsed = DRFShoppingListResponse.model_validate(
            shopping_list_payload(items=[list_item_payload(details=None)])
        )
        assert parsed.items[0].details is None

    def test_accepts_an_unknown_extra_field(self):
        # The backend adds fields without a BFF release, so an unknown one must
        # not fail the page.
        raw = shopping_list_payload(some_future_field="surprise")
        assert DRFShoppingListResponse.model_validate(raw).name == "Weekly groceries"

    def test_rejects_a_payload_with_no_id(self):
        raw = shopping_list_payload()
        del raw["id"]
        with pytest.raises(ValueError):
            DRFShoppingListResponse.model_validate(raw)


class TestMapShoppingList:
    def test_maps_the_aggregate_with_its_items(self):
        raw = shopping_list_payload(
            priority="high",
            items=[
                list_item_payload(
                    name="Milk", status="purchased", tags=[tag_payload()]
                ),
                list_item_payload(name="Bread"),
            ],
        )

        mapped = map_shopping_list(DRFShoppingListResponse.model_validate(raw))

        assert mapped.name == "Weekly groceries"
        assert mapped.priority is ListPriority.HIGH
        assert [item.name for item in mapped.items] == ["Milk", "Bread"]
        assert mapped.items[0].is_purchased is True
        assert mapped.items[1].is_purchased is False
        assert mapped.items[0].tags[0].name == "Home"

    def test_a_null_completed_at_leaves_the_list_undated_and_incomplete(self):
        mapped = map_shopping_list(
            DRFShoppingListResponse.model_validate(shopping_list_payload())
        )
        assert mapped.completed_at is None
        assert mapped.is_completed is False

    def test_a_stamped_completion_is_read_as_utc(self):
        mapped = map_shopping_list(
            DRFShoppingListResponse.model_validate(
                shopping_list_payload(
                    status="purchased", completed_at="2026-10-05T14:31:12.480000Z"
                )
            )
        )
        assert mapped.completed_at is not None
        assert mapped.is_completed is True

    def test_an_unrecognised_priority_falls_back_to_medium(self):
        # `medium` is `CreateShoppingListSerializer`'s own default, so an item
        # stored before this BFF knew the value still gets the badge the form
        # would have given it -- rather than a blank badge.
        mapped = map_shopping_list(
            DRFShoppingListResponse.model_validate(
                shopping_list_payload(priority="urgent")
            )
        )
        assert mapped.priority is ListPriority.MEDIUM

    def test_an_unrecognised_status_reads_as_pending_never_as_purchased(self):
        # The asymmetry matters: guessing "bought" would render a checklist
        # ticking itself off, and that direction destroys work the user still has
        # to do. Guessing "pending" only shows them something they already knew.
        mapped = map_shopping_list(
            DRFShoppingListResponse.model_validate(
                shopping_list_payload(
                    status="archived",
                    items=[list_item_payload(status="archived")],
                )
            )
        )
        assert mapped.is_completed is False
        assert all(not item.is_purchased for item in mapped.items)

    def test_an_archived_list_maps_with_is_active_false(self):
        # What `GET /shopping-lists/inactive/` serves.
        mapped = map_shopping_list(
            DRFShoppingListResponse.model_validate(
                shopping_list_payload(is_active=False)
            )
        )
        assert mapped.is_active is False


# --------------------------------------------------------------------------- #
# Wishlist
# --------------------------------------------------------------------------- #
class TestWishlistSchema:
    def test_accepts_a_null_estimated_price(self):
        # Nullable on the serialiser, so a user who wrote no price gets `null`.
        # Declaring it a plain string would raise inside a mapper on the first
        # wishlist page a new user opened.
        parsed = DRFWishlistItemResponse.model_validate(
            wishlist_item_payload(estimated_price=None)
        )
        assert parsed.estimated_price is None

    @pytest.mark.parametrize("field", ["link", "image_link", "description"])
    def test_accepts_null_optional_fields(self, field):
        parsed = DRFWishlistItemResponse.model_validate(
            wishlist_item_payload(**{field: None})
        )
        assert getattr(parsed, field) is None

    def test_accepts_a_null_completed_at(self):
        parsed = DRFWishlistResponse.model_validate(wishlist_payload())
        assert parsed.completed_at is None


class TestMapWishlistItem:
    def test_converts_the_decimal_string_to_a_decimal_without_losing_cents(self):
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(estimated_price="399.90")
            )
        )
        assert mapped.estimated_price is not None
        assert mapped.estimated_price.amount == Decimal("399.90")
        # `normalize()` would have produced "399.9"; the two-decimal form is what
        # the serialiser guarantees and what the card renders.
        assert str(mapped.estimated_price) == "399.90"

    def test_an_absent_price_is_none_rather_than_zero(self):
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(estimated_price=None)
            )
        )
        assert mapped.estimated_price is None
        assert mapped.estimated_price_amount is None

    def test_a_zero_price_stays_a_price(self):
        # Free is a real answer; collapsing it into `None` would lose it.
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(estimated_price="0.00")
            )
        )
        assert mapped.estimated_price is not None
        assert mapped.estimated_price.amount == Decimal("0.00")

    def test_an_unreadable_price_becomes_none_rather_than_raising(self):
        # A price is decoration on a card. One unreadable figure must not take
        # the whole wishlist page down.
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(estimated_price="not-a-number")
            )
        )
        assert mapped.estimated_price is None

    def test_a_negative_price_from_the_api_becomes_none_rather_than_raising(self):
        # The API cannot produce this -- `min_value=0` on the serializer -- but if
        # it ever did, the value object would refuse it, and refusing it here
        # keeps the page up.
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(estimated_price="-1.00")
            )
        )
        assert mapped.estimated_price is None

    def test_maps_the_priority(self):
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(priority="nice_to_have")
            )
        )
        assert mapped.priority is ItemPriority.NICE_TO_HAVE

    def test_an_unrecognised_priority_falls_back_to_wanted(self):
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(priority="desperate")
            )
        )
        assert mapped.priority is ItemPriority.WANTED

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "not-a-url",
        ],
    )
    def test_a_non_http_url_is_dropped_rather_than_reaching_the_template(self, url):
        # Both values are rendered into an `href` and an `img src`. A `javascript:`
        # URL surviving into either attribute is an injection the browser runs on
        # render, so a stored value that fails the check becomes `None` -- the
        # card renders without the link and the image falls back -- rather than
        # raising and blanking the whole wishlist.
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(link=url, image_link=url)
            )
        )
        assert mapped.link is None
        assert mapped.image_link is None

    def test_a_valid_url_survives(self):
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(link="https://example.com/x?a=1&b=2")
            )
        )
        assert mapped.link == "https://example.com/x?a=1&b=2"

    def test_maps_the_purchased_status(self):
        mapped = map_wishlist_item(
            DRFWishlistItemResponse.model_validate(
                wishlist_item_payload(status="purchased")
            )
        )
        assert mapped.status is ItemStatus.PURCHASED
        assert mapped.is_purchased is True


class TestMapWishlist:
    def test_maps_the_aggregate_with_its_items(self):
        mapped = map_wishlist(
            DRFWishlistResponse.model_validate(
                wishlist_payload(
                    items=[
                        wishlist_item_payload(name="Lamp", estimated_price=None),
                        wishlist_item_payload(name="Rug", status="purchased"),
                    ]
                )
            )
        )

        assert mapped.name == "My Wishlist"
        assert [item.name for item in mapped.items] == ["Lamp", "Rug"]
        assert mapped.items[0].estimated_price is None
        assert mapped.items[1].is_purchased is True

    def test_an_empty_wishlist_maps_and_is_not_complete(self):
        mapped = map_wishlist(DRFWishlistResponse.model_validate(wishlist_payload()))
        assert mapped.items == []
        assert mapped.is_completed is False
        assert mapped.pending_items == []


# --------------------------------------------------------------------------- #
# The mapping rule-7 exists to catch, tested directly
# --------------------------------------------------------------------------- #
def test_a_purchases_payload_read_as_a_boolean_would_have_been_wrong():
    """The schema decision, asserted rather than asserted-about.

    A purchases item's "bought" flag is a `status` *string*. Modelling it as a
    boolean is the mistake this file exists to prevent, and the failure it causes
    is silent rather than loud: every item would arrive as unbought and every
    checklist would render as though nothing had ever been bought.

    The test constructs the boolean reading this BFF originally made and shows
    it cannot be derived from the payload, then shows the string reading works.
    """
    raw = wishlist_item_payload(status="purchased")

    # The wire field is a string; there is no boolean anywhere in the payload.
    assert isinstance(raw["status"], str)
    assert "is_purchased" not in raw

    mapped = map_wishlist_item(DRFWishlistItemResponse.model_validate(raw))
    assert mapped.is_purchased is True


def test_a_malformed_timestamp_is_a_domain_error_not_a_bare_value_error():
    # The view's `except DomainException` does not catch a bare `ValueError`, so
    # an unlabelled one here would turn a bad payload into a 500.
    with pytest.raises(InvalidPurchaseError):
        map_wishlist(
            DRFWishlistResponse.model_validate(
                wishlist_payload(completed_at="the day before yesterday")
            )
        )
