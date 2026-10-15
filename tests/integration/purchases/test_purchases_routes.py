"""Integration tests for the purchases web layer.

The full request path -- Flask routing, CSRF, the use cases, the DRF adapters,
and the mappers -- with only the DRF HTTP boundary mocked by `respx`. Mocking at
the socket is the only level that still tests the adapter's URL construction,
status handling, and payload shapes together, which is where this context has
three separate shapes and two 200-with-an-aggregate responses.

Every response body here is copied from the backend's
`apps/purchases/interfaces/serializers.py`.

**Assertions about page-level markers are scoped to `<main>`** (AGENTS.md, rule
8). The layout in `base.html` owns interactive zones of its own -- the account
dropdown is an `x-data` scope -- so a whole-document count goes off by one
whenever the layout grows. That is also what makes "exactly one Alpine root per
page" enforceable at all: a card-level scope in the wishlist grid would be caught
here, and it would scale with the number of items the user has.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from uuid import uuid4

import httpx
import pytest

from src.interfaces.web.app import create_app
from src.shared.auth.jwt_cookie_manager import ACCESS_COOKIE, REFRESH_COOKIE
from tests.integration.rentals.page_html import main_content

BASE = "http://localhost:8000/api/v1"
LISTS_URL = f"{BASE}/purchases/shopping-lists/"
WISHLIST_URL = f"{BASE}/purchases/wishlist/"

ACCESS_TOKEN = "purchases-access-token"
REFRESH_TOKEN = "purchases-refresh-token"

USER_ID = uuid4()
LIST_ID = uuid4()
ITEM_ID = uuid4()
WISHLIST_ITEM_ID = uuid4()

CSRF_MARKER = 'name="csrf_token" value="'


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

    def today(self) -> date:
        return date(2026, 10, 5)


def tag_payload(name: str = "Home") -> dict:
    return {"id": str(uuid4()), "name": name, "slug": name.lower()}


def list_item_payload(**overrides) -> dict:
    payload = {
        "id": str(ITEM_ID),
        "name": "Milk",
        "quantity": 2,
        "details": "Semi-skimmed",
        "status": "pending",
        "tags": [],
    }
    payload.update(overrides)
    return payload


def shopping_list_payload(**overrides) -> dict:
    payload = {
        "id": str(LIST_ID),
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
        "id": str(WISHLIST_ITEM_ID),
        "name": "Espresso machine",
        "link": "https://example.com/machine",
        "image_link": "https://example.com/machine.png",
        "description": "For the kitchen",
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
def app():
    """An app with a frozen clock, so `today`-dependent rendering is stable."""
    application = create_app()
    application.clock = FrozenClock()
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def authed_client(app):
    with app.test_client() as test_client:
        test_client.set_cookie(ACCESS_COOKIE, ACCESS_TOKEN)
        test_client.set_cookie(REFRESH_COOKIE, REFRESH_TOKEN)
        with test_client.session_transaction() as session:
            session["user_id"] = str(USER_ID)
            session["is_staff"] = False
        yield test_client


def _csrf_from(html: str) -> str:
    start = html.find(CSRF_MARKER)
    if start == -1:
        return ""
    start += len(CSRF_MARKER)
    return html[start : html.find('"', start)]


def _stub_lists(respx_mock, active=(), archived=()) -> None:
    respx_mock.get(LISTS_URL).mock(return_value=httpx.Response(200, json=list(active)))
    respx_mock.get(f"{BASE}/purchases/shopping-lists/inactive/").mock(
        return_value=httpx.Response(200, json=list(archived))
    )


def _stub_list_detail(respx_mock, payload: dict) -> None:
    respx_mock.get(f"{LISTS_URL}{LIST_ID}/").mock(
        return_value=httpx.Response(200, json=payload)
    )


def _stub_wishlist(respx_mock, payload: dict | None = None) -> None:
    respx_mock.get(WISHLIST_URL).mock(
        return_value=httpx.Response(200, json=payload or wishlist_payload())
    )
    respx_mock.get(f"{BASE}/profile/me/").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": str(USER_ID),
                "first_name": "Test",
                "last_name": "User",
                "timezone": "UTC",
                "language": "en",
                "currency": "USD",
                "date_format": "YYYY-MM-DD",
                "avatar_url": None,
                "bio": None,
            },
        )
    )


# --------------------------------------------------------------------------- #
# Overview page
# --------------------------------------------------------------------------- #
class TestListsPage:
    def test_renders_a_card_per_active_list(self, respx_mock, authed_client):
        with respx_mock:
            _stub_lists(respx_mock, [shopping_list_payload()])
            body = authed_client.get("/purchases/").data.decode()

        page = main_content(body)
        assert page.count('data-testid="shopping-list-card"') == 1
        assert "Weekly groceries" in page

    def test_the_card_links_to_the_detail_page(self, respx_mock, authed_client):
        with respx_mock:
            _stub_lists(respx_mock, [shopping_list_payload()])
            body = authed_client.get("/purchases/").data.decode()

        assert f'href="/purchases/lists/{LIST_ID}/"' in main_content(body)

    def test_the_card_shows_the_counts_it_derived_from_the_items(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_lists(
                respx_mock,
                [
                    shopping_list_payload(
                        items=[
                            list_item_payload(status="purchased"),
                            list_item_payload(id=str(uuid4())),
                        ]
                    )
                ],
            )
            body = authed_client.get("/purchases/").data.decode()

        page = main_content(body)
        # The progress bar and the count beside it are two views of one number,
        # computed in the ViewModel from the same item list.
        assert 'aria-valuenow="50"' in page
        pending = re.search(
            r'data-testid="card-pending-count"[^>]*>(\d+)<', page
        ).group(1)
        assert pending == "1"

    def test_an_empty_state_when_there_are_no_lists(self, respx_mock, authed_client):
        with respx_mock:
            _stub_lists(respx_mock)
            body = authed_client.get("/purchases/").data.decode()

        assert "No active shopping lists." in main_content(body)

    def test_archived_lists_are_shown_behind_a_disclosure(
        self, respx_mock, authed_client
    ):
        # A soft-deleted list is still readable, so it still needs a page; but it
        # is not something the user wants at the same weight as an active list.
        with respx_mock:
            _stub_lists(
                respx_mock,
                [shopping_list_payload()],
                [shopping_list_payload(id=str(uuid4()), is_active=False)],
            )
            body = authed_client.get("/purchases/").data.decode()

        page = main_content(body)
        assert "Archived lists" in page
        assert "<details" in page

    def test_the_page_still_has_exactly_one_alpine_scope(
        self, respx_mock, authed_client
    ):
        # Rule 8: scoped to `<main>`, because the layout's account dropdown owns an
        # `x-data` scope of its own and rule 5 explicitly permits it.
        with respx_mock:
            _stub_lists(respx_mock, [shopping_list_payload()])
            body = authed_client.get("/purchases/").data.decode()

        assert main_content(body).count("x-data=") == 1

    def test_an_archived_read_failure_costs_the_panel_not_the_page(
        self, respx_mock, authed_client
    ):
        # The archived section is secondary. Taking the overview down because a
        # secondary read failed would be a self-inflicted outage.
        with respx_mock:
            respx_mock.get(LISTS_URL).mock(
                return_value=httpx.Response(200, json=[shopping_list_payload()])
            )
            respx_mock.get(f"{BASE}/purchases/shopping-lists/inactive/").mock(
                return_value=httpx.Response(500, json={"detail": "boom"})
            )
            body = authed_client.get("/purchases/").data.decode()

        page = main_content(body)
        assert 'data-testid="shopping-list-card"' in page
        assert "unavailable right now" in page

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self, respx_mock, app):
        with respx_mock:
            response = app.test_client().get("/purchases/")

        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


# --------------------------------------------------------------------------- #
# List detail page
# --------------------------------------------------------------------------- #
class TestListDetailPage:
    def test_renders_a_row_per_item_with_its_quantity(self, respx_mock, authed_client):
        with respx_mock:
            _stub_list_detail(respx_mock, shopping_list_payload())
            body = authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()

        page = main_content(body)
        assert page.count('data-testid="list-item"') == 1
        assert "2&times;" in page
        assert "Milk" in page

    def test_a_quantity_of_one_is_not_prefixed(self, respx_mock, authed_client):
        # "1x Milk" is not what the user wrote, and a "1" prefix on every row of a
        # one-item list is noise.
        with respx_mock:
            _stub_list_detail(
                respx_mock,
                shopping_list_payload(items=[list_item_payload(quantity=1)]),
            )
            body = authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()

        page = main_content(body)
        assert "&times;" not in page

    def test_pending_items_come_first_and_bought_ones_are_struck_through(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_list_detail(
                respx_mock,
                shopping_list_payload(
                    items=[
                        list_item_payload(name="Milk"),
                        list_item_payload(
                            id=str(uuid4()), name="Bread", status="purchased"
                        ),
                    ]
                ),
            )
            body = authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()

        page = main_content(body)
        assert page.index("To buy") < page.index("Bought")
        assert "line-through" in page

    def test_a_bought_row_has_no_live_tick(self, respx_mock, authed_client):
        # The backend exposes no "un-purchase" route, so a live button here would
        # silently do nothing.
        with respx_mock:
            _stub_list_detail(
                respx_mock,
                shopping_list_payload(
                    status="purchased",
                    completed_at="2026-10-05T14:31:12.480000Z",
                    items=[list_item_payload(status="purchased")],
                ),
            )
            body = authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()

        page = main_content(body)
        assert 'data-testid="mark-purchased"' not in page
        assert "2026-10-05" in page

    def test_a_pending_row_posts_to_the_mark_purchased_route(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_list_detail(respx_mock, shopping_list_payload())
            body = authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()

        assert (
            f'action="/purchases/lists/{LIST_ID}/items/{ITEM_ID}/mark-purchased/"'
            in main_content(body)
        )

    def test_the_add_item_form_posts_to_the_add_route(self, respx_mock, authed_client):
        with respx_mock:
            _stub_list_detail(respx_mock, shopping_list_payload())
            body = authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()

        assert f'action="/purchases/lists/{LIST_ID}/items/add/"' in main_content(body)

    def test_the_page_still_has_exactly_one_alpine_scope(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_list_detail(
                respx_mock,
                shopping_list_payload(
                    items=[
                        list_item_payload(),
                        list_item_payload(id=str(uuid4())),
                        list_item_payload(id=str(uuid4())),
                    ]
                ),
            )
            body = authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()

        # Three items, three tick buttons, and still one root: a per-item `x-data`
        # is the failure rule 5 warns about, and it scales with the item count.
        page = main_content(body)
        assert page.count('data-testid="mark-purchased"') == 3
        assert page.count("x-data=") == 1

    def test_an_empty_list_shows_its_empty_state(self, respx_mock, authed_client):
        with respx_mock:
            _stub_list_detail(respx_mock, shopping_list_payload(items=[]))
            body = authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()

        page = main_content(body)
        assert "This list has no items." in page
        assert 'data-testid="list-item"' not in page

    def test_a_404_redirects_with_a_flash_rather_than_rendering_an_empty_page(
        self, respx_mock, authed_client
    ):
        # The backend answers 404 for "missing" and "not yours" alike, so the page
        # must not become a way to probe for other people's list ids.
        with respx_mock:
            respx_mock.get(f"{LISTS_URL}{LIST_ID}/").mock(
                return_value=httpx.Response(404, json={"detail": "Not found."})
            )
            response = authed_client.get(f"/purchases/lists/{LIST_ID}/")

        assert response.status_code == 302
        assert "/purchases/" in response.headers["Location"]


# --------------------------------------------------------------------------- #
# Wishlist page
# --------------------------------------------------------------------------- #
class TestWishlistPage:
    def test_renders_a_card_per_item(self, respx_mock, authed_client):
        with respx_mock:
            _stub_wishlist(respx_mock)
            body = authed_client.get("/purchases/wishlist/").data.decode()

        page = main_content(body)
        assert page.count('data-testid="wishlist-card"') == 1
        assert "Espresso machine" in page

    def test_the_price_is_formatted_in_the_profile_currency(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_wishlist(respx_mock)
            body = authed_client.get("/purchases/wishlist/").data.decode()

        # `en` + `USD` is comma-grouped with a dot fraction. A bare "399.90" would
        # mean the ViewModel was bypassed and the template formatted the money.
        assert "$399.90" in main_content(body)

    def test_an_absent_price_renders_as_a_dash_and_not_as_zero(
        self, respx_mock, authed_client
    ):
        # "Not estimated" and "estimated at nothing" are different claims, and the
        # API's field is nullable so both are expressible.
        with respx_mock:
            _stub_wishlist(
                respx_mock,
                wishlist_payload(items=[wishlist_item_payload(estimated_price=None)]),
            )
            body = authed_client.get("/purchases/wishlist/").data.decode()

        page = main_content(body)
        assert "—" in page
        assert "$0.00" not in page

    @pytest.mark.parametrize(
        ("priority", "expected_style"),
        [
            # The colours the brief specifies, asserted so a rebrand cannot
            # quietly recolour one priority and leave the other two.
            ("must_have", "bg-red-100 text-red-700"),
            ("wanted", "bg-yellow-100 text-yellow-800"),
            ("nice_to_have", "bg-blue-100 text-blue-800"),
        ],
    )
    def test_each_priority_gets_its_own_badge_colour(
        self, respx_mock, authed_client, priority, expected_style
    ):
        with respx_mock:
            _stub_wishlist(
                respx_mock,
                wishlist_payload(items=[wishlist_item_payload(priority=priority)]),
            )
            body = authed_client.get("/purchases/wishlist/").data.decode()

        assert expected_style in main_content(body)

    def test_an_item_with_an_image_renders_it_and_one_without_falls_back(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_wishlist(
                respx_mock,
                wishlist_payload(
                    items=[
                        wishlist_item_payload(image_link="https://example.com/a.png"),
                        wishlist_item_payload(
                            id=str(uuid4()), name="Rug", image_link=None
                        ),
                    ]
                ),
            )
            body = authed_client.get("/purchases/wishlist/").data.decode()

        page = main_content(body)
        assert 'src="https://example.com/a.png"' in page
        assert page.count('data-testid="wishlist-card"') == 2
        assert page.count("<img") == 1

    def test_a_pending_card_posts_to_the_mark_purchased_route(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_wishlist(respx_mock)
            body = authed_client.get("/purchases/wishlist/").data.decode()

        assert (
            f'action="/purchases/wishlist/items/{WISHLIST_ITEM_ID}/mark-purchased/"'
            in main_content(body)
        )

    def test_a_bought_card_has_no_live_tick(self, respx_mock, authed_client):
        with respx_mock:
            _stub_wishlist(
                respx_mock,
                wishlist_payload(
                    status="purchased",
                    completed_at="2026-10-05T14:31:12.480000Z",
                    items=[wishlist_item_payload(status="purchased")],
                ),
            )
            body = authed_client.get("/purchases/wishlist/").data.decode()

        page = main_content(body)
        assert 'data-testid="mark-purchased"' not in page
        assert "Bought" in page

    def test_the_page_still_has_exactly_one_alpine_scope(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_wishlist(
                respx_mock,
                wishlist_payload(
                    items=[
                        wishlist_item_payload(),
                        wishlist_item_payload(id=str(uuid4())),
                        wishlist_item_payload(id=str(uuid4())),
                    ]
                ),
            )
            body = authed_client.get("/purchases/wishlist/").data.decode()

        # The card grid is where a per-card scope would appear, and it scales with
        # the number of items -- so this is the assertion that has to exist.
        assert main_content(body).count("x-data=") == 1

    def test_a_new_users_empty_wishlist_renders(self, respx_mock, authed_client):
        # `GET /wishlist/` creates the list on first access, so "no wishlist yet"
        # is never a state this page has to handle as an error.
        with respx_mock:
            _stub_wishlist(respx_mock, wishlist_payload(items=[]))
            body = authed_client.get("/purchases/wishlist/").data.decode()

        page = main_content(body)
        assert "Nothing on the wishlist yet." in page
        assert 'data-testid="wishlist-card"' not in page

    def test_the_page_works_without_a_profile(self, respx_mock, authed_client):
        # A currency is never worth a 500: a card should show its price in the
        # default currency rather than the page failing.
        with respx_mock:
            _stub_wishlist(respx_mock)
            respx_mock.get(f"{BASE}/profile/me/").mock(
                return_value=httpx.Response(500, json={"detail": "boom"})
            )
            body = authed_client.get("/purchases/wishlist/").data.decode()

        assert 'data-testid="wishlist-card"' in main_content(body)


# --------------------------------------------------------------------------- #
# Mutations: the form-post paths
# Mutations: the form-post paths
# --------------------------------------------------------------------------- #
class TestCreateList:
    """Creating a list, through the overview's form.

    Every test here does its page load *inside* the `respx_mock` context, because
    the CSRF token is read from a rendered page and that page load is itself a
    DRF read. Fetching the token outside the context would make an unmocked
    request rather than a token.
    """

    def test_posts_the_name_and_priority_and_redirects(self, respx_mock, authed_client):
        with respx_mock:
            route = respx_mock.post(LISTS_URL).mock(
                return_value=httpx.Response(201, json=shopping_list_payload())
            )
            _stub_lists(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/").data.decode())
            response = authed_client.post(
                "/purchases/lists/create/",
                data={
                    "csrf_token": token,
                    "name": "Weekly groceries",
                    "priority": "high",
                },
                follow_redirects=True,
            )

        assert response.status_code == 200
        assert b"Shopping list created" in response.data
        assert b'"priority":"high"' in route.calls[0].request.read()

    def test_a_blank_name_flashes_and_never_reaches_the_backend(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            route = respx_mock.post(LISTS_URL)
            _stub_lists(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/").data.decode())
            response = authed_client.post(
                "/purchases/lists/create/",
                data={"csrf_token": token, "name": "   ", "priority": "medium"},
                follow_redirects=True,
            )

        assert not route.called
        assert b"cannot be empty" in response.data

    def test_a_tampered_priority_is_a_message_not_a_500(
        self, respx_mock, authed_client
    ):
        # `<select>` is client-controlled. A bare `ValueError` from the enum would
        # not be caught by `except DomainException` and would be a 500.
        with respx_mock:
            route = respx_mock.post(LISTS_URL)
            _stub_lists(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/").data.decode())
            response = authed_client.post(
                "/purchases/lists/create/",
                data={"csrf_token": token, "name": "Shop", "priority": "urgent"},
                follow_redirects=True,
            )

        assert not route.called
        assert b"Priority must be one of" in response.data

    def test_a_missing_csrf_token_is_refused(self, respx_mock, authed_client):
        with respx_mock:
            route = respx_mock.post(LISTS_URL)
            _stub_lists(respx_mock)
            response = authed_client.post(
                "/purchases/lists/create/",
                data={"csrf_token": "", "name": "Shop", "priority": "medium"},
                follow_redirects=True,
            )

        assert not route.called
        assert b"Invalid CSRF token." in response.data


class TestAddListItem:
    def test_posts_the_item_and_redirects_back_to_the_list(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            route = respx_mock.post(f"{LISTS_URL}{LIST_ID}/add-item/").mock(
                return_value=httpx.Response(201, json=shopping_list_payload())
            )
            _stub_list_detail(respx_mock, shopping_list_payload())
            token = _csrf_from(
                authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()
            )
            response = authed_client.post(
                f"/purchases/lists/{LIST_ID}/items/add/",
                data={"csrf_token": token, "name": "Milk", "quantity": "2"},
            )

        assert response.status_code == 302
        assert f"/purchases/lists/{LIST_ID}/" in response.headers["Location"]
        assert b'"quantity":2' in route.calls[0].request.read()

    def test_a_quantity_of_zero_is_refused_before_the_request(
        self, respx_mock, authed_client
    ):
        # Zero looks like "not counting this one", and the API's `min_value=1`
        # would answer 400 with a message the user cannot trace.
        with respx_mock:
            route = respx_mock.post(f"{LISTS_URL}{LIST_ID}/add-item/")
            _stub_list_detail(respx_mock, shopping_list_payload())
            token = _csrf_from(
                authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()
            )
            response = authed_client.post(
                f"/purchases/lists/{LIST_ID}/items/add/",
                data={"csrf_token": token, "name": "Milk", "quantity": "0"},
                follow_redirects=True,
            )

        assert not route.called
        assert b"less than one" in response.data

    def test_a_non_numeric_quantity_is_a_message_not_a_500(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            route = respx_mock.post(f"{LISTS_URL}{LIST_ID}/add-item/")
            _stub_list_detail(respx_mock, shopping_list_payload())
            token = _csrf_from(
                authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()
            )
            response = authed_client.post(
                f"/purchases/lists/{LIST_ID}/items/add/",
                data={"csrf_token": token, "name": "Milk", "quantity": "two"},
                follow_redirects=True,
            )

        assert not route.called
        assert b"whole number" in response.data


class TestAddWishlistItem:
    def test_posts_the_item_including_the_price_as_a_decimal_string(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            route = respx_mock.post(f"{BASE}/purchases/wishlist/add-item/").mock(
                return_value=httpx.Response(201, json=wishlist_payload())
            )
            _stub_wishlist(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/wishlist/").data.decode())
            response = authed_client.post(
                "/purchases/wishlist/items/add/",
                data={
                    "csrf_token": token,
                    "name": "Espresso machine",
                    "estimated_price": "399.90",
                    "priority": "must_have",
                },
            )

        assert response.status_code == 302
        assert b'"estimated_price":"399.90"' in route.calls[0].request.read()

    def test_a_blank_price_is_omitted_rather_than_sent_as_null(
        self, respx_mock, authed_client
    ):
        # On a create, `null` and absent mean the same thing to DRF; omitting it
        # is what "no estimate" means and keeps the body honest.
        with respx_mock:
            route = respx_mock.post(f"{BASE}/purchases/wishlist/add-item/").mock(
                return_value=httpx.Response(201, json=wishlist_payload())
            )
            _stub_wishlist(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/wishlist/").data.decode())
            authed_client.post(
                "/purchases/wishlist/items/add/",
                data={
                    "csrf_token": token,
                    "name": "Lamp",
                    "estimated_price": "",
                    "priority": "wanted",
                },
            )

        body = route.calls[0].request.read()
        assert b"estimated_price" not in body
        assert b'"priority":"wanted"' in body

    def test_a_non_numeric_price_is_a_message_not_a_500(
        self, respx_mock, authed_client
    ):
        # `Decimal("abc")` raises `InvalidOperation`, which is *not* a
        # `DomainException`. Left uncaught it would be a 500 for a mistyped field.
        with respx_mock:
            route = respx_mock.post(f"{BASE}/purchases/wishlist/add-item/")
            _stub_wishlist(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/wishlist/").data.decode())
            response = authed_client.post(
                "/purchases/wishlist/items/add/",
                data={
                    "csrf_token": token,
                    "name": "Lamp",
                    "estimated_price": "about four hundred",
                    "priority": "wanted",
                },
                follow_redirects=True,
            )

        assert not route.called
        assert b"must be a number" in response.data

    def test_a_negative_price_is_refused_before_the_request(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            route = respx_mock.post(f"{BASE}/purchases/wishlist/add-item/")
            _stub_wishlist(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/wishlist/").data.decode())
            response = authed_client.post(
                "/purchases/wishlist/items/add/",
                data={
                    "csrf_token": token,
                    "name": "Lamp",
                    "estimated_price": "-1.00",
                    "priority": "wanted",
                },
                follow_redirects=True,
            )

        assert not route.called
        assert b"cannot be negative" in response.data

    def test_a_javascript_url_is_refused_before_the_request(
        self, respx_mock, authed_client
    ):
        # The value is rendered into an `href`, and a `javascript:` URL there is an
        # injection the browser executes on click.
        with respx_mock:
            route = respx_mock.post(f"{BASE}/purchases/wishlist/add-item/")
            _stub_wishlist(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/wishlist/").data.decode())
            response = authed_client.post(
                "/purchases/wishlist/items/add/",
                data={
                    "csrf_token": token,
                    "name": "Lamp",
                    "link": "javascript:alert(1)",
                    "priority": "wanted",
                },
                follow_redirects=True,
            )

        assert not route.called
        assert b"http or https" in response.data


# --------------------------------------------------------------------------- #
# Mutations: the fetch paths
# --------------------------------------------------------------------------- #
class TestMarkPurchasedViaFetch:
    """The JSON half of the dual contract.

    The same routes serve the form post and the `fetch`. A failure has to answer
    a 4xx rather than 200-with-a-flag, because the client decides whether to
    revert by reading the status.
    """

    def test_a_list_tick_answers_json_with_the_reloaded_state(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_list_detail(respx_mock, shopping_list_payload())
            token = _csrf_from(
                authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()
            )
            respx_mock.post(
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
            response = authed_client.post(
                f"/purchases/lists/{LIST_ID}/items/{ITEM_ID}/mark-purchased/",
                data={"csrf_token": token},
                headers={"X-Requested-With": "fetch", "X-CSRF-Token": token},
            )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        # The completion flag is the backend's own recomputation, echoed so the
        # client can report the tick that finished the list without a second read.
        assert payload["is_completed"] is True
        assert payload["purchased_count"] == 1
        assert payload["pending_count"] == 0

    def test_a_list_tick_that_completes_the_list_says_so(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_list_detail(respx_mock, shopping_list_payload())
            token = _csrf_from(
                authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()
            )
            respx_mock.post(
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
            response = authed_client.post(
                f"/purchases/lists/{LIST_ID}/items/{ITEM_ID}/mark-purchased/",
                data={"csrf_token": token},
                headers={"X-Requested-With": "fetch", "X-CSRF-Token": token},
            )

        assert b"complete" in response.data.lower()

    def test_a_missing_item_answers_404_not_200_with_a_flag(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_list_detail(respx_mock, shopping_list_payload())
            token = _csrf_from(
                authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()
            )
            respx_mock.post(
                f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/mark-purchased/"
            ).mock(return_value=httpx.Response(404, json={"detail": "Not found."}))

            response = authed_client.post(
                f"/purchases/lists/{LIST_ID}/items/{ITEM_ID}/mark-purchased/",
                data={"csrf_token": token},
                headers={"X-Requested-With": "fetch", "X-CSRF-Token": token},
            )

        assert response.status_code == 404
        assert response.get_json()["ok"] is False

    def test_an_invalid_csrf_token_answers_400_on_the_fetch_path(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            route = respx_mock.post(
                f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/mark-purchased/"
            )
            response = authed_client.post(
                f"/purchases/lists/{LIST_ID}/items/{ITEM_ID}/mark-purchased/",
                data={"csrf_token": "wrong"},
                headers={"X-Requested-With": "fetch", "X-CSRF-Token": "wrong"},
            )

        assert response.status_code == 400
        assert not route.called

    def test_a_wishlist_tick_answers_json_with_the_reloaded_state(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_wishlist(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/wishlist/").data.decode())
            respx_mock.post(
                f"{BASE}/purchases/wishlist/items/{WISHLIST_ITEM_ID}/mark-purchased/"
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
            response = authed_client.post(
                f"/purchases/wishlist/items/{WISHLIST_ITEM_ID}/mark-purchased/",
                data={"csrf_token": token},
                headers={"X-Requested-With": "fetch", "X-CSRF-Token": token},
            )

        assert response.status_code == 200
        assert response.get_json()["is_completed"] is True

    def test_a_missing_wishlist_item_answers_404(self, respx_mock, authed_client):
        with respx_mock:
            _stub_wishlist(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/wishlist/").data.decode())
            respx_mock.post(
                f"{BASE}/purchases/wishlist/items/{WISHLIST_ITEM_ID}/mark-purchased/"
            ).mock(return_value=httpx.Response(404, json={"detail": "Not found."}))

            response = authed_client.post(
                f"/purchases/wishlist/items/{WISHLIST_ITEM_ID}/mark-purchased/",
                data={"csrf_token": token},
                headers={"X-Requested-With": "fetch", "X-CSRF-Token": token},
            )

        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Form-post ticks (the no-JavaScript path)
# --------------------------------------------------------------------------- #
class TestMarkPurchasedViaFormPost:
    """The same routes without the fetch header.

    A page that only worked with JavaScript would be a page that does not work
    for anyone whose script is blocked, slow, or erroring, so both callers are
    tested rather than only the one that looks nicer.
    """

    def test_a_list_tick_redirects_back_with_a_success_flash(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_list_detail(respx_mock, shopping_list_payload())
            token = _csrf_from(
                authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()
            )
            respx_mock.post(
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
            response = authed_client.post(
                f"/purchases/lists/{LIST_ID}/items/{ITEM_ID}/mark-purchased/",
                data={"csrf_token": token},
            )

        assert response.status_code == 302
        assert f"/purchases/lists/{LIST_ID}/" in response.headers["Location"]

    def test_a_stale_row_flashes_that_it_is_gone_rather_than_success(
        self, respx_mock, authed_client
    ):
        # The row the user ticked was already gone, which means their view was
        # stale. "Marked as purchased" would hide that.
        with respx_mock:
            _stub_list_detail(respx_mock, shopping_list_payload())
            token = _csrf_from(
                authed_client.get(f"/purchases/lists/{LIST_ID}/").data.decode()
            )
            respx_mock.post(
                f"{LISTS_URL}{LIST_ID}/items/{ITEM_ID}/mark-purchased/"
            ).mock(return_value=httpx.Response(404, json={"detail": "Not found."}))

            response = authed_client.post(
                f"/purchases/lists/{LIST_ID}/items/{ITEM_ID}/mark-purchased/",
                data={"csrf_token": token},
                follow_redirects=True,
            )

        assert b"no longer exists" in response.data

    def test_a_wishlist_tick_redirects_back_with_a_success_flash(
        self, respx_mock, authed_client
    ):
        with respx_mock:
            _stub_wishlist(respx_mock)
            token = _csrf_from(authed_client.get("/purchases/wishlist/").data.decode())
            respx_mock.post(
                f"{BASE}/purchases/wishlist/items/{WISHLIST_ITEM_ID}/mark-purchased/"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json=wishlist_payload(
                        items=[wishlist_item_payload(status="purchased")],
                    ),
                )
            )
            response = authed_client.post(
                f"/purchases/wishlist/items/{WISHLIST_ITEM_ID}/mark-purchased/",
                data={"csrf_token": token},
            )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/purchases/wishlist/")


# --------------------------------------------------------------------------- #
# Escaping
# --------------------------------------------------------------------------- #
class TestEscaping:
    @pytest.mark.parametrize(
        ("path", "page"),
        [
            ("/purchases/", "lists"),
            ("/purchases/wishlist/", "wishlist"),
        ],
    )
    def test_user_text_never_reaches_a_javascript_expression(
        self, respx_mock, authed_client, path, page
    ):
        """The end of the chain this BFF cares about.

        User text is rendered as HTML, escaped, and never interpolated into an
        `x-data` body or a handler argument -- the tick reads its URL from a
        server-rendered attribute rather than building it in JavaScript.
        """
        hostile = "Robert'); DROP TABLE lists;--"
        with respx_mock:
            if page == "lists":
                _stub_lists(respx_mock, [shopping_list_payload(name=hostile)])
            else:
                _stub_wishlist(
                    respx_mock,
                    wishlist_payload(items=[wishlist_item_payload(name=hostile)]),
                )
            body = authed_client.get(path).data.decode()

        assert "Robert&#39;); DROP TABLE lists;--" in body
        for scope in re.findall(r'x-data="(.*?)"', body, re.DOTALL):
            assert "{{" not in scope, "user data was interpolated into an x-data body"


# --------------------------------------------------------------------------- #
# The backend being unreachable
# --------------------------------------------------------------------------- #
class TestBackendUnreachable:
    def test_an_unreachable_backend_renders_the_branded_503(
        self, respx_mock, authed_client
    ):
        # The list read is the page, so its failure propagates to the 503 handler
        # rather than being swallowed into a flash. A 500 would mean a bug here;
        # a 503 says the backend is not answering, which is a different thing.
        with respx_mock:
            respx_mock.get(LISTS_URL).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            respx_mock.get(f"{BASE}/purchases/shopping-lists/inactive/").mock(
                return_value=httpx.Response(200, json=[])
            )
            response = authed_client.get("/purchases/")

        assert response.status_code == 503
