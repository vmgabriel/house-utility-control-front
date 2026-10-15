"""Unit tests for the purchases use cases, with the ports stubbed by hand.

Fakes rather than `unittest.mock.AsyncMock` for the ports, and the reason is
worth stating: these use cases pass *entities* across the port boundary, so what
a test needs to control is a store it can inspect afterwards. An `AsyncMock`
records calls but hands back whatever it was told to, which turns every
assertion into a restatement of the mock's own configuration -- a use case that
dropped the list it fetched would still pass, because the assertion is against
the value the test injected, not the value the use case returned.

The fakes below hold real `ShoppingList` / `Wishlist` objects and record the
arguments they were called with, so a test can assert on both.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

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
from src.purchases.domain.entities import (
    ShoppingList,
    ShoppingListItem,
    Tag,
    Wishlist,
    WishlistItem,
)
from src.purchases.domain.exceptions import (
    InvalidPurchaseError,
    InvalidPurchasesInputError,
    ShoppingListNotFoundError,
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
    normalize_text,
)

TOKEN = "purchases-access-token"


def _tag(name: str = "Home") -> Tag:
    return Tag(id=TagId(uuid4()), name=name, slug=name.lower())


def _item(purchased: bool = False) -> ShoppingListItem:
    return ShoppingListItem(
        id=ShoppingListItemId(uuid4()),
        name="Milk",
        quantity=Quantity(2),
        status=ItemStatus.PURCHASED if purchased else ItemStatus.PENDING,
        tags=[_tag()],
    )


def _list(items: list[ShoppingListItem] | None = None, **overrides) -> ShoppingList:
    fields = {
        "id": ShoppingListId(uuid4()),
        "name": "Groceries",
        "priority": ListPriority.MEDIUM,
        "status": ItemStatus.PENDING,
        "items": list(items or []),
    }
    fields.update(overrides)
    return ShoppingList(**fields)


def _wishlist_item(purchased: bool = False) -> WishlistItem:
    return WishlistItem(
        id=WishlistItemId(uuid4()),
        name="Espresso machine",
        priority=ItemPriority.MUST_HAVE,
        status=ItemStatus.PURCHASED if purchased else ItemStatus.PENDING,
        estimated_price=EstimatedPrice("399.90"),
    )


def _wishlist(items: list[WishlistItem] | None = None) -> Wishlist:
    return Wishlist(
        id=WishlistId(uuid4()),
        name="My Wishlist",
        status=ItemStatus.PENDING,
        items=list(items or []),
    )


class FakeShoppingLists:
    """In-memory stand-in for `ShoppingListRepositoryPort`."""

    def __init__(self, lists: dict[str, ShoppingList] | None = None) -> None:
        self.lists: dict[str, ShoppingList] = dict(lists or {})
        self.inactive: dict[str, ShoppingList] = {}
        self.calls: list[tuple] = []

    def _get(self, list_id) -> ShoppingList | None:
        return self.lists.get(str(list_id))

    async def get_active_lists(self, access_token):
        self.calls.append(("get_active_lists", access_token))
        return list(self.lists.values())

    async def get_inactive_lists(self, access_token):
        self.calls.append(("get_inactive_lists", access_token))
        return list(self.inactive.values())

    async def get_list(self, access_token, list_id):
        self.calls.append(("get_list", access_token, list_id))
        return self._get(list_id)

    async def create_list(self, access_token, name, priority):
        self.calls.append(("create_list", access_token, name, priority))
        created = _list(name=name, priority=priority)
        self.lists[str(created.id)] = created
        return created

    async def add_item(self, access_token, list_id, *, name, quantity=1, details=None):
        self.calls.append(("add_item", access_token, list_id, name, quantity, details))
        found = self._get(list_id)
        assert found is not None, "the fake only ever holds lists it was seeded with"
        return _list(found.items + [_item()], id=found.id, name=found.name)

    async def mark_item_purchased(self, access_token, list_id, item_id):
        self.calls.append(("mark_item_purchased", access_token, list_id, item_id))
        found = self._get(list_id)
        assert found is not None
        ticked = [
            ShoppingListItem(
                id=existing.id,
                name=existing.name,
                quantity=existing.quantity,
                status=(
                    ItemStatus.PURCHASED if existing.id == item_id else existing.status
                ),
                tags=existing.tags,
            )
            for existing in found.items
        ]
        completed = all(item.status.is_purchased for item in ticked)
        updated = _list(
            ticked,
            id=found.id,
            name=found.name,
            status=ItemStatus.PURCHASED if completed else ItemStatus.PENDING,
        )
        self.lists[str(list_id)] = updated
        return updated


class FakeWishlist:
    """In-memory stand-in for `WishlistRepositoryPort`."""

    def __init__(self, wishlist: Wishlist | None = None) -> None:
        self.wishlist = wishlist or _wishlist()
        self.calls: list[tuple] = []

    async def get_or_create_wishlist(self, access_token):
        self.calls.append(("get_or_create_wishlist", access_token))
        return self.wishlist

    async def add_item(
        self,
        access_token,
        *,
        name,
        link=None,
        image_link=None,
        description=None,
        estimated_price=None,
        priority=ItemPriority.WANTED,
    ):
        self.calls.append(
            (
                "add_item",
                access_token,
                name,
                link,
                image_link,
                description,
                estimated_price,
                priority,
            )
        )
        added = WishlistItem(
            id=WishlistItemId(uuid4()),
            name=name,
            priority=priority,
            link=link,
            image_link=image_link,
            description=description,
            estimated_price=estimated_price,
        )
        return Wishlist(
            id=self.wishlist.id,
            name=self.wishlist.name,
            status=self.wishlist.status,
            items=self.wishlist.items + [added],
        )

    async def mark_item_purchased(self, access_token, item_id):
        self.calls.append(("mark_item_purchased", access_token, item_id))
        ticked = [
            WishlistItem(
                id=existing.id,
                name=existing.name,
                priority=existing.priority,
                status=(
                    ItemStatus.PURCHASED if existing.id == item_id else existing.status
                ),
                estimated_price=existing.estimated_price,
            )
            for existing in self.wishlist.items
        ]
        updated = Wishlist(
            id=self.wishlist.id,
            name=self.wishlist.name,
            status=self.wishlist.status,
            items=ticked,
        )
        self.wishlist = updated
        return updated


class FakeTags:
    def __init__(self, tags: list[Tag] | None = None) -> None:
        self.tags = list(tags or [])
        self.calls: list[tuple] = []

    async def get_all_tags(self, access_token):
        self.calls.append(("get_all_tags", access_token))
        return list(self.tags)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
class TestReads:
    async def test_active_lists_are_returned_verbatim(self):
        shopping_list = _list([_item()])
        repo = FakeShoppingLists({str(shopping_list.id): shopping_list})

        result = await GetActiveShoppingListsUseCase(shopping_lists=repo).execute(TOKEN)

        assert result == [shopping_list]

    async def test_archived_lists_are_a_separate_read_not_a_filter(self):
        # The backend serves them from a different endpoint, so a use case that
        # filtered the active read instead would be reading the wrong thing.
        repo = FakeShoppingLists()
        result = await GetArchivedShoppingListsUseCase(shopping_lists=repo).execute(
            TOKEN
        )

        assert result == []
        assert ("get_inactive_lists", TOKEN) in repo.calls

    async def test_get_list_returns_the_aggregate(self):
        shopping_list = _list([_item()])
        repo = FakeShoppingLists({str(shopping_list.id): shopping_list})

        result = await GetShoppingListUseCase(shopping_lists=repo).execute(
            TOKEN, shopping_list.id
        )

        assert result is shopping_list

    async def test_get_list_raises_for_a_list_that_is_not_the_callers(self):
        # The port answers `None` for "missing" and "not yours" alike -- the
        # backend sends the same 404 -- and the use case has to turn that into a
        # message the view can flash without revealing which case it was.
        use_case = GetShoppingListUseCase(shopping_lists=FakeShoppingLists())

        with pytest.raises(ShoppingListNotFoundError, match="not available"):
            await use_case.execute(TOKEN, ShoppingListId(uuid4()))

    async def test_wishlist_read_never_raises_for_a_missing_wishlist(self):
        # `GET /wishlist/` creates the single list on first access, so there is
        # no not-found branch to take here at all.
        repo = FakeWishlist()
        result = await GetWishlistUseCase(wishlist=repo).execute(TOKEN)

        assert isinstance(result, Wishlist)
        assert repo.calls == [("get_or_create_wishlist", TOKEN)]

    async def test_tag_catalogue_is_returned_verbatim(self):
        tags = [_tag("Home"), _tag("Gift")]
        repo = FakeTags(tags)

        result = await ListTagsUseCase(tags=repo).execute(TOKEN)

        assert result == tags


# --------------------------------------------------------------------------- #
# Creating a list
# --------------------------------------------------------------------------- #
class TestCreateShoppingList:
    async def test_passes_the_name_and_priority_through(self):
        repo = FakeShoppingLists()

        created = await CreateShoppingListUseCase(shopping_lists=repo).execute(
            TOKEN, name="Weekly groceries", priority=ListPriority.HIGH
        )

        assert created.name == "Weekly groceries"
        assert created.priority is ListPriority.HIGH
        assert (
            "create_list",
            TOKEN,
            "Weekly groceries",
            ListPriority.HIGH,
        ) in repo.calls

    async def test_defaults_the_priority_to_medium_like_the_api_does(self):
        # `CreateShoppingListSerializer` defaults to medium; sending the default
        # explicitly means the stored value and the rendered badge come from one
        # decision rather than from two defaults that happen to agree.
        repo = FakeShoppingLists()
        await CreateShoppingListUseCase(shopping_lists=repo).execute(TOKEN, name="Shop")
        assert repo.calls[-1][3] is ListPriority.MEDIUM

    async def test_collapses_whitespace_before_sending(self):
        repo = FakeShoppingLists()
        await CreateShoppingListUseCase(shopping_lists=repo).execute(
            TOKEN, name="  Weekly   groceries  "
        )
        assert repo.calls[-1][2] == "Weekly groceries"

    async def test_rejects_a_blank_name_without_calling_the_port(self):
        repo = FakeShoppingLists()

        with pytest.raises(InvalidPurchasesInputError, match="cannot be empty"):
            await CreateShoppingListUseCase(shopping_lists=repo).execute(
                TOKEN, name="  "
            )

        assert repo.calls == [], "a blank name must not reach the network"

    async def test_a_domain_refusal_arrives_as_an_input_error(self):
        # Both are DomainExceptions so the view words them the same way, but the
        # type is what says the failure happened before any I/O. The value
        # object's own error is what gets translated, so this is a real
        # translation rather than two names for one thing.
        repo = FakeShoppingLists()
        use_case = CreateShoppingListUseCase(shopping_lists=repo)

        with pytest.raises(InvalidPurchasesInputError, match="cannot exceed"):
            await use_case.execute(TOKEN, name="x" * 201)

        assert repo.calls == []

        with pytest.raises(InvalidPurchaseError, match="cannot exceed"):
            normalize_text("x" * 201, limit=200, label="Shopping list name")

    async def test_rejects_a_priority_that_is_not_an_enum_member(self):
        repo = FakeShoppingLists()
        use_case = CreateShoppingListUseCase(shopping_lists=repo)

        with pytest.raises(InvalidPurchasesInputError, match="known level"):
            await use_case.execute(TOKEN, name="Shop", priority="urgent")

        assert repo.calls == []


# --------------------------------------------------------------------------- #
# Adding items
# --------------------------------------------------------------------------- #
class TestAddItemToShoppingList:
    async def test_sends_the_name_quantity_and_details(self):
        repo = FakeShoppingLists()
        shopping_list = _list()
        repo.lists[str(shopping_list.id)] = shopping_list

        await AddItemToShoppingListUseCase(shopping_lists=repo).execute(
            TOKEN,
            shopping_list.id,
            name="  Milk  ",
            quantity=3,
            details="  Semi-skimmed  ",
        )

        call = next(entry for entry in repo.calls if entry[0] == "add_item")
        assert call[3] == "Milk"
        assert call[4] == 3
        assert call[5] == "Semi-skimmed"

    async def test_defaults_the_quantity_to_one(self):
        repo = FakeShoppingLists()
        shopping_list = _list()
        repo.lists[str(shopping_list.id)] = shopping_list

        await AddItemToShoppingListUseCase(shopping_lists=repo).execute(
            TOKEN, shopping_list.id, name="Milk"
        )

        assert next(e for e in repo.calls if e[0] == "add_item")[4] == 1

    async def test_maps_a_blank_details_field_to_none(self):
        # A cleared form input posts "", and the backend's `_optional_str` maps
        # that to `None` too -- so sending `None` here means the same thing
        # without a field the API would only discard.
        repo = FakeShoppingLists()
        shopping_list = _list()
        repo.lists[str(shopping_list.id)] = shopping_list

        await AddItemToShoppingListUseCase(shopping_lists=repo).execute(
            TOKEN, shopping_list.id, name="Milk", details="   "
        )

        assert next(e for e in repo.calls if e[0] == "add_item")[5] is None

    @pytest.mark.parametrize("quantity", [0, -1])
    async def test_rejects_a_quantity_below_one_without_calling_the_port(
        self, quantity
    ):
        # Zero is a plausible thing to type -- it reads as "not counting this
        # one" -- and the API's `min_value=1` would answer 400 with a message the
        # user cannot trace. Refusing here names the field instead.
        repo = FakeShoppingLists()
        shopping_list = _list()
        repo.lists[str(shopping_list.id)] = shopping_list

        with pytest.raises(InvalidPurchasesInputError, match="less than one"):
            await AddItemToShoppingListUseCase(shopping_lists=repo).execute(
                TOKEN, shopping_list.id, name="Milk", quantity=quantity
            )

        assert repo.calls == []

    async def test_rejects_a_blank_name_without_calling_the_port(self):
        repo = FakeShoppingLists()
        shopping_list = _list()
        repo.lists[str(shopping_list.id)] = shopping_list

        with pytest.raises(InvalidPurchasesInputError, match="cannot be empty"):
            await AddItemToShoppingListUseCase(shopping_lists=repo).execute(
                TOKEN, shopping_list.id, name=" "
            )

        assert repo.calls == []

    async def test_returns_the_reloaded_list_rather_than_the_new_item(self):
        # The endpoint answers with the whole aggregate, and that response is the
        # only place the new item's id appears.
        repo = FakeShoppingLists()
        shopping_list = _list()
        repo.lists[str(shopping_list.id)] = shopping_list

        result = await AddItemToShoppingListUseCase(shopping_lists=repo).execute(
            TOKEN, shopping_list.id, name="Milk"
        )

        assert isinstance(result, ShoppingList)
        assert len(result.items) == 1

    async def test_rejects_a_list_id_that_is_not_a_shopping_list_id(self):
        # Not coerced from a string: a wrong id means the template and the domain
        # have drifted, and guessing would address whichever list the string
        # happened to parse as.
        repo = FakeShoppingLists()

        with pytest.raises(InvalidPurchasesInputError, match="ShoppingListId"):
            await AddItemToShoppingListUseCase(shopping_lists=repo).execute(
                TOKEN, str(uuid4()), name="Milk"
            )


# --------------------------------------------------------------------------- #
# Marking as purchased
# --------------------------------------------------------------------------- #
class TestMarkShoppingListItemPurchased:
    async def test_returns_the_reloaded_list_with_the_item_ticked(self):
        bought = _item(purchased=True)
        pending = _item()
        shopping_list = _list([bought, pending])
        repo = FakeShoppingLists({str(shopping_list.id): shopping_list})

        result = await MarkShoppingListItemPurchasedUseCase(
            shopping_lists=repo
        ).execute(TOKEN, shopping_list.id, pending.id)

        assert result.items[1].is_purchased is True

    async def test_reports_completion_when_the_last_item_is_ticked(self):
        # The completion flag comes from the backend's own recomputation, which is
        # why the *list* is the return value: a local re-derivation would have to
        # restate the backend's rule that an empty list is never complete.
        only = _item()
        shopping_list = _list([only])
        repo = FakeShoppingLists({str(shopping_list.id): shopping_list})

        result = await MarkShoppingListItemPurchasedUseCase(
            shopping_lists=repo
        ).execute(TOKEN, shopping_list.id, only.id)

        assert result.is_completed is True

    async def test_the_list_is_not_complete_while_an_item_is_still_pending(self):
        # The response's `status` is the backend's own recomputation. The fake
        # mirrors its rule -- completed only when every item is bought -- so a
        # use case that derived completion locally from the *item count alone*
        # would be caught here reporting True on a half-bought list.
        bought = _item(purchased=True)
        pending = _item()
        shopping_list = _list([bought, pending])
        repo = FakeShoppingLists({str(shopping_list.id): shopping_list})

        # Re-tick an already-bought item: the list still has a pending item, so
        # it must still read as incomplete.
        result = await MarkShoppingListItemPurchasedUseCase(
            shopping_lists=repo
        ).execute(TOKEN, shopping_list.id, bought.id)

        assert result.is_completed is False
        assert len(result.purchased_items) == 1
        assert len(result.pending_items) == 1

    async def test_passes_both_ids_through(self):
        # The backend's nested item routes carry the list uuid as well and 404 on
        # a mismatched pair, so the use case has to forward the id the page was
        # rendered under rather than looking it up.
        pending = _item()
        shopping_list = _list([pending])
        repo = FakeShoppingLists({str(shopping_list.id): shopping_list})

        await MarkShoppingListItemPurchasedUseCase(shopping_lists=repo).execute(
            TOKEN, shopping_list.id, pending.id
        )

        assert (
            "mark_item_purchased",
            TOKEN,
            shopping_list.id,
            pending.id,
        ) in repo.calls

    async def test_rejects_ids_of_the_wrong_type(self):
        use_case = MarkShoppingListItemPurchasedUseCase(
            shopping_lists=FakeShoppingLists()
        )
        list_id = ShoppingListId(uuid4())

        with pytest.raises(InvalidPurchasesInputError, match="ShoppingListItemId"):
            await use_case.execute(TOKEN, list_id, str(uuid4()))


class TestMarkWishlistItemPurchased:
    async def test_returns_the_reloaded_wishlist_with_the_item_ticked(self):
        pending = _wishlist_item()
        repo = FakeWishlist(_wishlist([pending]))

        result = await MarkWishlistItemPurchasedUseCase(wishlist=repo).execute(
            TOKEN, pending.id
        )

        assert result.items[0].is_purchased is True

    async def test_rejects_an_id_of_the_wrong_type(self):
        repo = FakeWishlist()
        with pytest.raises(InvalidPurchasesInputError, match="WishlistItemId"):
            await MarkWishlistItemPurchasedUseCase(wishlist=repo).execute(
                TOKEN, str(uuid4())
            )


# --------------------------------------------------------------------------- #
# Adding wishlist items
# --------------------------------------------------------------------------- #
class TestAddItemToWishlist:
    async def test_sends_every_field_through(self):
        repo = FakeWishlist()

        await AddItemToWishlistUseCase(wishlist=repo).execute(
            TOKEN,
            name="  Espresso machine  ",
            link="https://example.com/machine",
            image_link="https://example.com/machine.png",
            description="  For the kitchen  ",
            estimated_price=EstimatedPrice("399.90"),
            priority=ItemPriority.MUST_HAVE,
        )

        call = next(entry for entry in repo.calls if entry[0] == "add_item")
        assert call[2] == "Espresso machine"
        assert call[3] == "https://example.com/machine"
        assert call[4] == "https://example.com/machine.png"
        assert call[5] == "For the kitchen"
        assert call[6].amount == EstimatedPrice("399.90").amount
        assert call[7] is ItemPriority.MUST_HAVE

    async def test_defaults_the_priority_to_wanted_like_the_api_does(self):
        repo = FakeWishlist()
        await AddItemToWishlistUseCase(wishlist=repo).execute(TOKEN, name="Lamp")
        assert repo.calls[-1][7] is ItemPriority.WANTED

    async def test_an_absent_estimate_stays_none(self):
        # Not `EstimatedPrice(0)`: "no estimate" and "estimated at nothing" are
        # different claims, and the API's field is nullable so absence is a value
        # it can express.
        repo = FakeWishlist()
        await AddItemToWishlistUseCase(wishlist=repo).execute(TOKEN, name="Lamp")
        assert repo.calls[-1][6] is None

    @pytest.mark.parametrize("field", ["link", "image_link"])
    async def test_refuses_a_non_http_url(self, field):
        # Both values reach a template `href` or `src`, so a `javascript:` or
        # `data:` URL surviving into either attribute is an injection the browser
        # would execute. Refusing at the edge is the check a browser cannot be
        # relied on to apply.
        repo = FakeWishlist()
        use_case = AddItemToWishlistUseCase(wishlist=repo)

        with pytest.raises(InvalidPurchasesInputError, match="http or https"):
            await use_case.execute(TOKEN, name="Lamp", **{field: "javascript:alert(1)"})

        assert not any(entry[0] == "add_item" for entry in repo.calls)

    async def test_rejects_a_blank_name_without_calling_the_port(self):
        repo = FakeWishlist()
        with pytest.raises(InvalidPurchasesInputError, match="cannot be empty"):
            await AddItemToWishlistUseCase(wishlist=repo).execute(TOKEN, name="  ")
        assert repo.calls == []

    async def test_rejects_a_raw_decimal_for_the_price(self):
        # The non-negativity rule belongs to the value object; a plain decimal
        # handed past it would make that rule optional.
        repo = FakeWishlist()
        with pytest.raises(InvalidPurchasesInputError, match="EstimatedPrice"):
            await AddItemToWishlistUseCase(wishlist=repo).execute(
                TOKEN, name="Lamp", estimated_price="10.00"
            )
        assert repo.calls == []

    async def test_rejects_a_priority_that_is_not_an_enum_member(self):
        repo = FakeWishlist()
        with pytest.raises(InvalidPurchasesInputError, match="known level"):
            await AddItemToWishlistUseCase(wishlist=repo).execute(
                TOKEN, name="Lamp", priority="urgent"
            )
        assert repo.calls == []
