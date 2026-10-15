"""Integration tests for the purchases DRF repositories, with `respx` at the socket.

These exercise the adapter's URL construction, status translation, and payload
shape together -- the three things a use-case test with a fake port cannot see,
and the three that are wrong most often. Mocking at the socket is the only level
that still tests all three at once.

Every request and response body here is copied from the backend's
`apps/purchases/interfaces/` rather than invented, and the URLs are the ones in
`apps/purchases/interfaces/urls.py` under the `/purchases/` prefix declared in
`apps/api/urls.py`.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
import respx

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
from src.purchases.infrastructure.repositories import (
    DRFShoppingListRepository,
    DRFTagRepository,
    DRFWishlistRepository,
)
from src.shared.http.drf_client import DRFAPIClient

BASE = "http://localhost:8000/api/v1"
LISTS_URL = f"{BASE}/purchases/shopping-lists/"
WISHLIST_URL = f"{BASE}/purchases/wishlist/"
TAGS_URL = f"{BASE}/purchases/tags/"

TOKEN = "purchases-access-token"

LIST_ID = str(uuid4())
ITEM_ID = str(uuid4())


def tag_payload(name: str = "Home") -> dict:
    return {"id": str(uuid4()), "name": name, "slug": name.lower()}


def list_item_payload(**overrides) -> dict:
    payload = {
        "id": ITEM_ID,
        "name": "Milk",
        "quantity": 2,
        "details": None,
        "status": "pending",
        "tags": [],
    }
    payload.update(overrides)
    return payload


def shopping_list_payload(**overrides) -> dict:
    payload = {
        "id": LIST_ID,
        "name": "Weekly groceries",
        "priority": "medium",
        "status": "pending",
        "is_active": True,
        "created_at": "2026-10-01T09:00:00Z",
        "completed_at": None,
        "items": [list_item_payload()],
        "tags": [],
    }
    payload.update(overrides)
    return payload


def wishlist_item_payload(**overrides) -> dict:
    payload = {
        "id": ITEM_ID,
        "name": "Espresso machine",
        "link": "https://example.com/machine",
        "image_link": None,
        "description": None,
        "estimated_price": "399.90",
        "priority": "must_have",
        "status": "pending",
        "tags": [],
    }
    payload.update(overrides)
    return payload


def wishlist_payload(**overrides) -> dict:
    payload = {
        "id": str(uuid4()),
        "name": "My Wishlist",
        "status": "pending",
        "is_active": True,
        "created_at": "2026-10-01T09:00:00Z",
        "completed_at": None,
        "items": [wishlist_item_payload()],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def shopping_lists() -> DRFShoppingListRepository:
    return DRFShoppingListRepository(DRFAPIClient(base_url=BASE))


@pytest.fixture
def wishlists() -> DRFWishlistRepository:
    return DRFWishlistRepository(DRFAPIClient(base_url=BASE))


@pytest.fixture
def tags() -> DRFTagRepository:
    return DRFTagRepository(DRFAPIClient(base_url=BASE))


# --------------------------------------------------------------------------- #
# Shopping lists: reads
# --------------------------------------------------------------------------- #
@respx.mock
class TestShoppingListReads:
    async def test_active_lists_come_from_the_bare_collection_url(self, shopping_lists):
        # `GET /shopping-lists/` is filtered to `is_active=true` server-side.
        # There is no query parameter that widens it, which is why the archived
        # read is a different endpoint and not a flag.
        route = respx.get(LISTS_URL).mock(
            return_value=httpx.Response(200, json=[shopping_list_payload()])
        )

        result = await shopping_lists.get_active_lists(TOKEN)

        assert route.called
        assert len(result) == 1
        assert result[0].name == "Weekly groceries"

    async def test_archived_lists_come_from_the_inactive_url(self, shopping_lists):
        route = respx.get(f"{BASE}/purchases/shopping-lists/inactive/").mock(
            return_value=httpx.Response(
                200, json=[shopping_list_payload(is_active=False)]
            )
        )

        result = await shopping_lists.get_inactive_lists(TOKEN)

        assert route.called
        assert result[0].is_active is False

    async def test_a_list_is_fetched_with_its_items_nested(self, shopping_lists):
        respx.get(f"{LISTS_URL}{LIST_ID}/").mock(
            return_value=httpx.Response(200, json=shopping_list_payload())
        )

        result = await shopping_lists.get_list(TOKEN, ShoppingListId(LIST_ID))

        assert result is not None
        assert len(result.items) == 1

    async def test_a_404_on_a_read_is_none_not_an_exception(self, shopping_lists):
        # The backend answers 404 for both "missing" and "not yours", and the two
        # are deliberately not separable here.
        respx.get(f"{LISTS_URL}{LIST_ID}/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )

        assert await shopping_lists.get_list(TOKEN, ShoppingListId(LIST_ID)) is None

    async def test_an_empty_collection_is_an_empty_list(self, shopping_lists):
        respx.get(LISTS_URL).mock(return_value=httpx.Response(200, json=[]))
        assert await shopping_lists.get_active_lists(TOKEN) == []

    async def test_a_paginated_envelope_is_rejected_with_a_named_error(
        self, shopping_lists
    ):
        # A DRF list view that starts paginating answers `{"results": [...]}`, and
        # validating that against the item schema would fail with a message about
        # a missing `id` on the key "results" -- accurate, and useless. The shape
        # is checked first so the failure names the real cause.
        respx.get(LISTS_URL).mock(
            return_value=httpx.Response(
                200, json={"count": 1, "next": None, "previous": None, "results": []}
            )
        )

        with pytest.raises(InvalidPurchasesInputError, match="did not return a list"):
            await shopping_lists.get_active_lists(TOKEN)

    async def test_a_null_body_on_a_collection_reads_as_empty(self, shopping_lists):
        # A 200 with no body is `None` after the shared client's JSON handling.
        # An empty page is the honest reading of "nothing to show", not a crash.
        respx.get(LISTS_URL).mock(return_value=httpx.Response(200))
        assert await shopping_lists.get_active_lists(TOKEN) == []


# --------------------------------------------------------------------------- #
# Shopping lists: list mutations
# --------------------------------------------------------------------------- #
@respx.mock
class TestShoppingListMutations:
    async def test_create_sends_the_name_and_priority(self, shopping_lists):
        route = respx.post(LISTS_URL).mock(
            return_value=httpx.Response(201, json=shopping_list_payload())
        )

        await shopping_lists.create_list(TOKEN, "Weekly groceries", ListPriority.HIGH)

        assert route.calls[0].request.read() == (
            b'{"name": "Weekly groceries", "priority": "high"}'
        )

    async def test_create_turns_a_400_into_the_backends_own_message(
        self, shopping_lists
    ):
        # The purchases views translate their domain errors through
        # `ValidationError({"detail": ...})`, so that string is the most useful
        # thing to show the user and it is worth surfacing verbatim.
        respx.post(LISTS_URL).mock(
            return_value=httpx.Response(400, json={"detail": "Name cannot be empty."})
        )

        with pytest.raises(InvalidPurchasesInputError, match="Name cannot be empty"):
            await shopping_lists.create_list(TOKEN, "", ListPriority.MEDIUM)

    async def test_an_update_omits_the_field_that_was_not_supplied(
        self, shopping_lists
    ):
        # An absent key is what PATCH means by "leave this alone". Sending `null`
        # would *clear* the column, and `UpdateShoppingListSerializer` would
        # reject it anyway -- a nullable column is not a nullable input.
        route = respx.patch(f"{LISTS_URL}{LIST_ID}/").mock(
            return_value=httpx.Response(
                200,
                json=shopping_list_payload(priority="high", name="Weekly groceries"),
            )
        )

        await shopping_lists.update_list(
            TOKEN, ShoppingListId(LIST_ID), priority=ListPriority.HIGH
        )

        assert route.calls[0].request.read() == b'{"priority": "high"}'

    async def test_an_update_with_nothing_to_change_is_refused_before_the_request(
        self, shopping_lists
    ):
        # The API answers 400 for an empty body ("Provide name or priority."), so
        # refusing here gives a message naming the problem instead of a 400 about
        # a field the user cannot see.
        route = respx.patch(f"{LISTS_URL}{LIST_ID}/")

        with pytest.raises(InvalidPurchasesInputError, match="name or a priority"):
            await shopping_lists.update_list(TOKEN, ShoppingListId(LIST_ID))

        assert not route.called

    async def test_a_404_on_update_raises_rather_than_returning_none(
        self, shopping_lists
    ):
        # Different from a read: the user was looking at a row that has stopped
        # existing, and reporting "updated" would hide that their view was stale.
        respx.patch(f"{LISTS_URL}{LIST_ID}/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )

        with pytest.raises(ShoppingListNotFoundError, match="not available"):
            await shopping_lists.update_list(
                TOKEN, ShoppingListId(LIST_ID), name="Renamed"
            )

    async def test_soft_delete_returns_the_archived_list_not_a_204(
        self, shopping_lists
    ):
        # `DELETE /shopping-lists/<pk>/` answers 200 with the list; the rows and
        # their items survive and `restore` brings them back.
        route = respx.delete(f"{LISTS_URL}{LIST_ID}/").mock(
            return_value=httpx.Response(
                200, json=shopping_list_payload(is_active=False)
            )
        )

        result = await shopping_lists.soft_delete_list(TOKEN, ShoppingListId(LIST_ID))

        assert route.called
        assert result.is_active is False

    async def test_restore_posts_to_the_restore_route_with_no_body(
        self, shopping_lists
    ):
        route = respx.post(f"{LISTS_URL}{LIST_ID}/restore/").mock(
            return_value=httpx.Response(200, json=shopping_list_payload())
        )

        result = await shopping_lists.restore_list(TOKEN, ShoppingListId(LIST_ID))

        assert route.called
        assert result.is_active is True


# --------------------------------------------------------------------------- #
# Shopping lists: item mutations
# --------------------------------------------------------------------------- #
@respx.mock
class TestShoppingListItemMutations:
    async def test_add_item_posts_name_and_quantity(self, shopping_lists):
        # `quantity` defaults to 1 on the API and is therefore sent explicitly.
        route = respx.post(f"{LISTS_URL}{LIST_ID}/add-item/").mock(
            return_value=httpx.Response(201, json=shopping_list_payload())
        )

        await shopping_lists.add_item(TOKEN, ShoppingListId(LIST_ID), name="Milk")

        assert route.calls[0].request.read() == b'{"name": "Milk", "quantity": 1}'

    async def test_add_item_omits_a_blank_details_field(self, shopping_lists):
        # `details` is `required=False`, and the backend's `_optional_str` maps an
        # empty string to `None` anyway -- so omitting it means the same thing with
        # one field fewer to drift.
        route = respx.post(f"{LISTS_URL}{LIST_ID}/add-item/").mock(
            return_value=httpx.Response(201, json=shopping_list_payload())
        )

        await shopping_lists.add_item(
            TOKEN, ShoppingListId(LIST_ID), name="Milk", details=None
        )

        assert b"details" not in route.calls[0].request.read()

    async def test_add_item_returns_the_whole_reloaded_list(self, shopping_lists):
        # The response is the aggregate, and it is the only place the new item's
        # id appears -- there is no item-collection endpoint for shopping lists.
        respx.post(f"{LISTS_URL}{LIST_ID}/add-item/").mock(
            return_value=httpx.Response(
                201,
                json=shopping_list_payload(
                    items=[list_item_payload(), list_item_payload(id=str(uuid4()))]
                ),
            )
        )

        result = await shopping_lists.add_item(
            TOKEN, ShoppingListId(LIST_ID), name="Milk"
        )

        assert len(result.items) == 2

    async def test_mark_purchased_posts_to_the_nested_action_and_returns_the_list(
        self, shopping_lists
    ):
        route = respx.post(
            f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/mark-purchased/"
        ).mock(
            return_value=httpx.Response(
                200,
                json=shopping_list_payload(
                    status="purchased",
                    completed_at="2026-10-05T14:31:12.480000Z",
                    items=[list_item_payload(status="purchased")],
                ),
            )
        )

        result = await shopping_lists.mark_item_purchased(
            TOKEN, ShoppingListId(LIST_ID), ShoppingListItemId(ITEM_ID)
        )

        assert route.called
        # The response is what tells the page that the tick completed the list.
        assert result.is_completed is True
        assert result.items[0].is_purchased is True

    async def test_mark_purchased_on_a_missing_item_raises(self, shopping_lists):
        respx.post(f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/mark-purchased/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )

        with pytest.raises(ShoppingListItemNotFoundError, match="no longer exists"):
            await shopping_lists.mark_item_purchased(
                TOKEN, ShoppingListId(LIST_ID), ShoppingListItemId(ITEM_ID)
            )

    async def test_a_mismatched_list_and_item_pair_is_a_404(self, shopping_lists):
        # The backend's `_require_item_on_list` pins the nesting: an item from
        # another list of the caller's own is refused rather than mutated.
        other_list = str(uuid4())
        respx.post(f"{LISTS_URL}{other_list}/items/{ITEM_ID}/mark-purchased/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )

        with pytest.raises(ShoppingListItemNotFoundError):
            await shopping_lists.mark_item_purchased(
                TOKEN, ShoppingListId(other_list), ShoppingListItemId(ITEM_ID)
            )

    async def test_delete_item_returns_the_reloaded_list(self, shopping_lists):
        route = respx.delete(f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/").mock(
            return_value=httpx.Response(200, json=shopping_list_payload(items=[]))
        )

        result = await shopping_lists.delete_item(
            TOKEN, ShoppingListId(LIST_ID), ShoppingListItemId(ITEM_ID)
        )

        assert route.called
        assert result.items == []

    async def test_update_item_sends_only_the_supplied_keys(self, shopping_lists):
        route = respx.patch(f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/").mock(
            return_value=httpx.Response(200, json=list_item_payload(name="Oat milk"))
        )

        result = await shopping_lists.update_item(
            TOKEN, ShoppingListId(LIST_ID), ShoppingListItemId(ITEM_ID), name="Oat milk"
        )

        assert route.calls[0].request.read() == b'{"name": "Oat milk"}'
        assert result.name == "Oat milk"

    async def test_update_item_with_nothing_to_change_is_refused(self, shopping_lists):
        route = respx.patch(f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/")

        with pytest.raises(InvalidPurchasesInputError, match="quantity, or details"):
            await shopping_lists.update_item(
                TOKEN, ShoppingListId(LIST_ID), ShoppingListItemId(ITEM_ID)
            )

        assert not route.called


# --------------------------------------------------------------------------- #
# Shopping lists: item tags
# --------------------------------------------------------------------------- #
@respx.mock
class TestShoppingListItemTags:
    async def test_attaching_tags_sends_names(self, shopping_lists):
        # By name, because the backend's `AddTagsToItem` creates whatever it is
        # given -- a name is the natural key on the way in.
        route = respx.post(f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/tags/").mock(
            return_value=httpx.Response(
                200, json=list_item_payload(tags=[tag_payload("Home")])
            )
        )

        result = await shopping_lists.add_tags_to_item(
            TOKEN,
            ShoppingListId(LIST_ID),
            ShoppingListItemId(ITEM_ID),
            ["Home"],
        )

        assert route.calls[0].request.read() == b'{"tag_names": ["Home"]}'
        assert result.tags[0].name == "Home"

    async def test_detaching_tags_sends_ids_in_a_delete_body(self, shopping_lists):
        # `DELETE` with a JSON body, and by id: attaching creates catalogue
        # entries on demand, so a name is not a stable handle for an existing
        # attachment the way an id is.
        tag = tag_payload("Home")
        route = respx.delete(f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/tags/").mock(
            return_value=httpx.Response(200, json=list_item_payload(tags=[]))
        )

        result = await shopping_lists.remove_tags_from_item(
            TOKEN,
            ShoppingListId(LIST_ID),
            ShoppingListItemId(ITEM_ID),
            [TagId(tag["id"])],
        )

        assert route.calls[0].request.read() == (
            b'{"tag_ids": ["' + tag["id"].encode() + b'"]}'
        )
        assert result.tags == []


# --------------------------------------------------------------------------- #
# Wishlist
# --------------------------------------------------------------------------- #
@respx.mock
class TestWishlistReads:
    async def test_the_wishlist_is_read_from_the_bare_url(self, wishlists):
        # `GET /wishlist/` is routed to `GetOrCreateWishlist` and answers 200
        # whether it created the list or found it, so there is no create call and
        # no not-found branch.
        route = respx.get(WISHLIST_URL).mock(
            return_value=httpx.Response(200, json=wishlist_payload())
        )

        result = await wishlists.get_or_create_wishlist(TOKEN)

        assert route.called
        assert result.name == "My Wishlist"

    async def test_the_wishlist_carries_its_items_nested(self, wishlists):
        respx.get(WISHLIST_URL).mock(
            return_value=httpx.Response(200, json=wishlist_payload())
        )

        result = await wishlists.get_or_create_wishlist(TOKEN)

        assert len(result.items) == 1
        assert result.items[0].estimated_price is not None

    async def test_renaming_patches_the_bare_url_with_a_name(self, wishlists):
        route = respx.patch(WISHLIST_URL).mock(
            return_value=httpx.Response(200, json=wishlist_payload(name="Gifts"))
        )

        result = await wishlists.update_wishlist(TOKEN, "Gifts")

        # Not addressed by uuid: the route resolves the caller's own wishlist.
        assert route.calls[0].request.read() == b'{"name": "Gifts"}'
        assert result.name == "Gifts"


@respx.mock
class TestWishlistMutations:
    async def test_add_item_sends_the_price_as_a_decimal_string(self, wishlists):
        # The serialiser declares a `DecimalField`, and DRF renders decimals as
        # strings in both directions. `str(Decimal("399.9"))` would be "399.9";
        # `f"{amount:f}"` is "399.90", which is what the field's two decimal
        # places mean.
        route = respx.post(f"{BASE}/purchases/wishlist/add-item/").mock(
            return_value=httpx.Response(201, json=wishlist_payload())
        )

        await wishlists.add_item(
            TOKEN, name="Espresso machine", estimated_price=EstimatedPrice("399.90")
        )

        assert b'"estimated_price": "399.90"' in route.calls[0].request.read()

    async def test_add_item_omits_every_absent_optional_field(self, wishlists):
        route = respx.post(f"{BASE}/purchases/wishlist/add-item/").mock(
            return_value=httpx.Response(201, json=wishlist_payload())
        )

        await wishlists.add_item(TOKEN, name="Lamp")

        body = route.calls[0].request.read()
        assert body == b'{"name": "Lamp", "priority": "wanted"}'
        # Absent, not `null`: on a create the two mean the same to DRF, and one
        # field fewer is one less way for the two paths to drift.
        assert b"link" not in body

    async def test_add_item_sends_a_zero_price_as_a_price_not_as_absence(
        self, wishlists
    ):
        # Zero is legal -- "free" is a real answer -- and it is sent as "0.00"
        # rather than omitted, so the card can distinguish it from "no estimate".
        # What the value object refuses is a negative, and it refuses that before
        # the round trip (asserted in the domain tests).
        route = respx.post(f"{BASE}/purchases/wishlist/add-item/").mock(
            return_value=httpx.Response(201, json=wishlist_payload())
        )

        await wishlists.add_item(
            TOKEN, name="Gift", estimated_price=EstimatedPrice("0.00")
        )

        assert b'"estimated_price": "0.00"' in route.calls[0].request.read()

    async def test_add_item_turns_a_400_into_the_backends_message(self, wishlists):
        respx.post(f"{BASE}/purchases/wishlist/add-item/").mock(
            return_value=httpx.Response(
                400, json={"detail": "Link must be a valid URL."}
            )
        )

        with pytest.raises(InvalidPurchasesInputError, match="valid URL"):
            await wishlists.add_item(TOKEN, name="Lamp", link="not-a-url")

    async def test_mark_purchased_returns_the_whole_wishlist(self, wishlists):
        route = respx.post(
            f"{BASE}/purchases/wishlist/items/{ITEM_ID}/mark-purchased/"
        ).mock(
            return_value=httpx.Response(
                200,
                json=wishlist_payload(
                    status="purchased",
                    completed_at="2026-10-05T14:31:12.480000Z",
                    items=[wishlist_item_payload(status="purchased")],
                ),
            )
        )

        result = await wishlists.mark_item_purchased(TOKEN, WishlistItemId(ITEM_ID))

        assert route.called
        assert result.is_completed is True
        assert result.items[0].is_purchased is True

    async def test_mark_purchased_on_a_foreign_item_raises(self, wishlists):
        # The backend answers 404 for "not yours" as well as "missing", so this
        # BFF cannot -- and must not -- distinguish them.
        respx.post(f"{BASE}/purchases/wishlist/items/{ITEM_ID}/mark-purchased/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )

        with pytest.raises(WishlistItemNotFoundError, match="no longer exists"):
            await wishlists.mark_item_purchased(TOKEN, WishlistItemId(ITEM_ID))

    async def test_delete_item_returns_the_reloaded_wishlist(self, wishlists):
        route = respx.delete(f"{BASE}/purchases/wishlist/items/{ITEM_ID}/").mock(
            return_value=httpx.Response(200, json=wishlist_payload(items=[]))
        )

        result = await wishlists.delete_item(TOKEN, WishlistItemId(ITEM_ID))

        assert route.called
        assert result.items == []

    async def test_update_item_sends_only_the_supplied_keys(self, wishlists):
        route = respx.patch(f"{BASE}/purchases/wishlist/items/{ITEM_ID}/").mock(
            return_value=httpx.Response(
                200, json=wishlist_item_payload(priority="wanted")
            )
        )

        result = await wishlists.update_item(
            TOKEN, WishlistItemId(ITEM_ID), priority=ItemPriority.WANTED
        )

        assert route.calls[0].request.read() == b'{"priority": "wanted"}'
        assert result.priority is ItemPriority.WANTED

    async def test_update_item_with_nothing_to_change_is_refused(self, wishlists):
        route = respx.patch(f"{BASE}/purchases/wishlist/items/{ITEM_ID}/")

        with pytest.raises(InvalidPurchasesInputError, match="something to change"):
            await wishlists.update_item(TOKEN, WishlistItemId(ITEM_ID))

        assert not route.called

    async def test_wishlist_item_tags_are_managed_by_name_then_by_id(self, wishlists):
        tag = tag_payload("Gift")
        respx.post(f"{BASE}/purchases/wishlist/items/{ITEM_ID}/tags/").mock(
            return_value=httpx.Response(200, json=wishlist_item_payload(tags=[tag]))
        )
        respx.delete(f"{BASE}/purchases/wishlist/items/{ITEM_ID}/tags/").mock(
            return_value=httpx.Response(200, json=wishlist_item_payload(tags=[]))
        )

        attached = await wishlists.add_tags_to_item(
            TOKEN, WishlistItemId(ITEM_ID), ["Gift"]
        )
        detached = await wishlists.remove_tags_from_item(
            TOKEN, WishlistItemId(ITEM_ID), [TagId(tag["id"])]
        )

        assert attached.tags[0].name == "Gift"
        assert detached.tags == []


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #
@respx.mock
class TestTagCatalogue:
    async def test_the_catalogue_is_read_from_the_bare_url(self, tags):
        route = respx.get(TAGS_URL).mock(
            return_value=httpx.Response(200, json=[tag_payload("Home")])
        )

        result = await tags.get_all_tags(TOKEN)

        assert route.called
        assert result[0].name == "Home"

    async def test_an_empty_catalogue_is_an_empty_list(self, tags):
        respx.get(TAGS_URL).mock(return_value=httpx.Response(200, json=[]))
        assert await tags.get_all_tags(TOKEN) == []

    async def test_creating_a_tag_posts_just_the_name(self, tags):
        route = respx.post(TAGS_URL).mock(
            return_value=httpx.Response(201, json=tag_payload("Holiday"))
        )

        result = await tags.create_or_get_tag(TOKEN, "Holiday")

        assert route.calls[0].request.read() == b'{"name": "Holiday"}'
        assert result.name == "Holiday"

    async def test_creating_an_existing_tag_answers_200_and_still_maps(self, tags):
        # `CreateTag` is idempotent by name: 201 for a new tag, 200 for one that
        # already existed. The adapter does not report the difference -- nothing
        # in the UI needs it -- but it must not choke on the 200 either.
        route = respx.post(TAGS_URL).mock(
            return_value=httpx.Response(200, json=tag_payload("Holiday"))
        )

        result = await tags.create_or_get_tag(TOKEN, "Holiday")

        assert route.called
        assert result.name == "Holiday"


# --------------------------------------------------------------------------- #
# The boundary rule-7 exists to catch, end to end
# --------------------------------------------------------------------------- #
@respx.mock
async def test_a_new_users_empty_wishlist_renders_without_a_500(wishlists):
    """The whole rule-7 chain, on the payload a brand-new user actually gets.

    An empty wishlist has `completed_at: null` and every item field absent. If any
    of those were modelled as non-nullable, this is where it would raise -- and
    the user would meet it on their very first visit to the page.
    """
    respx.get(WISHLIST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": str(uuid4()),
                "name": "My Wishlist",
                "status": "pending",
                "is_active": True,
                "created_at": "2026-10-01T09:00:00Z",
                "completed_at": None,
                "items": [],
            },
        )
    )

    result = await wishlists.get_or_create_wishlist(TOKEN)

    assert result.items == []
    assert result.completed_at is None
    assert result.is_completed is False


@respx.mock
async def test_a_wishlist_item_with_every_optional_field_absent_maps(wishlists):
    respx.get(WISHLIST_URL).mock(
        return_value=httpx.Response(
            200,
            json=wishlist_payload(
                items=[
                    {
                        "id": str(uuid4()),
                        "name": "Lamp",
                        "link": None,
                        "image_link": None,
                        "description": None,
                        "estimated_price": None,
                        "priority": "wanted",
                        "status": "pending",
                        "tags": [],
                    }
                ]
            ),
        )
    )

    result = await wishlists.get_or_create_wishlist(TOKEN)
    item = result.items[0]

    assert item.link is None
    assert item.image_link is None
    assert item.description is None
    assert item.estimated_price is None


# --------------------------------------------------------------------------- #
# Tag catalogue: a 404 on a removal
# --------------------------------------------------------------------------- #
@respx.mock
async def test_removing_a_tag_from_a_missing_item_raises(shopping_lists):
    respx.delete(f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/tags/").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )

    with pytest.raises(ShoppingListItemNotFoundError):
        await shopping_lists.remove_tags_from_item(
            TOKEN,
            ShoppingListId(LIST_ID),
            ShoppingListItemId(ITEM_ID),
            [TagId(uuid4())],
        )
