"""E2E tests for the purchases pages.

These drive a real browser against the in-process `StubDRFServer`, so they cover
what an `httpx`-mocked integration test cannot: that the nav link reaches a page
that renders, that a tick actually posts to the *nested* item route rather than
the detail one, and that Alpine re-renders the page from the server's answer
rather than from a guess.

The DRF boundary is stubbed rather than `page.route()`d, per the module note in
`drf_stub.py`: every DRF call is made by Flask through `httpx`, never by the
browser, so a Playwright route would mock nothing while the suite appeared to
pass.

**Payloads are the backend's.** `status` is a string (`"pending"` /
`"purchased"`), `estimated_price` is a decimal rendered as a string, and
`completed_at` is null on a list nobody has finished. Every payload here is
shaped that way, and the assertions depend on it.
"""

from __future__ import annotations

import re
from uuid import uuid4

from playwright.sync_api import Page, expect

from tests.e2e.drf_stub import StubResponse
from tests.e2e.support import expect_path, set_auth_cookies

LIST_ID = "11111111-1111-1111-1111-111111111111"
OTHER_LIST_ID = "22222222-2222-2222-2222-222222222222"
ITEM_ID = "33333333-3333-3333-3333-333333333333"
SECOND_ITEM_ID = "44444444-4444-4444-4444-444444444444"
WISHLIST_ITEM_ID = "55555555-5555-5555-5555-555555555555"


def _tag(name: str = "Home") -> dict:
    return {"id": str(uuid4()), "name": name, "slug": name.lower()}


def list_item(**overrides) -> dict:
    """One `ShoppingListItemSerializer` payload."""
    payload = {
        "id": ITEM_ID,
        "name": "Milk",
        "quantity": 2,
        "details": "Semi-skimmed",
        # A string, not a boolean. The purchases serialisers declare
        # `ChoiceField(choices=ITEM_STATUS_CHOICES)` and there is no
        # `is_purchased` on the wire at all.
        "status": "pending",
        "tags": [],
    }
    payload.update(overrides)
    return payload


def shopping_list(**overrides) -> dict:
    """One `ShoppingListSerializer` payload, items nested."""
    payload = {
        "id": LIST_ID,
        "name": "Weekly groceries",
        "priority": "medium",
        "status": "pending",
        "is_active": True,
        "created_at": "2026-10-01T09:00:00Z",
        "completed_at": None,
        "items": [list_item()],
        "tags": [],
    }
    payload.update(overrides)
    return payload


def wishlist_item(**overrides) -> dict:
    """One `WishlistItemSerializer` payload."""
    payload = {
        "id": WISHLIST_ITEM_ID,
        "name": "Espresso machine",
        "link": "https://example.com/machine",
        "image_link": None,
        "description": None,
        # A decimal as a string: DRF's COERCE_DECIMAL_TO_STRING is on by default.
        "estimated_price": "399.90",
        "priority": "must_have",
        "status": "pending",
        "tags": [],
    }
    payload.update(overrides)
    return payload


def seed_lists(mock_drf, lists: list[dict] | None = None) -> None:
    """Put rows on the stub for the active and archived list reads.

    Deep-copied, as `seed_tasks` documents: the tick handler mutates the stored
    rows in place, so a test that seeded a module-level constant would have that
    constant rewritten by its own first click.
    """
    from copy import deepcopy

    mock_drf.backend.shopping_lists = [
        deepcopy(row) for row in (lists if lists is not None else [shopping_list()])
    ]


def seed_wishlist(mock_drf, items: list[dict] | None = None, **overrides) -> None:
    """Give the stub a wishlist the caller already owns."""
    from copy import deepcopy

    mock_drf.backend.wishlist = {
        "id": str(uuid4()),
        "name": "My Wishlist",
        "status": "pending",
        "is_active": True,
        "created_at": "2026-10-01T09:00:00Z",
        "completed_at": None,
        "items": deepcopy(items if items is not None else [wishlist_item()]),
        **overrides,
    }


def seed_profile(mock_drf, currency: str = "USD", language: str = "en") -> None:
    """Pin the profile the stub serves, so money formatting is deterministic."""
    mock_drf.seed_profile({"currency": currency, "language": language})


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #
class TestNavigation:
    def test_the_nav_link_reaches_the_shopping_lists_overview(
        self, page: Page, mock_drf
    ):
        seed_lists(mock_drf)
        set_auth_cookies(page)
        page.goto("/")

        page.get_by_role("link", name="Purchases").click()

        expect_path(page, "/purchases/")
        expect(page.get_by_role("heading", name="Shopping lists")).to_be_visible()

    def test_the_overview_links_through_to_the_wishlist(self, page: Page, mock_drf):
        seed_lists(mock_drf)
        seed_wishlist(mock_drf)
        set_auth_cookies(page)
        page.goto("/purchases/")

        page.get_by_role("link", name="Wishlist").click()

        expect_path(page, "/purchases/wishlist/")
        expect(page.get_by_role("heading", name="My Wishlist")).to_be_visible()


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
class TestListsOverview:
    def test_each_active_list_is_a_card(self, page: Page, mock_drf):
        seed_lists(mock_drf)
        set_auth_cookies(page)

        page.goto("/purchases/")

        expect(page.get_by_test_id("shopping-list-card")).to_have_count(1)
        expect(page.get_by_text("Weekly groceries")).to_be_visible()

    def test_a_card_links_to_its_checklist(self, page: Page, mock_drf):
        seed_lists(mock_drf)
        set_auth_cookies(page)
        page.goto("/purchases/")

        page.get_by_test_id("shopping-list-card").click()

        expect_path(page, f"/purchases/lists/{LIST_ID}/")
        expect(page.get_by_test_id("list-item")).to_have_count(1)

    def test_the_empty_state_when_there_are_no_lists(self, page: Page, mock_drf):
        seed_lists(mock_drf, [])
        set_auth_cookies(page)

        page.goto("/purchases/")

        expect(page.get_by_text("No active shopping lists.")).to_be_visible()


# --------------------------------------------------------------------------- #
# The checklist
# --------------------------------------------------------------------------- #
class TestListDetail:
    def test_items_render_with_their_details(self, page: Page, mock_drf):
        seed_lists(mock_drf)
        set_auth_cookies(page)

        page.goto(f"/purchases/lists/{LIST_ID}/")

        expect(page.get_by_test_id("list-item")).to_have_count(1)
        expect(page.get_by_text("Semi-skimmed")).to_be_visible()

    def test_a_tick_posts_to_the_nested_item_route_and_strikes_it_through(
        self, page: Page, mock_drf
    ):
        """The one assertion that cannot be made from the respx suite alone.

        The route is `/shopping-lists/<list>/items/<item>/mark-purchased/`, and a
        BFF that posted to the detail route instead would be refused with a 404
        for a reason that looks like a missing row. The stub records the request,
        so the path itself is asserted -- not merely that the page changed.
        """
        seed_lists(mock_drf)
        set_auth_cookies(page)
        page.goto(f"/purchases/lists/{LIST_ID}/")

        page.get_by_test_id("mark-purchased").first.click()

        page.wait_for_load_state("networkidle")
        calls = mock_drf.calls(
            "POST",
            f"/purchases/shopping-lists/{LIST_ID}/items/{ITEM_ID}/mark-purchased/",
        )
        assert len(calls) == 1, "the tick did not post to the nested item route"
        # The strike-through is the visible consequence, and it is asserted after
        # the reload the `fetch` triggers -- so the page really re-rendered from
        # the server's answer.
        expect(page.get_by_role("heading", name="Bought")).to_be_visible()
        expect(page.locator(".line-through").first).to_be_visible()

    def test_ticking_the_last_item_completes_the_list(self, page: Page, mock_drf):
        # The backend recomputes the list's own `status` and `completed_at` on the
        # same response, and only that response knows the tick finished the list.
        seed_lists(mock_drf, [shopping_list(items=[list_item()])])
        set_auth_cookies(page)
        page.goto(f"/purchases/lists/{LIST_ID}/")

        page.get_by_test_id("mark-purchased").first.click()
        page.wait_for_load_state("networkidle")

        expect(page.get_by_test_id("list-summary")).to_contain_text("completed")
        # And the rows agree with the summary: nothing left under "To buy".
        expect(page.get_by_role("heading", name="To buy")).to_be_visible()
        expect(page.get_by_test_id("list-item")).to_have_count(1)
        expect(page.get_by_test_id("mark-purchased")).to_have_count(0)

    def test_adding_an_item_lands_it_on_the_checklist(self, page: Page, mock_drf):
        seed_lists(mock_drf, [shopping_list(items=[])])
        set_auth_cookies(page)
        page.goto(f"/purchases/lists/{LIST_ID}/")

        page.get_by_role("button", name="Add item").click()
        page.get_by_label("Item").fill("Bread")
        page.get_by_label("Details").fill("Sourdough")
        page.get_by_role("button", name="Add", exact=True).click()

        expect_path(page, f"/purchases/lists/{LIST_ID}/")
        expect(page.get_by_test_id("list-item")).to_have_count(1)
        row = page.get_by_test_id("list-item").first
        expect(row).to_contain_text("Bread")
        expect(row).to_contain_text("Sourdough")

    def test_a_quantity_of_zero_is_refused_without_a_network_call(
        self, page: Page, mock_drf
    ):
        # The form's own `min="1"` would block this in the browser, so the POST is
        # issued through the shared request context to exercise the server-side
        # contract rather than asserting the client-side guard twice.
        seed_lists(mock_drf, [shopping_list(items=[])])
        set_auth_cookies(page)
        page.goto(f"/purchases/lists/{LIST_ID}/")

        token = page.locator(
            "form[action$='/items/add/'] input[name='csrf_token']"
        ).first.input_value()
        response = page.request.post(
            f"/purchases/lists/{LIST_ID}/items/add/",
            form={"csrf_token": token, "name": "Milk", "quantity": "0"},
            timeout=10_000,
        )

        assert "less than one" in response.text()
        assert not mock_drf.calls(
            "POST", f"/purchases/shopping-lists/{LIST_ID}/add-item/"
        )

    def test_a_list_that_is_not_the_callers_redirects_with_a_flash(
        self, page: Page, mock_drf
    ):
        # The stub answers 404 for a list id it does not hold, exactly as the
        # backend does for both "missing" and "not yours".
        seed_lists(mock_drf, [shopping_list()])
        set_auth_cookies(page)

        page.goto(f"/purchases/lists/{OTHER_LIST_ID}/")

        expect_path(page, "/purchases/")
        expect(page.get_by_text("That shopping list is not available.")).to_be_visible()


# --------------------------------------------------------------------------- #
# Creating a list
# --------------------------------------------------------------------------- #
class TestCreateList:
    def test_the_overview_form_creates_a_list(self, page: Page, mock_drf):
        seed_lists(mock_drf, [])
        set_auth_cookies(page)
        page.goto("/purchases/")

        page.get_by_role("button", name="New list").click()
        page.get_by_label("List name").fill("Hardware store")
        page.get_by_label("Priority").select_option("high")
        page.get_by_role("button", name="Create").click()

        expect_path(page, "/purchases/")
        expect(page.get_by_test_id("shopping-list-card")).to_have_count(1)
        expect(page.get_by_test_id("shopping-list-card").first).to_contain_text(
            "Hardware store"
        )
        # The card links to a page that exists -- a badge pointing at a 404 is the
        # failure an `href` assertion is for.
        expect(page.get_by_test_id("shopping-list-card")).to_have_attribute(
            "href", re.compile(r"^/purchases/lists/[0-9a-f-]+/$")
        )

    def test_a_blank_name_is_refused_and_nothing_is_created(self, page: Page, mock_drf):
        seed_lists(mock_drf, [])
        set_auth_cookies(page)
        page.goto("/purchases/")

        token = page.locator(
            "form[action$='/lists/create/'] input[name='csrf_token']"
        ).first.input_value()
        response = page.request.post(
            "/purchases/lists/create/",
            form={"csrf_token": token, "name": "   ", "priority": "medium"},
            timeout=10_000,
        )

        assert "cannot be empty" in response.text()
        assert not mock_drf.calls("POST", "/purchases/shopping-lists/")


# --------------------------------------------------------------------------- #
# The wishlist
# --------------------------------------------------------------------------- #
class TestWishlist:
    def test_cards_render_with_the_price_and_the_priority_badge(
        self, page: Page, mock_drf
    ):
        seed_wishlist(mock_drf)
        seed_profile(mock_drf)
        set_auth_cookies(page)

        page.goto("/purchases/wishlist/")

        expect(page.get_by_test_id("wishlist-card")).to_have_count(1)
        expect(page.get_by_test_id("estimated-price")).to_have_text("$399.90")
        expect(page.get_by_test_id("priority-badge")).to_have_text("Must have")

    def test_an_item_without_an_estimate_renders_a_dash_not_a_zero(
        self, page: Page, mock_drf
    ):
        seed_wishlist(mock_drf, [wishlist_item(estimated_price=None)])
        seed_profile(mock_drf)
        set_auth_cookies(page)

        page.goto("/purchases/wishlist/")

        expect(page.get_by_test_id("estimated-price")).to_have_text("—")

    def test_the_price_follows_the_profile_currency(self, page: Page, mock_drf):
        # Money formatting is the profile context's preference reaching a card
        # here, with no import between the two contexts.
        seed_wishlist(mock_drf)
        seed_profile(mock_drf, currency="BRL", language="pt-BR")
        set_auth_cookies(page)

        page.goto("/purchases/wishlist/")

        expect(page.get_by_test_id("estimated-price")).to_have_text("R$399,90")

    def test_a_new_user_gets_an_empty_wishlist_rather_than_an_error(
        self, page: Page, mock_drf
    ):
        # `GET /wishlist/` is a get-or-create, so a user who has never had one
        # still gets a page.
        mock_drf.backend.wishlist = None
        set_auth_cookies(page)

        page.goto("/purchases/wishlist/")

        expect(page.get_by_text("Nothing on the wishlist yet.")).to_be_visible()

    def test_a_tick_posts_to_the_wishlist_item_route(self, page: Page, mock_drf):
        seed_wishlist(mock_drf)
        seed_profile(mock_drf)
        set_auth_cookies(page)
        page.goto("/purchases/wishlist/")

        page.get_by_test_id("mark-purchased").first.click()
        page.wait_for_load_state("networkidle")

        calls = mock_drf.calls(
            "POST",
            f"/purchases/wishlist/items/{WISHLIST_ITEM_ID}/mark-purchased/",
        )
        assert len(calls) == 1, "the tick did not post to the wishlist item route"
        expect(page.get_by_text("Bought").first).to_be_visible()

    def test_ticking_the_last_item_completes_the_wishlist(self, page: Page, mock_drf):
        seed_wishlist(mock_drf, [wishlist_item()])
        seed_profile(mock_drf)
        set_auth_cookies(page)
        page.goto("/purchases/wishlist/")

        page.get_by_test_id("mark-purchased").first.click()
        page.wait_for_load_state("networkidle")

        expect(page.get_by_test_id("wishlist-summary")).to_contain_text("completed")

    def test_the_add_form_creates_an_item_with_its_price(self, page: Page, mock_drf):
        seed_wishlist(mock_drf, [])
        seed_profile(mock_drf)
        set_auth_cookies(page)
        page.goto("/purchases/wishlist/")

        page.get_by_role("button", name="Add item").click()
        page.get_by_label("Item").fill("Espresso machine")
        page.get_by_label("Estimated price").fill("399.90")
        page.get_by_label("Priority").select_option("must_have")
        page.get_by_role("button", name="Add to wishlist").click()

        expect_path(page, "/purchases/wishlist/")
        expect(page.get_by_test_id("wishlist-card")).to_have_count(1)
        expect(page.get_by_test_id("wishlist-card").first).to_contain_text(
            "Espresso machine"
        )

    def test_a_negative_price_is_refused_and_no_item_is_created(
        self, page: Page, mock_drf
    ):
        seed_wishlist(mock_drf, [])
        seed_profile(mock_drf)
        set_auth_cookies(page)
        page.goto("/purchases/wishlist/")

        token = page.locator(
            "form[action$='/wishlist/items/add/'] input[name='csrf_token']"
        ).first.input_value()
        response = page.request.post(
            "/purchases/wishlist/items/add/",
            form={
                "csrf_token": token,
                "name": "Lamp",
                "estimated_price": "-1.00",
                "priority": "wanted",
            },
            timeout=10_000,
        )

        assert "cannot be negative" in response.text()
        assert not mock_drf.calls("POST", "/purchases/wishlist/add-item/")

    def test_the_image_is_rendered_when_the_item_has_one(self, page: Page, mock_drf):
        """Same-origin on purpose.

        `tests/e2e/conftest.py` refuses to let the browser reach an external
        host, so a wishlist image hosted on `example.com` would fail this for a
        reason that has nothing to do with the card. The URL is built from the
        page's own origin instead: the BFF answers 404 for it, which is fine --
        the assertion is that the `<img>` was rendered at all, and a card with
        no `image_link` renders a placeholder rather than a tag.
        """
        set_auth_cookies(page)
        page.goto("/purchases/wishlist/")
        origin = page.evaluate("window.location.origin")
        image_url = f"{origin}/wishlist-image.png"

        seed_wishlist(mock_drf, [wishlist_item(image_link=image_url)])
        seed_profile(mock_drf)
        page.reload()

        expect(page.locator(f'img[src="{image_url}"]')).to_have_count(1)

    def test_a_card_without_an_image_renders_no_img_tag(self, page: Page, mock_drf):
        # No `image_link` is the common case -- the API's field is nullable and a
        # wishlist typed from memory rarely has a product photo to hand.
        seed_wishlist(mock_drf, [wishlist_item(image_link=None)])
        seed_profile(mock_drf)
        set_auth_cookies(page)

        page.goto("/purchases/wishlist/")

        expect(page.locator("img")).to_have_count(0)
        expect(page.get_by_test_id("wishlist-card")).to_have_count(1)


# --------------------------------------------------------------------------- #
# Degradation
# --------------------------------------------------------------------------- #
class TestDegradation:
    def test_a_failing_list_read_never_renders_an_empty_overview(
        self, page: Page, mock_drf
    ):
        """A failed read must not look like "you have no lists".

        The overview's empty state is a real state a real user reaches, so a
        backend failure quietly rendering it would tell someone with five lists
        that they have none -- and they would go and buy nothing. The list read
        *is* the page, so its failure propagates instead of being degraded away.

        Note what this is **not** asserting: a 503 page. `DRFAPIClient` raises
        `ServiceUnavailableError` only for a transport failure, where no response
        existed at all; a 5xx *from* the backend is a `DRFAPIClientError` and
        surfaces as a 500. That is deliberate -- AGENTS.md forbids dressing up a
        500, because a 500 means a bug in the BFF and a branded page would hide
        the traceback.
        """
        mock_drf.on(
            "GET",
            "/purchases/shopping-lists/",
            StubResponse(500, {"detail": "Boom."}),
        )
        set_auth_cookies(page)

        page.goto("/purchases/")

        expect(page.get_by_text("No active shopping lists.")).to_have_count(0)
        expect(page.get_by_test_id("shopping-list-card")).to_have_count(0)

    def test_a_stale_tick_reports_that_the_row_is_gone(self, page: Page, mock_drf):
        """A 4xx, not 200-with-a-flag.

        The button's strike-through is applied optimistically and reverted on a
        failure, and that branch reads the *status*. A stub answering 200 with
        `{"ok": false}` would let a client that trusts a body field instead of
        the status look correct here.
        """
        seed_lists(mock_drf, [shopping_list(items=[list_item()])])
        mock_drf.on(
            "POST",
            f"/purchases/shopping-lists/{LIST_ID}/items/{ITEM_ID}/mark-purchased/",
            StubResponse(404, {"detail": "Not found."}),
        )
        set_auth_cookies(page)
        page.goto(f"/purchases/lists/{LIST_ID}/")

        page.get_by_test_id("mark-purchased").first.click()

        expect(page.get_by_test_id("tick-error")).to_contain_text("no longer exists")
        # The row is still there and still pending: the optimistic tick reverted.
        expect(page.get_by_test_id("list-item")).to_have_count(1)
        expect(page.get_by_test_id("mark-purchased")).to_have_count(1)
