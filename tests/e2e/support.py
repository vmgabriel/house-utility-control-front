"""Reusable helpers for the end-to-end suite.

The ``mock_drf_*`` functions configure the fake DRF backend (see
``drf_stub`` for why the backend is faked over HTTP rather than intercepted in
the browser). They are plain functions rather than fixtures so a test can opt
into exactly the endpoints it exercises and ignore the rest.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from tests.e2e.drf_stub import (
    DEFAULT_ACCESS_TOKEN,
    DEFAULT_OVERVIEW,
    DEFAULT_REFRESH_TOKEN,
    DEFAULT_USER,
    EMPTY_OVERVIEW,
    StubDRFServer,
    StubResponse,
    default_transaction,
    transaction_payload,
)

__all__ = [
    "date_range",
    "expect_path",
    "form_csrf_token",
    "net_balance",
    "mock_drf_dashboard_failure",
    "mock_drf_dashboard_overview",
    "mock_drf_login",
    "mock_drf_login_unavailable",
    "mock_drf_logout",
    "mock_drf_profile_failure",
    "mock_drf_refresh",
    "mock_drf_transaction_create",
    "mock_drf_transaction_delete",
    "mock_drf_transactions_list",
    "mock_drf_transactions_list_status",
    "mock_drf_user_me",
    "set_auth_cookies",
    "submit_form",
    "summary_card",
    "summary_value",
    "transaction_row",
]


# ---------------------------------------------------------------------------
# Navigation and lookup
# ---------------------------------------------------------------------------


def expect_path(page: Page, path: str) -> None:
    """Assert the browser ends up at ``path``, waiting for the redirect first.

    A glob like ``**/auth/login`` cannot be used here: Playwright resolves a
    string URL against the fixture's ``base_url`` first, which turns the glob
    into an unmatchable literal. A regex is left alone, so it is compared
    against the real URL.
    """
    pattern = re.compile(re.escape(path) + r"(?:[?#].*)?$")
    try:
        page.wait_for_url(pattern)
    except PlaywrightTimeout as exc:
        raise AssertionError(
            f"Expected to end at {path!r} but the browser is at {page.url!r}"
        ) from exc


def summary_card(page: Page, title: str):
    """The dashboard summary card whose heading is ``title``."""
    return page.locator("section").filter(
        has=page.get_by_role("heading", name=title, exact=True)
    )


def account_menu_button(page: Page):
    """The avatar/initials toggle in the nav.

    Located by the initials badge rather than by a name, because the button's
    accessible name is the user's display name and changes with the fixture
    user. The badge is the one element in the toggle that does not.
    """
    return page.get_by_test_id("avatar-initials").locator("xpath=ancestor::button")


def open_account_menu(page: Page) -> None:
    """Open the nav account dropdown and wait for it to be usable.

    Anything inside the dropdown is ``x-show``-hidden until Alpine boots, so a
    test that clicks a menu item directly would either fail on visibility or --
    worse -- pass against a panel Alpine had not yet bound.
    """
    account_menu_button(page).click()
    expect(page.get_by_role("button", name="Logout")).to_be_visible()


def net_balance(card):
    """A card's headline figure: the paragraph right after its heading."""
    return card.locator("h2 + p")


def date_range(card):
    """A card's date range, the paragraph after its headline figure."""
    return card.locator("h2 + p + p")


def summary_value(card, label: str):
    """The figure rendered beside ``label`` ("Income", "Expense", ...).

    Reading the label/value pair is what the user does, and it stays correct
    even when two figures on the same card happen to be equal.
    """
    return (
        card.locator("dl > div")
        .filter(has=card.page.get_by_text(label, exact=True))
        .locator("dd")
    )


def transaction_row(page: Page, description: str):
    """The transaction list item whose description is ``description``.

    Scoping to the row keeps assertions strict: a description also appears in
    the row's screen-reader-only delete-button label, so an unscoped
    ``get_by_text`` would match two elements.
    """
    return page.locator("li").filter(has=page.get_by_text(description, exact=True))


# ---------------------------------------------------------------------------
# Form submission
# ---------------------------------------------------------------------------


def form_csrf_token(page: Page, action_suffix: str = "/create") -> str:
    """Read the CSRF token the server rendered into a form's hidden field."""
    value = page.locator(
        f"form[action$='{action_suffix}'] input[name='csrf_token']"
    ).first.input_value()
    assert value, f"no CSRF token rendered in the form posting to *{action_suffix}"
    return value


def submit_form(page: Page, path: str, fields: dict[str, str]) -> str:
    """POST a form directly and return the HTML the redirect lands on.

    The create form's inputs carry ``required``/``min`` attributes and its
    submit button is disabled by Alpine until the form is valid, so the browser
    cannot express a request the server should still reject. Posting through
    the shared request context keeps the session cookies and exercises exactly
    that server-side contract, instead of asserting the client-side guard twice.
    """
    token = form_csrf_token(page)
    response = page.request.post(
        path, form={**fields, "csrf_token": token}, timeout=10_000
    )
    return response.text()


# ---------------------------------------------------------------------------
# Auth cookies
# ---------------------------------------------------------------------------


def set_auth_cookies(
    page: Page,
    access_token: str = DEFAULT_ACCESS_TOKEN,
    refresh_token: str = DEFAULT_REFRESH_TOKEN,
    base_url: str | None = None,
) -> None:
    """Seed the HttpOnly auth cookies the BFF expects, skipping the login form.

    Cookies are scoped with ``url`` rather than ``domain`` so they always match
    whichever host and port the live server actually bound to.
    """
    target = base_url or "http://127.0.0.1:5000"
    page.context.add_cookies(
        [
            {
                "name": "bt_access_token",
                "value": access_token,
                "url": target,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            },
            {
                "name": "bt_refresh_token",
                "value": refresh_token,
                "url": target,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            },
        ]
    )


# ---------------------------------------------------------------------------
# DRF endpoint stubs
# ---------------------------------------------------------------------------


def mock_drf_login(
    stub: StubDRFServer,
    success: bool = True,
    access: str = DEFAULT_ACCESS_TOKEN,
    refresh: str = DEFAULT_REFRESH_TOKEN,
) -> None:
    """Stub ``POST /users/auth/login/``."""
    if success:
        stub.on(
            "POST",
            "/users/auth/login/",
            StubResponse(200, {"access": access, "refresh": refresh}),
        )
    else:
        stub.on(
            "POST",
            "/users/auth/login/",
            StubResponse(401, {"detail": "No active account found."}),
        )


def mock_drf_fail_user_list(stub: StubDRFServer) -> None:
    """Make ``GET /users/`` fail with a 500.

    Models a reachable-but-unhappy backend, which is the case the admin
    dashboard's dashed placeholders exist for. Stubs registered later win, so
    this must be called *after* seeding.
    """
    stub.on(
        "GET",
        "/users/",
        StubResponse(500, {"detail": "User service unavailable."}),
    )


def mock_drf_login_unavailable(stub: StubDRFServer) -> None:
    """Stub a backend outage on login (anything that is not 400/401)."""
    stub.on(
        "POST",
        "/users/auth/login/",
        StubResponse(503, {"detail": "Service temporarily unavailable."}),
    )


def mock_drf_refresh(
    stub: StubDRFServer,
    access: str = "rotated-access-token",
    refresh: str = "rotated-refresh-token",
) -> None:
    """Stub ``POST /users/auth/refresh/``."""
    stub.on(
        "POST",
        "/users/auth/refresh/",
        StubResponse(200, {"access": access, "refresh": refresh}),
    )


def mock_drf_logout(stub: StubDRFServer, success: bool = True) -> None:
    """Stub ``POST /users/auth/logout/``."""
    if success:
        stub.on("POST", "/users/auth/logout/", StubResponse(204))
    else:
        stub.on(
            "POST",
            "/users/auth/logout/",
            StubResponse(500, {"detail": "Logout failed."}),
        )


def mock_drf_user_me(
    stub: StubDRFServer,
    user_id: str = "user-123",
    email: str = "test@example.com",
    full_name: str = "Test User",
    plan: str = "free",
) -> None:
    """Stub ``GET /users/me/`` with the backend's ``full_name`` field name."""
    user = dict(DEFAULT_USER)
    user.update({"id": user_id, "email": email, "full_name": full_name, "plan": plan})
    stub.on("GET", "/users/me/", StubResponse(200, user))


def mock_drf_transactions_list(
    stub: StubDRFServer, transactions: list[dict] | None = None
) -> None:
    """Stub ``GET /transactions/``.

    Accepts the compact shape the tests use (only the fields they assert on);
    the rest of the DRF payload is filled in with realistic values.
    """
    stub.seed_transactions(*(transaction_payload(tx) for tx in transactions or []))


def mock_drf_transactions_list_status(stub: StubDRFServer, status: int) -> None:
    """Make the transactions list fail with ``status``."""
    stub.on(
        "GET",
        "/transactions/",
        StubResponse(status, {"detail": "Something went wrong."}),
    )


def mock_drf_transaction_create(
    stub: StubDRFServer, success: bool = True, transaction_id: str = "tx-new-123"
) -> None:
    """Stub ``POST /transactions/``.

    On success the created row is appended to the store, so the list the page
    re-fetches after the redirect already contains it -- exactly as a real
    backend would behave.
    """

    def handler(request) -> StubResponse:
        if not success:
            return StubResponse(400, {"amount": ["Enter a valid amount."]})
        payload = request.json if isinstance(request.json, dict) else {}
        created = default_transaction(
            transaction_id=transaction_id,
            transaction_type=payload.get("transaction_type", "expense"),
            amount=payload.get("amount", "0.00"),
            description=payload.get("description", ""),
            category=payload.get("category", "General"),
            date=payload.get("date", "2026-09-26"),
        )
        stub.backend.transactions.append(created)
        return StubResponse(201, created)

    stub.on_call("POST", "/transactions/", handler)


def mock_drf_transaction_delete(
    stub: StubDRFServer, transaction_id: str, success: bool = True
) -> None:
    """Stub ``DELETE /transactions/{id}/``."""

    def handler(_request) -> StubResponse:
        if not success:
            return StubResponse(404, {"detail": "Not found."})
        store = stub.backend.transactions
        for index, row in enumerate(store):
            if row.get("id") == transaction_id:
                del store[index]
                return StubResponse(204)
        return StubResponse(404, {"detail": "Not found."})

    stub.on_call("DELETE", f"/transactions/{transaction_id}/", handler)


def mock_drf_dashboard_overview(
    stub: StubDRFServer, has_data: bool = True, overview: dict | None = None
) -> None:
    """Stub ``GET /dashboard/overview/``.

    ``has_data=False`` sends the all-null envelope the backend produces for a
    brand-new account; ``overview`` replaces the payloads wholesale.
    """
    if overview is not None:
        stub.backend.overview = overview
    else:
        stub.backend.overview = (
            dict(DEFAULT_OVERVIEW) if has_data else dict(EMPTY_OVERVIEW)
        )
    stub.on_call(
        "GET",
        "/dashboard/overview/",
        lambda _r: StubResponse(200, dict(stub.backend.overview)),
    )


def mock_drf_profile_failure(stub: StubDRFServer, status: int = 500) -> None:
    """Make the profile endpoint fail."""
    stub.on(
        "GET",
        "/profile/me/",
        StubResponse(status, {"detail": "Profile unavailable."}),
    )


def mock_drf_dashboard_failure(stub: StubDRFServer, status: int = 500) -> None:
    """Make the dashboard overview endpoint fail."""
    stub.on(
        "GET",
        "/dashboard/overview/",
        StubResponse(status, {"detail": "Dashboard unavailable."}),
    )
