"""Purchases Flask blueprint: shopping lists and the wishlist.

Views here are deliberately thin: read a value from the form, hand it to a use
case, render a ViewModel. No business rule lives in this module -- every one of
them is enforced by the backend or by the domain value objects the use case
already called.

**Every interactive element lives inside the single top-level ``x-data`` scope of
its template** (AGENTS.md, "Alpine.js Scope Strictness"). A button placed outside
that scope renders and does nothing. Each page therefore opens exactly one root,
and `tests/integration/purchases/test_purchases_routes.py` slices each rendered
page to `<main>` and counts the roots, because a second one is the failure this
rule is written against and it scales with the number of cards on the page.

**Two kinds of request reach the same routes.** A form post navigates and the
server redirects back with a flash, so the page works with JavaScript off. The
mark-purchased buttons additionally post with `fetch` and get JSON, so a tick
strikes the row through without a full reload. That dual contract is deliberate
and is what `_wants_json` exists for -- see the arrangement in
`src/tasks/interfaces/web/routes.py`, which does the same for the board's moves.

**A failure answers 4xx, never 200-with-a-flag.** The `fetch` path decides
whether to revert its optimistic strike-through by whether the response was ok,
and a 200 carrying `{"ok": false}` would make that decision depend on a field
both sides have to agree the meaning of.
"""

from __future__ import annotations

import asyncio

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from src.purchases.domain.exceptions import (
    ShoppingListItemNotFoundError,
    ShoppingListNotFoundError,
    WishlistItemNotFoundError,
)
from src.purchases.domain.value_objects import (
    ItemPriority,
    ListPriority,
    ShoppingListId,
    ShoppingListItemId,
    WishlistItemId,
)
from src.purchases.infrastructure.repositories import estimated_price_from_form
from src.purchases.interfaces.viewmodels import (
    ITEM_PRIORITY_LABELS,
    LIST_PRIORITY_LABELS,
    ShoppingListDetailViewModel,
    ShoppingListItemViewModel,
    ShoppingListsViewModel,
    WishlistItemViewModel,
    WishlistViewModel,
)
from src.shared.auth.csrf import CSRFTokenManager
from src.shared.auth.jwt_cookie_manager import JWTCookieManager
from src.shared.domain.exceptions import DomainException
from src.shared.utils.currency import CurrencyPreference

purchases_bp = Blueprint("purchases", __name__, url_prefix="/purchases")

#: Header the browser's `fetch` cannot be made to set cross-origin, which makes
#: it a usable signal that *this* request came from our own JavaScript rather
#: than from a navigation. Paired with `same-origin`, so a cross-site form cannot
#: forge it.
FETCH_HEADER = "X-Requested-With"
FETCH_HEADER_VALUE = "fetch"


def _access_token() -> str | None:
    """Read the JWT from its HttpOnly cookie."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    return cookie_manager.get_access_token(request)


def _require_csrf() -> bool:
    """Validate the token from the form body or the request header.

    The header is accepted because the mark-purchased buttons post with `fetch`
    and read their token from a data attribute rather than a form. Accepting
    either alone would leave one of the two callers unprotected.
    """
    csrf_manager: CSRFTokenManager = current_app.csrf_manager
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    return csrf_manager.validate_token(token)


def _wants_json() -> bool:
    """Whether this request came from `fetch` and should get JSON back.

    Not a query parameter: a caller who can set the query can also set the
    header, so making it one would not add a check, only a way to get it wrong.
    """
    return request.headers.get(FETCH_HEADER, "").strip().lower() == FETCH_HEADER_VALUE


def _parse_list_priority(raw: str | None) -> ListPriority:
    """Parse the list priority from the form.

    Raising a `DomainException` rather than letting `ListPriority(text)` raise a
    bare `ValueError` is the whole point: the route's `except DomainException`
    catches the first and turns it into a flash, and does not catch the second --
    so a tampered form would otherwise be a 500 instead of a message.
    """
    text = (raw or "").strip() or ListPriority.MEDIUM.value
    try:
        return ListPriority(text)
    except ValueError as exc:
        allowed = ", ".join(label for label in LIST_PRIORITY_LABELS.values())
        raise DomainException(f"Priority must be one of: {allowed}.") from exc


def _parse_item_priority(raw: str | None) -> ItemPriority:
    """Parse the wishlist item priority from the form."""
    text = (raw or "").strip() or ItemPriority.WANTED.value
    try:
        return ItemPriority(text)
    except ValueError as exc:
        allowed = ", ".join(label for label in ITEM_PRIORITY_LABELS.values())
        raise DomainException(f"Priority must be one of: {allowed}.") from exc


def _parse_quantity(raw: str | None) -> int:
    """Parse the quantity form field, mapping a blank to 1.

    A blank input posts `""`, which means "one of these" rather than zero, so it
    is defaulted rather than rejected. A non-numeric or sub-one value raises:
    `Quantity` already refuses it with a message naming the field, and that is a
    `DomainException`, so the route flashes it verbatim.
    """
    text = (raw or "").strip()
    if not text:
        return 1
    try:
        return int(text)
    except ValueError as exc:
        raise DomainException("Quantity must be a whole number.") from exc


async def _currency_preference(access_token: str) -> CurrencyPreference:
    """Read the user's currency and locale from the profile context.

    `EstimatedPrice` carries no currency -- it is a bare amount -- and the
    currency is a profile preference this context may not import
    (AGENTS.md, constraint 1). `CurrencyPreference.from_profile` reads it
    dynamically, so purchases has no compile-time dependency on profile at all.

    Any failure degrades to the default currency rather than taking the page
    down: a wishlist card should still show its estimated price to a user whose
    profile could not be read, in a fallback currency, rather than show nothing.
    """
    try:
        profile = await current_app.get_profile_use_case.execute(access_token)
    except Exception:  # noqa: BLE001 - a currency is never worth a 500
        current_app.logger.warning(
            "Falling back to the default currency; profile unavailable for purchases."
        )
        return CurrencyPreference()
    return CurrencyPreference.from_profile(profile)


def _list_detail_url(shopping_list) -> str:
    """The detail page's URL for one list.

    Takes the aggregate rather than a bare id because that is what the ViewModel
    iterates, and passing `shopping_list.id` at the call site would mean the
    ViewModel's callback contract and this function's could disagree about the
    shape without either complaining.
    """
    return url_for("purchases.list_detail", list_id=str(shopping_list.id))


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@purchases_bp.route("/")
async def lists():
    """The shopping-list overview: active lists, plus the archived ones.

    Three reads, two of them optional. The archived list is a secondary section
    behind a disclosure and its failure costs the user that panel; the tag
    catalogue feeds a suggestion input and its failure costs an autocomplete. The
    active lists are the page, so their failure propagates to the 503 handler.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    active, archived = await asyncio.gather(
        current_app.get_active_lists_use_case.execute(access_token),
        current_app.get_archived_lists_use_case.execute(access_token),
        return_exceptions=True,
    )
    if isinstance(active, BaseException):
        raise active
    archived_list: list = [] if isinstance(archived, BaseException) else list(archived)
    if isinstance(archived, BaseException):
        current_app.logger.warning(
            "Archived shopping lists unavailable for the overview: %s",
            type(archived).__name__,
        )

    return render_template(
        "purchases/lists.html",
        lists=ShoppingListsViewModel.build(
            active, archived_list, detail_url_for=_list_detail_url
        ),
        archived_unavailable=isinstance(archived, BaseException),
        today=current_app.clock.today().isoformat(),
    )


@purchases_bp.route("/lists/<uuid:list_id>/")
async def list_detail(list_id):
    """One shopping list as a checklist.

    A list that does not exist, or is not the caller's, answers the same redirect
    with the same words: the backend returns 404 for both and the page must not
    become a way to probe for other people's list ids.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    shopping_list_id = ShoppingListId(str(list_id))
    try:
        shopping_list = await current_app.get_shopping_list_use_case.execute(
            access_token, shopping_list_id
        )
    except ShoppingListNotFoundError:
        flash("That shopping list is not available.", "error")
        return redirect(url_for("purchases.lists"))

    def item_view_model(item) -> ShoppingListItemViewModel:
        return ShoppingListItemViewModel.from_domain(
            item,
            mark_purchased_url=url_for(
                "purchases.mark_list_item_purchased",
                list_id=str(shopping_list_id),
                item_id=str(item.id),
            ),
        )

    return render_template(
        "purchases/list_detail.html",
        detail=ShoppingListDetailViewModel.build(
            shopping_list,
            item_view_model_for=item_view_model,
            back_url=url_for("purchases.lists"),
        ),
        list_id=str(shopping_list_id),
        today=current_app.clock.today().isoformat(),
    )


@purchases_bp.route("/wishlist/")
async def wishlist():
    """The wishlist, as a card grid.

    The single `GET` is also the create: the backend's `GetOrCreateWishlist`
    answers 200 with the list whether it made it or found it, so a user who has
    never had one still gets a page rather than an empty-state error.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    current, currency = await asyncio.gather(
        current_app.get_wishlist_use_case.execute(access_token),
        _currency_preference(access_token),
    )

    def item_view_model(item) -> WishlistItemViewModel:
        return WishlistItemViewModel.from_domain(
            item,
            currency=currency,
            mark_purchased_url=url_for(
                "purchases.mark_wishlist_item_purchased", item_id=str(item.id)
            ),
        )

    return render_template(
        "purchases/wishlist.html",
        wishlist=WishlistViewModel.build(current, item_view_model_for=item_view_model),
        today=current_app.clock.today().isoformat(),
    )


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #
@purchases_bp.route("/lists/create/", methods=["POST"])
async def create_list():
    """Create a shopping list from the overview's form.

    A plain form post that always redirects, so the page works without
    JavaScript and the browser lands back on the overview with a flash.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("purchases.lists"))

    try:
        shopping_list = await current_app.create_shopping_list_use_case.execute(
            access_token,
            name=request.form.get("name", ""),
            priority=_parse_list_priority(request.form.get("priority")),
        )
    except DomainException as exc:
        flash(str(exc), "error")
    else:
        flash(f"Shopping list created: {shopping_list.name}.", "success")
    return redirect(url_for("purchases.lists"))


@purchases_bp.route("/lists/<uuid:list_id>/items/add/", methods=["POST"])
async def add_list_item(list_id):
    """Add one item to a shopping list.

    Reached by the detail page's inline form, which is a real `<form>` with an
    action rather than a fetch: the common case is typing a name and pressing
    enter, and that has to work.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(_back_to_list(list_id))

    shopping_list_id = ShoppingListId(str(list_id))
    try:
        shopping_list = await current_app.add_shopping_list_item_use_case.execute(
            access_token,
            shopping_list_id,
            name=request.form.get("name", ""),
            quantity=_parse_quantity(request.form.get("quantity")),
            details=request.form.get("details"),
        )
    except ShoppingListNotFoundError:
        flash("That shopping list is not available.", "error")
        return redirect(url_for("purchases.lists"))
    except DomainException as exc:
        flash(str(exc), "error")
    else:
        flash(f"Added to {shopping_list.name}.", "success")
    return redirect(_back_to_list(shopping_list_id))


@purchases_bp.route(
    "/lists/<uuid:list_id>/items/<uuid:item_id>/mark-purchased/", methods=["POST"]
)
async def mark_list_item_purchased(list_id, item_id):
    """Tick one item off a shopping list.

    Two callers, one code path, the same arrangement as the tasks board: a form
    post gets a flash and a redirect, and the checkbox's `fetch` gets JSON so it
    can strike the row through without a page load.

    The response is the **whole reloaded list**, echoed as JSON. That is the
    backend's response, and forwarding it is what lets the client reconcile its
    optimistic strike-through with what the backend actually decided -- which
    matters on the tick that completes the list, because only the response knows
    that happened.
    """
    access_token = _access_token()
    if not access_token:
        if _wants_json():
            return jsonify({"ok": False, "message": "Not signed in."}), 401
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        if _wants_json():
            return jsonify({"ok": False, "message": "Invalid CSRF token."}), 400
        flash("Invalid CSRF token.", "error")
        return redirect(_back_to_list(list_id))

    shopping_list_id = ShoppingListId(str(list_id))
    failure_status = 400
    try:
        shopping_list = (
            await current_app.mark_shopping_list_item_purchased_use_case.execute(
                access_token,
                shopping_list_id,
                ShoppingListItemId(str(item_id)),
            )
        )
    except ShoppingListItemNotFoundError:
        # Worded differently from the success case on purpose: the row the user
        # ticked was already gone, which means their view was stale, and saying
        # "marked" would hide that.
        message, failure_status = "That item no longer exists on this list.", 404
    except DomainException as exc:
        message = str(exc)
    else:
        message = (
            "Shopping list complete. Everything on it has been bought."
            if shopping_list.is_completed
            else "Item marked as purchased."
        )
        if _wants_json():
            return (
                jsonify(
                    {"ok": True, "message": message, **_aggregate_state(shopping_list)}
                ),
                200,
            )
        flash(message, "success")
        return redirect(_back_to_list(shopping_list_id))

    if _wants_json():
        return jsonify({"ok": False, "message": message}), failure_status
    flash(message, "error")
    return redirect(_back_to_list(shopping_list_id))


@purchases_bp.route("/wishlist/items/add/", methods=["POST"])
async def add_wishlist_item():
    """Add a wanted item to the wishlist.

    A form post, for the same reason as the shopping-list one: the common case
    is a name and a price, and that must work with JavaScript off.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("purchases.wishlist"))

    try:
        wishlist = await current_app.add_wishlist_item_use_case.execute(
            access_token,
            name=request.form.get("name", ""),
            link=request.form.get("link"),
            image_link=request.form.get("image_link"),
            description=request.form.get("description"),
            estimated_price=estimated_price_from_form(
                request.form.get("estimated_price")
            ),
            priority=_parse_item_priority(request.form.get("priority")),
        )
    except DomainException as exc:
        flash(str(exc), "error")
    else:
        flash(
            f"Added to {wishlist.name}: {request.form.get('name', '').strip()}.",
            "success",
        )
    return redirect(url_for("purchases.wishlist"))


@purchases_bp.route("/wishlist/items/<uuid:item_id>/mark-purchased/", methods=["POST"])
async def mark_wishlist_item_purchased(item_id):
    """Tick one wishlist item off as bought.

    The same dual form-post/`fetch` contract as the shopping-list counterpart,
    for the same reason: a card grid's tick should not cost a page load, and the
    page must still work without JavaScript.
    """
    access_token = _access_token()
    if not access_token:
        if _wants_json():
            return jsonify({"ok": False, "message": "Not signed in."}), 401
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        if _wants_json():
            return jsonify({"ok": False, "message": "Invalid CSRF token."}), 400
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("purchases.wishlist"))

    wishlist_id = WishlistItemId(str(item_id))
    failure_status = 400
    try:
        wishlist = await current_app.mark_wishlist_item_purchased_use_case.execute(
            access_token, wishlist_id
        )
    except WishlistItemNotFoundError:
        message, failure_status = "That wishlist item no longer exists.", 404
    except DomainException as exc:
        message = str(exc)
    else:
        message = (
            "Wishlist complete. Everything on it has been bought."
            if wishlist.is_completed
            else "Marked as purchased."
        )
        if _wants_json():
            return (
                jsonify({"ok": True, "message": message, **_aggregate_state(wishlist)}),
                200,
            )
        flash(message, "success")
        return redirect(url_for("purchases.wishlist"))

    if _wants_json():
        return jsonify({"ok": False, "message": message}), failure_status
    flash(message, "error")
    return redirect(url_for("purchases.wishlist"))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _aggregate_state(aggregate) -> dict:
    """The facts a `fetch` caller needs to reconcile its optimistic update.

    Shared by both aggregates because both are "a list of items, some bought",
    and the JSON contract is the same four fields either way. Only what the
    client cannot already know is sent: these counts and the completion flag come
    from the backend's own recomputation, so the tick that completes a list can
    be reported without the page re-deriving it locally -- and without a second
    read to find out.
    """
    items = aggregate.items
    purchased = sum(1 for item in items if item.is_purchased)
    return {
        "item_count": len(items),
        "purchased_count": purchased,
        "pending_count": len(items) - purchased,
        "is_completed": aggregate.is_completed,
    }


def _back_to_list(list_id) -> str:
    """Where an item mutation returns the user to: that list's page.

    Never the raw `Referer`. An attacker-controlled header echoed into a
    `Location` is an open redirect that turns a successful POST into a convincing
    phishing link, and here the destination is fully determined by the id already
    in the URL -- so there is nothing a referer could add.
    """
    return url_for("purchases.list_detail", list_id=str(list_id))


__all__ = ["purchases_bp"]
