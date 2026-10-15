"""A fake DRF backend, served in-process, for end-to-end tests.

Why a real HTTP server instead of ``page.route()``
--------------------------------------------------
The BFF is server-side rendered: every DRF call is issued by Flask through
``httpx``, never by the browser. ``page.route()`` can only intercept requests
that originate inside the page, so mocking the backend with Playwright routes
would silently mock *nothing* while the suite appeared to pass. The only honest
seam is the network boundary between Flask and DRF, which is what this module
fakes: a real HTTP server on a real port, reached through
``DRF_API_BASE_URL``.

Because the stub runs in a thread inside the pytest process, a test reads and
writes its behaviour directly (``stub.requests``, ``stub.transactions``) with no
control channel, no serialisation, and no hidden coupling to the client.

The stub enforces ``Authorization: Bearer`` on protected endpoints and keeps a
set of accepted access tokens, so the 401 -> refresh -> retry flow in
``AutoRefreshingRepository`` can be exercised for real instead of being mocked
away.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from flask import Flask, Response, request
from werkzeug.serving import BaseWSGIServer, make_server

#: Path prefix the frontend is configured with (`DRF_API_BASE_URL`).
API_PREFIX = "/api/v1"

#: Default token pair handed out by the stubbed login endpoint.
DEFAULT_ACCESS_TOKEN = "mock-access-token-123"
DEFAULT_REFRESH_TOKEN = "mock-refresh-token-456"

#: A token the stub always rejects, so tests can drive the refresh flow by
#: seeding the browser cookie with it.
EXPIRED_ACCESS_TOKEN = "expired-access-token"

#: Access token minted by the stubbed refresh endpoint.
ROTATED_ACCESS_TOKEN = "rotated-access-token-789"
ROTATED_REFRESH_TOKEN = "rotated-refresh-token-012"


@dataclass(frozen=True)
class RecordedRequest:
    """A single request the frontend made against the stub."""

    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    json: Any

    @property
    def authorization(self) -> str | None:
        return self.headers.get("Authorization")

    @property
    def bearer_token(self) -> str | None:
        auth = self.authorization
        if auth and auth.startswith("Bearer "):
            return auth[len("Bearer ") :]
        return None


@dataclass(frozen=True)
class StubResponse:
    """What the stub replies with. ``body=None`` means "no body" (e.g. 204)."""

    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


#: A route is either a fixed response or a callable computing one per request.
Route = StubResponse | Callable[[RecordedRequest], StubResponse]


def paginated(results: list[dict], count: int | None = None) -> dict:
    """Wrap results in the `count`/`next`/`previous`/`results` DRF envelope."""
    return {
        "count": len(results) if count is None else count,
        "next": None,
        "previous": None,
        "results": results,
    }


def summary_payload(
    period: str,
    total_income: str,
    total_expense: str,
    net_balance: str,
    start_date: str,
    end_date: str,
    total_investment: str = "0.00",
    total_savings: str = "0.00",
) -> dict:
    """A dashboard summary exactly as the backend serialises it."""
    return {
        "period": period,
        "total_income": total_income,
        "total_expense": total_expense,
        "total_investment": total_investment,
        "total_savings": total_savings,
        "net_balance": net_balance,
        "start_date": start_date,
        "end_date": end_date,
    }


#: A realistic overview. Within each card the net balance is deliberately
#: different from the income and expense totals, so an assertion for one figure
#: cannot accidentally match another.
DEFAULT_OVERVIEW: dict = {
    "today": summary_payload(
        "daily", "100.00", "62.50", "37.50", "2026-09-26", "2026-09-26"
    ),
    "this_week": summary_payload(
        "weekly", "500.00", "300.00", "200.00", "2026-09-21", "2026-09-27"
    ),
    "this_month": summary_payload(
        "monthly", "2000.00", "1500.00", "500.00", "2026-09-01", "2026-09-30"
    ),
}

#: An overview the backend sends when the user has no data at all.
EMPTY_OVERVIEW: dict = {"today": None, "this_week": None, "this_month": None}

#: The model name the health probe reports. Only ever shown in a tooltip, but
#: pinned so an assertion on it would not be asserting on a random string.
DEFAULT_LLM_MODEL: str = "qwen2.5:3b"

#: The user the stubbed `/users/me/` endpoint reports.
DEFAULT_USER: dict = {
    "id": "user-123",
    "email": "test@example.com",
    "full_name": "Test User",
    "plan": "free",
    "is_active": True,
    "is_staff": False,
    "is_superuser": False,
}

#: The profile the stubbed `/profile/me/` endpoints serve. Matches the
#: backend's DRFProfileResponse shape exactly.
DEFAULT_PROFILE: dict = {
    "id": "user-123",
    "first_name": "Test",
    "last_name": "User",
    "timezone": "UTC",
    "language": "es",
    "currency": "USD",
    "date_format": "YYYY-MM-DD",
    "avatar_url": None,
    "bio": None,
}


def default_transaction(
    transaction_id: str = "tx-1",
    transaction_type: str = "expense",
    amount: str = "50.00",
    description: str = "Test transaction",
    category: str = "General",
    date: str = "2026-09-26",
) -> dict:
    """A transaction exactly as the backend serialises it."""
    return {
        "id": transaction_id,
        "transaction_type": transaction_type,
        "amount": amount,
        "description": description,
        "category": category,
        "date": date,
        "created_at": "2026-09-26T10:00:00Z",
        "updated_at": "2026-09-26T10:00:00Z",
    }


def transaction_payload(transaction: dict) -> dict:
    """Expand the compact shape tests use into a full DRF payload.

    Tests spell out only the fields they assert on, so everything else is
    filled in with realistic values.
    """
    return default_transaction(
        transaction_id=transaction.get("id", "tx-1"),
        transaction_type=transaction.get("type", "expense"),
        amount=transaction.get("amount", "50.00"),
        description=transaction.get("description", "Test transaction"),
        category=transaction.get("category", "General"),
        date=transaction.get("date", "2026-09-26"),
    )


class StubBackend:
    """Routes requests to per-test handlers and records what it received.

    Handlers are keyed by ``(method, path)`` where ``path`` is the part of the
    URL after ``/api/v1`` and always keeps its trailing slash, e.g.
    ``("GET", "/transactions/")``. Any unstubbed combination is recorded and
    answered with 404, which turns "the frontend called something we did not
    expect" into a readable failure instead of a mystery 500.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._routes: dict[tuple[str, str], Route] = {}
        #: Every request seen, in order, across all of a test's page loads.
        self.requests: list[RecordedRequest] = []
        #: Backend-owned transaction rows; the list endpoint serves these.
        self.transactions: list[dict] = []
        #: Backend-owned user rows; the /users/ endpoints serve these.
        self.users: list[dict] = []
        #: Backend-owned task rows; the /tasks/tasks/ endpoints serve these.
        self.tasks: list[dict] = []
        #: Backend-owned goal rows; the /tasks/goals/ endpoint serves these.
        self.goals: list[dict] = []
        #: Backend-owned daily plans; the /tasks/daily-plans/ endpoint serves these.
        self.daily_plans: list[dict] = []
        #: Backend-owned shopping lists; `/purchases/shopping-lists/` serves
        #: these, filtered by `is_active` as the backend does.
        self.shopping_lists: list[dict] = []
        #: The caller's single wishlist, or `None` until the first read creates
        #: it -- which is what `GetOrCreateWishlist` does.
        self.wishlist: dict | None = None
        #: The shared, global tag catalogue. Not owner-scoped, as in the backend.
        self.purchases_tags: list[dict] = []
        #: Whether the fake LLM provider answers. False makes the health probe,
        #: both task triggers, and daily-plan generation answer 503, which is how
        #: the real backend reports an unreachable provider.
        self.llm_available: bool = True
        #: Which 409 `add-task` should answer, if any: ``None`` to succeed, or
        #: ``"done"``/``"full"``. The backend raises `TaskAlreadyDone` and
        #: `DailyPlanFull` under their own conditions, so a test that wants
        #: either one has to be able to ask for it.
        self.add_daily_plan_task_conflict: str | None = None
        #: Which status action should answer 409: ``"mark-doing"``,
        #: ``"mark-done"``, or ``"reopen"``, or ``None`` for a clean success.
        #: Set by a test that needs the board's optimistic update to be reverted.
        self.transition_failure: str | None = None
        #: The timestamp `mark-done` stamps onto a task. Fixed rather than
        #: `datetime.now()` so a test can assert the exact completion time the
        #: card renders.
        self.stub_now: str = "2026-10-05T14:31:12.480000Z"
        #: Access tokens the stub accepts. Anything else gets a 401.
        self.valid_access_tokens: set[str] = set()
        self.overview: dict = DEFAULT_OVERVIEW
        self.user: dict = DEFAULT_USER
        self.profile: dict = dict(DEFAULT_PROFILE)
        self._next_id = 0
        self.reset()

    # -- configuration ---------------------------------------------------

    def reset(self) -> None:
        """Restore the default happy-path behaviour between tests."""
        with self._lock:
            self._routes.clear()
            self.requests.clear()
            self.transactions = []
            self.users = []
            self.tasks = []
            self.goals = []
            self.daily_plans = []
            self.shopping_lists = []
            self.wishlist = None
            self.purchases_tags = []
            self.llm_available = True
            self.add_daily_plan_task_conflict = None
            self.transition_failure = None
            self.valid_access_tokens = {DEFAULT_ACCESS_TOKEN, ROTATED_ACCESS_TOKEN}
            self.overview = DEFAULT_OVERVIEW
            self.user = DEFAULT_USER
            self.profile = dict(DEFAULT_PROFILE)
            self._next_id = 0
            self._install_defaults()

    def on(self, method: str, path: str, response: StubResponse) -> None:
        """Always answer ``method path`` with ``response``."""
        with self._lock:
            self._routes[(method.upper(), path)] = response

    def on_call(
        self, method: str, path: str, handler: Callable[[RecordedRequest], StubResponse]
    ) -> None:
        """Answer with whatever ``handler`` computes for each request."""
        with self._lock:
            self._routes[(method.upper(), path)] = handler

    def seed_transactions(self, *transactions: dict) -> None:
        """Pre-populate the transaction store the list endpoint serves."""
        with self._lock:
            self.transactions = list(transactions)

    def seed_users(self, *users: dict) -> None:
        with self._lock:
            self.users = list(users)

    def require_token(self, token: str) -> None:
        """Make ``token`` the only accepted access token."""
        with self._lock:
            self.valid_access_tokens = {token}

    # -- introspection ---------------------------------------------------

    def calls(self, method: str, path: str) -> list[RecordedRequest]:
        """Every recorded request matching ``method path``."""
        with self._lock:
            return [
                r
                for r in self.requests
                if r.method == method.upper() and r.path == path
            ]

    # -- dispatch --------------------------------------------------------

    def _token_response(self) -> StubResponse:
        return StubResponse(
            200, {"access": DEFAULT_ACCESS_TOKEN, "refresh": DEFAULT_REFRESH_TOKEN}
        )

    def _refresh_response(self) -> StubResponse:
        return StubResponse(
            200, {"access": ROTATED_ACCESS_TOKEN, "refresh": ROTATED_REFRESH_TOKEN}
        )

    def _me_response(self) -> StubResponse:
        return StubResponse(200, dict(self.user))

    def _list_response(self, recorded: RecordedRequest) -> StubResponse:
        page = max(1, int(recorded.query.get("page", 1) or 1))
        page_size = max(1, int(recorded.query.get("page_size", 20) or 20))
        start = (page - 1) * page_size
        return StubResponse(
            200, paginated(self.transactions[start : start + page_size])
        )

    def _create_response(self, recorded: RecordedRequest) -> StubResponse:
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        self._next_id += 1
        created = {
            "id": payload.get("id") or f"tx-new-{self._next_id}",
            "transaction_type": payload.get("transaction_type", "expense"),
            "amount": payload.get("amount", "0.00"),
            "description": payload.get("description", ""),
            "category": payload.get("category", "General"),
            "date": payload.get("date", "2026-09-26"),
            "created_at": "2026-09-26T10:00:00Z",
            "updated_at": "2026-09-26T10:00:00Z",
        }
        self.transactions.append(created)
        return StubResponse(201, created)

    def _find(self, transaction_id: str) -> dict | None:
        for row in self.transactions:
            if row.get("id") == transaction_id:
                return row
        return None

    def _delete_response(self, transaction_id: str) -> StubResponse:
        for index, row in enumerate(self.transactions):
            if row.get("id") == transaction_id:
                del self.transactions[index]
                return StubResponse(204)
        return StubResponse(404, {"detail": "Not found."})

    def _overview_response(self, _recorded: RecordedRequest) -> StubResponse:
        return StubResponse(200, dict(self.overview))

    def _profile_response(self, _recorded: RecordedRequest) -> StubResponse:
        return StubResponse(200, dict(self.profile))

    def _register_response(self, recorded: RecordedRequest) -> StubResponse:
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        if not payload.get("email") or not payload.get("password"):
            return StubResponse(400, {"detail": "Email and password required."})
        user = {
            "id": "user-new",
            "email": payload["email"],
            "full_name": payload.get("full_name", ""),
            "plan": "free",
            "is_active": True,
            "is_staff": False,
        }
        self.users.append(user)
        return StubResponse(201, user)

    def _users_list_response(self, recorded: RecordedRequest) -> StubResponse:
        page = max(1, int(recorded.query.get("page", 1) or 1))
        page_size = max(1, int(recorded.query.get("page_size", 20) or 20))
        start = (page - 1) * page_size
        return StubResponse(
            200,
            {
                "count": len(self.users),
                "results": self.users[start : start + page_size],
            },
        )

    def _find_user(self, user_id: str) -> dict | None:
        for row in self.users:
            if row.get("id") == user_id:
                return row
        return None

    def _user_item_response(self, recorded: RecordedRequest) -> StubResponse:
        user_id = recorded.path.removeprefix("/users/").removesuffix("/")
        row = self._find_user(user_id)
        if row is None:
            return StubResponse(404, {"detail": "Not found."})
        if recorded.method in {"PATCH", "PUT"}:
            payload = recorded.json if isinstance(recorded.json, dict) else {}
            for key in ("full_name", "is_active"):
                if key in payload:
                    row[key] = payload[key]
            return StubResponse(200, dict(row))
        if recorded.method == "GET":
            return StubResponse(200, dict(row))
        return StubResponse(405, {"detail": "Method not allowed."})

    def _user_plan_response(self, recorded: RecordedRequest) -> StubResponse:
        user_id = recorded.path.removeprefix("/users/").removesuffix("/plan/")
        row = self._find_user(user_id)
        if row is None:
            return StubResponse(404, {"detail": "Not found."})
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        if "plan" in payload:
            row["plan"] = payload["plan"]
        return StubResponse(200, dict(row))

    def _update_profile_response(self, recorded: RecordedRequest) -> StubResponse:
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        for key in ("first_name", "last_name", "timezone", "avatar_url", "bio"):
            if key in payload:
                value = payload[key]
                # The backend treats "" as "clear" for nullable fields.
                if key in ("avatar_url", "bio") and value == "":
                    value = None
                self.profile[key] = value
        return StubResponse(200, dict(self.profile))

    def _update_preferences_response(self, recorded: RecordedRequest) -> StubResponse:
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        for key in ("language", "currency", "date_format"):
            if key in payload:
                self.profile[key] = payload[key]
        return StubResponse(200, dict(self.profile))

    def _install_defaults(self) -> None:
        """Happy-path behaviour, mirroring the real backend's status codes."""
        self.on("POST", "/users/auth/login/", self._token_response())
        self.on("POST", "/users/auth/refresh/", self._refresh_response())
        self.on("POST", "/users/auth/logout/", StubResponse(204))
        self.on_call("GET", "/users/me/", lambda _r: self._me_response())
        self.on_call("GET", "/transactions/", self._list_response)
        self.on_call("POST", "/transactions/", self._create_response)
        self.on_call("GET", "/dashboard/overview/", self._overview_response)
        self.on_call("GET", "/profile/me/", self._profile_response)
        self.on_call("PATCH", "/profile/me/", self._update_profile_response)
        self.on_call(
            "PATCH", "/profile/me/preferences/", self._update_preferences_response
        )
        self.on_call("POST", "/users/auth/register/", self._register_response)
        self.on_call("GET", "/users/", self._users_list_response)
        self.on_call("*", "/users/<id>/", self._user_item_response)
        self.on_call("*", "/users/<id>/plan/", self._user_plan_response)
        # Item routes are per-id, so they are resolved dynamically.
        self.on_call("*", "/transactions/<id>/", self._item_response)

        # Tasks. The health probe is registered by default rather than per-test
        # because the layout calls it on every page load -- without it here,
        # every E2E test would record a 404 the layout never looks at, and the
        # amber dot would be the correct response to a stub that simply has not
        # heard of the endpoint.
        self.on_call("GET", "/tasks/tasks/", self._tasks_list_response)
        self.on_call("POST", "/tasks/tasks/", self._create_task_response)
        self.on_call("GET", "/tasks/goals/", self._goals_list_response)
        # The three status transitions. Registered before the `/tasks/tasks/<id>/`
        # fallback below, which would otherwise swallow all three: they share its
        # exact shape, and a handler that ignored the action segment could not
        # tell `mark-done` from `reopen` -- which is the whole point of testing
        # that the board posts to the right one.
        self.on_call("POST", "/tasks/tasks/<id>/mark-doing/", self._transition_response)
        self.on_call("POST", "/tasks/tasks/<id>/mark-done/", self._transition_response)
        self.on_call("POST", "/tasks/tasks/<id>/reopen/", self._transition_response)
        self.on_call("GET", "/tasks/daily-plans/", self._daily_plans_response)
        self.on_call("POST", "/tasks/daily-plans/", self._create_daily_plan_response)
        # Registered before the `<id>` fallback below, and it has to be: both
        # `generate/` and the plan-detail path have the same shape
        # (`/tasks/daily-plans/<something>/`), and a fallback matcher would
        # swallow whichever was registered later.
        self.on_call(
            "POST", "/tasks/daily-plans/generate/", self._generate_daily_plan_response
        )
        self.on_call(
            "POST", "/tasks/daily-plans/<id>/add-task/", self._add_task_to_plan_response
        )
        self.on_call("GET", "/tasks/tasks/llm-health/", self._llm_health_response)
        self.on_call("POST", "/tasks/tasks/<id>/evaluate/", self._llm_trigger_response)
        self.on_call("POST", "/tasks/tasks/<id>/decompose/", self._llm_trigger_response)
        self.on_call("*", "/tasks/tasks/<id>/", self._task_detail_response)
        self.on_call("*", "/tasks/goals/<id>/", self._goal_detail_response)

        # Purchases. The three collection routes are registered by exact path,
        # because each has a per-id sibling of the same shape (`inactive/` beside
        # `<uuid>/`, for one) and a fallback matcher would swallow whichever was
        # registered later.
        self.on_call("GET", "/purchases/shopping-lists/", self._shopping_lists_response)
        self.on_call(
            "GET",
            "/purchases/shopping-lists/inactive/",
            self._archived_shopping_lists_response,
        )
        self.on_call(
            "POST", "/purchases/shopping-lists/", self._create_shopping_list_response
        )
        self.on_call(
            "*", "/purchases/shopping-lists/<id>/", self._shopping_list_detail_response
        )
        self.on_call(
            "POST",
            "/purchases/shopping-lists/<id>/add-item/",
            self._add_shopping_list_item_response,
        )
        self.on_call(
            "POST",
            "/purchases/shopping-lists/<id>/items/<item_id>/mark-purchased/",
            self._mark_shopping_list_item_response,
        )
        self.on_call("GET", "/purchases/wishlist/", self._wishlist_response)
        self.on_call("PATCH", "/purchases/wishlist/", self._update_wishlist_response)
        self.on_call(
            "POST", "/purchases/wishlist/add-item/", self._add_wishlist_item_response
        )
        self.on_call(
            "POST",
            "/purchases/wishlist/items/<item_id>/mark-purchased/",
            self._mark_wishlist_item_response,
        )
        self.on_call("GET", "/purchases/tags/", self._tags_response)
        self.on_call("POST", "/purchases/tags/", self._create_tag_response)

    # -- purchases ---------------------------------------------------------

    def _shopping_lists_response(self, _recorded: RecordedRequest) -> StubResponse:
        """`GET /shopping-lists/` -- active lists only.

        The stub filters by `is_active` rather than serving a fixed list, because
        the backend does: a soft-deleted list is simply absent here. A stub that
        ignored it would let a page showing an archived list as active pass.
        """
        return StubResponse(
            200, [row for row in self.shopping_lists if row.get("is_active", True)]
        )

    def _archived_shopping_lists_response(
        self, _recorded: RecordedRequest
    ) -> StubResponse:
        """`GET /shopping-lists/inactive/` -- the soft-deleted ones."""
        return StubResponse(
            200, [row for row in self.shopping_lists if not row.get("is_active", True)]
        )

    def _find_shopping_list(self, list_id: str) -> dict | None:
        return next(
            (row for row in self.shopping_lists if row.get("id") == list_id), None
        )

    def _shopping_list_detail_response(self, recorded: RecordedRequest) -> StubResponse:
        list_id = recorded.path.removeprefix("/purchases/shopping-lists/").removesuffix(
            "/"
        )
        row = self._find_shopping_list(list_id)
        # 404 for "missing" and "not yours" alike, which is what the backend
        # answers and what the BFF is not entitled to distinguish.
        if row is None:
            return StubResponse(404, {"detail": "Not found."})
        return StubResponse(200, row)

    def _create_shopping_list_response(self, recorded: RecordedRequest) -> StubResponse:
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        name = str(payload.get("name", "")).strip()
        if not name:
            return StubResponse(400, {"name": ["This field may not be blank."]})
        if payload.get("priority") not in (None, "high", "medium", "low"):
            return StubResponse(400, {"priority": ['"x" is not a valid choice.']})
        row = {
            # A real UUID: the BFF's `ShoppingListId` validates as one, so an
            # incrementing string here would be rejected inside the mapper and
            # read as a broken feature rather than as a bad fixture.
            "id": str(uuid4()),
            "name": name,
            "priority": payload.get("priority", "medium"),
            "status": "pending",
            "is_active": True,
            "created_at": self.stub_now,
            "completed_at": None,
            "items": [],
            "tags": [],
        }
        self.shopping_lists.append(row)
        return StubResponse(201, row)

    def _add_shopping_list_item_response(
        self, recorded: RecordedRequest
    ) -> StubResponse:
        list_id = recorded.path.removeprefix("/purchases/shopping-lists/").removesuffix(
            "/add-item/"
        )
        row = self._find_shopping_list(list_id)
        if row is None:
            return StubResponse(404, {"detail": "Not found."})
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        name = str(payload.get("name", "")).strip()
        if not name:
            return StubResponse(400, {"name": ["This field may not be blank."]})
        quantity = payload.get("quantity", 1)
        if not isinstance(quantity, int) or quantity < 1:
            # `AddShoppingListItemSerializer` declares `min_value=1`.
            return StubResponse(
                400, {"quantity": ["Ensure this value is greater than or equal to 1."]}
            )
        row["items"].append(
            {
                "id": str(uuid4()),
                "name": name,
                "quantity": quantity,
                "details": payload.get("details") or None,
                "status": "pending",
                "tags": [],
            }
        )
        # 201 with the whole aggregate, which is the only place the new item's id
        # appears -- there is no item-collection endpoint for shopping lists.
        return StubResponse(201, row)

    def _mark_shopping_list_item_response(
        self, recorded: RecordedRequest
    ) -> StubResponse:
        segments = recorded.path.strip("/").split("/")
        # purchases/shopping-lists/<list>/items/<item>/mark-purchased
        list_id, item_id = segments[2], segments[4]
        row = self._find_shopping_list(list_id)
        if row is None:
            return StubResponse(404, {"detail": "Not found."})
        item = next((i for i in row["items"] if i.get("id") == item_id), None)
        # The nesting is pinned server-side: an item from another list of the
        # caller's own is refused rather than mutated.
        if item is None:
            return StubResponse(404, {"detail": "Shopping list item not found."})
        item["status"] = "purchased"
        self._refresh_list_status(row)
        return StubResponse(200, row)

    def _refresh_list_status(self, row: dict) -> None:
        """Recompute a list's `status` and `completed_at`, as the backend does.

        The empty-list rule matters and is easy to get wrong: a list with no
        items is **never** completed, so a freshly created list cannot read as
        finished.
        """
        items = row.get("items") or []
        completed = bool(items) and all(i.get("status") == "purchased" for i in items)
        row["status"] = "purchased" if completed else "pending"
        row["completed_at"] = self.stub_now if completed else None

    def _wishlist_response(self, _recorded: RecordedRequest) -> StubResponse:
        """`GET /wishlist/` -- get-or-create, 200 either way.

        Never 404 and never a 201-that-is-a-create: the backend routes this to
        `GetOrCreateWishlist`, and the BFF has no separate create call.
        """
        if self.wishlist is None:
            self.wishlist = {
                "id": str(uuid4()),
                "name": "My Wishlist",
                "status": "pending",
                "is_active": True,
                "created_at": self.stub_now,
                "completed_at": None,
                "items": [],
            }
        return StubResponse(200, self.wishlist)

    def _update_wishlist_response(self, recorded: RecordedRequest) -> StubResponse:
        if self.wishlist is None:
            return self._wishlist_response(recorded)
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        if "name" in payload:
            self.wishlist["name"] = (
                str(payload["name"]).strip() or self.wishlist["name"]
            )
        return StubResponse(200, self.wishlist)

    def _add_wishlist_item_response(self, recorded: RecordedRequest) -> StubResponse:
        if self.wishlist is None:
            self._wishlist_response(recorded)
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        name = str(payload.get("name", "")).strip()
        if not name:
            return StubResponse(400, {"name": ["This field may not be blank."]})
        price = payload.get("estimated_price")
        if price is not None:
            try:
                if float(price) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return StubResponse(
                    400, {"estimated_price": ["Ensure this value is greater than 0."]}
                )
        self.wishlist["items"].append(
            {
                "id": str(uuid4()),
                "name": name,
                "link": payload.get("link") or None,
                "image_link": payload.get("image_link") or None,
                "description": payload.get("description") or None,
                # Rendered as a string, because DRF's COERCE_DECIMAL_TO_STRING is
                # on by default and the BFF's schema types it as one.
                "estimated_price": None if price is None else f"{float(price):.2f}",
                "priority": payload.get("priority", "wanted"),
                "status": "pending",
                "tags": [],
            }
        )
        return StubResponse(201, self.wishlist)

    def _mark_wishlist_item_response(self, recorded: RecordedRequest) -> StubResponse:
        if self.wishlist is None:
            return StubResponse(404, {"detail": "Not found."})
        item_id = recorded.path.strip("/").split("/")[3]
        item = next((i for i in self.wishlist["items"] if i.get("id") == item_id), None)
        if item is None:
            return StubResponse(404, {"detail": "Wishlist item not found."})
        item["status"] = "purchased"
        items = self.wishlist["items"]
        completed = bool(items) and all(i.get("status") == "purchased" for i in items)
        self.wishlist["status"] = "purchased" if completed else "pending"
        self.wishlist["completed_at"] = self.stub_now if completed else None
        return StubResponse(200, self.wishlist)

    def _tags_response(self, _recorded: RecordedRequest) -> StubResponse:
        return StubResponse(200, list(self.purchases_tags))

    def _create_tag_response(self, recorded: RecordedRequest) -> StubResponse:
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        name = str(payload.get("name", "")).strip()
        existing = next((t for t in self.purchases_tags if t["name"] == name), None)
        if existing is not None:
            # Idempotent by name, and the 200-vs-201 is the only place the
            # difference is visible -- so the stub reproduces it.
            return StubResponse(200, existing)
        row = {"id": str(uuid4()), "name": name, "slug": name.lower()}
        self.purchases_tags.append(row)
        return StubResponse(201, row)

    def _item_response(self, recorded: RecordedRequest) -> StubResponse:
        transaction_id = recorded.path.removeprefix("/transactions/").removesuffix("/")
        if recorded.method == "GET":
            row = self._find(transaction_id)
            if row is None:
                return StubResponse(404, {"detail": "Not found."})
            return StubResponse(200, row)
        if recorded.method == "DELETE":
            return self._delete_response(transaction_id)
        if recorded.method in {"PATCH", "PUT"}:
            row = self._find(transaction_id)
            if row is None:
                return StubResponse(404, {"detail": "Not found."})
            payload = recorded.json if isinstance(recorded.json, dict) else {}
            row.update(payload)
            return StubResponse(200, row)
        return StubResponse(405, {"detail": "Method not allowed."})

    # -- tasks ------------------------------------------------------------

    def _tasks_list_response(self, recorded: RecordedRequest) -> StubResponse:
        """Serve the task list, honouring the `status` and `goal_id` filters.

        `TaskFilterQuerySerializer` declares `status` as a choice over
        ``TASK_STATUS_CHOICES`` -- ``todo`` / ``doing`` / ``done`` -- and
        ``goal_id`` as an optional UUID. A stub that ignored the filters would
        serve the whole backlog as the "completed" list, and every assertion
        about the finished-tasks section would pass for the wrong reason. An
        unrecognised `status` answers 400 exactly as the backend does, so a
        client that sent ``completed`` would fail here rather than quietly
        appear to work.

        `goal_id` is honoured **independently** of `status`, which is the whole
        point of using it: a goal detail page asks for a goal's tasks with no
        status at all and needs the finished ones back. A stub that required both
        filters to be absent-together, or that tied them together, would hide
        exactly the bug that matters there.
        """
        rows = list(self.tasks)
        goal_id = recorded.query.get("goal_id")
        if goal_id is not None:
            rows = [row for row in rows if row.get("goal_id") == goal_id]
        status = recorded.query.get("status")
        if status is None:
            return StubResponse(200, rows)
        if status not in {"todo", "doing", "done"}:
            return StubResponse(400, {"detail": "Unknown task status."})
        return StubResponse(200, [row for row in rows if row.get("status") == status])

    def _task_detail_response(self, recorded: RecordedRequest) -> StubResponse:
        """GET/PATCH/DELETE on one task, as `TaskViewSet` exposes them.

        Registered with `"*"` because the collection routes are matched first,
        so this only ever sees a detail path.
        """
        task_id = recorded.path.removeprefix("/tasks/tasks/").removesuffix("/")
        row = next((t for t in self.tasks if t.get("id") == task_id), None)
        if row is None:
            return StubResponse(404, {"detail": "Not found."})
        if recorded.method == "GET":
            return StubResponse(200, row)
        if recorded.method == "DELETE":
            self.tasks.remove(row)
            # 204 with an empty body, as the backend's `destroy` answers.
            return StubResponse(204)
        if recorded.method in {"PATCH", "PUT"}:
            payload = recorded.json if isinstance(recorded.json, dict) else {}
            # `partial=True`, so only the keys actually sent are written --
            # absent keys leave the stored column alone. Mirrored here because a
            # stub that overwrote everything would make a BFF sending the whole
            # entity look correct.
            for key, value in payload.items():
                if key == "importance":
                    row["importance"] = value
                elif key == "estimated_hours" and value is None:
                    row["estimated_hours"] = "0.00"
                else:
                    row[key] = value
            return StubResponse(200, row)
        return StubResponse(405, {"detail": "Method not allowed."})

    #: Which of the three transitions `transition_failure` should refuse, and
    #: with which 409. The backend raises `TaskAlreadyDone` from both
    #: `mark-doing` and `mark-done`, and `TaskNotCompleted` from `reopen`, so a
    #: stub that always succeeded would let a board mishandle a rejection pass.
    TRANSITION_FAILURES = {
        "mark-doing": (409, "A completed task cannot be moved back to doing."),
        "mark-done": (409, "Task is already marked as done."),
        "reopen": (409, "Only a completed task can be reopened."),
    }

    def _transition_response(self, recorded: RecordedRequest) -> StubResponse:
        """One of `mark-doing` / `mark-done` / `reopen`.

        The action is taken from the path, not from the row's current status --
        which is the point. The stub does not re-implement the backend's rules;
        it applies the transition it was asked for, and answers 409 only when the
        test explicitly asked for a failure. A stub that *checked* the status
        itself would hide exactly the bug worth catching: the board posting
        `reopen` for a task that was never finished.
        """
        segments = recorded.path.rstrip("/").split("/")
        action = segments[-1]
        task_id = segments[-2]
        row = next((t for t in self.tasks if t.get("id") == task_id), None)
        if row is None:
            return StubResponse(404, {"detail": "Not found."})

        if self.transition_failure == action:
            code, message = self.TRANSITION_FAILURES[action]
            return StubResponse(code, {"detail": message})

        if action == "mark-doing":
            row["status"] = "doing"
            row["completed_at"] = None
        elif action == "mark-done":
            row["status"] = "done"
            row["completed_at"] = self.stub_now
        elif action == "reopen":
            row["status"] = "todo"
            row["completed_at"] = None
        else:
            return StubResponse(404, {"detail": f"Unstubbed action {action}."})

        # `is_overwhelmed` is recomputed server-side against the day's load, and
        # re-sending the stored value would let a stale badge pass an E2E.
        row["is_overwhelmed"] = False
        return StubResponse(200, row)

    def _goals_list_response(self, _recorded: RecordedRequest) -> StubResponse:
        return StubResponse(200, list(self.goals))

    def _goal_detail_response(self, recorded: RecordedRequest) -> StubResponse:
        """GET one goal, as `GoalViewSet.retrieve` exposes it.

        A 404 for a goal that is not in the store, which is what the backend
        answers for both "missing" and "not yours" -- a stub that invented a
        third outcome would let a page that renders an empty goal instead of
        saying "no longer exists" pass here.
        """
        goal_id = recorded.path.removeprefix("/tasks/goals/").removesuffix("/")
        row = next((g for g in self.goals if g.get("id") == goal_id), None)
        if row is None:
            return StubResponse(404, {"detail": "Not found."})
        return StubResponse(200, row)

    def _daily_plans_response(self, _recorded: RecordedRequest) -> StubResponse:
        return StubResponse(200, list(self.daily_plans))

    def _create_daily_plan_response(self, recorded: RecordedRequest) -> StubResponse:
        """POST /daily-plans/ -- `GetOrCreateDailyPlan`, so 201 either way.

        Returns the existing plan when the date already has one, which is the
        backend's behaviour and the reason a client cannot tell "created" from
        "found" and does not need to.
        """
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        plan_date = payload.get("date")
        existing = next(
            (p for p in self.daily_plans if p.get("date") == plan_date), None
        )
        if existing is not None:
            return StubResponse(201, existing)
        self._next_id += 1
        plan = {
            # A real UUID, not an incrementing string. `DailyPlanId` validates as
            # a UUID, so a `plan-2` here would be rejected inside the mapper and
            # the failure would look like a broken feature rather than a
            # fixture that does not match the API.
            "id": str(uuid4()),
            "date": plan_date,
            "tasks": [],
            "total_hours": "0.00",
            "is_full": False,
            "generated_by_llm": False,
            "created_at": "2026-10-05T06:00:00Z",
        }
        self.daily_plans.append(plan)
        return StubResponse(201, plan)

    def _generate_daily_plan_response(self, recorded: RecordedRequest) -> StubResponse:
        """POST /daily-plans/generate/ -- 202, queued, and no plan.

        The shape matters more here than anywhere else in this stub: the real
        endpoint hands the work to Celery and returns immediately, so a 200 with a
        plan would let a BFF that claims "your day is planned" pass, and a 200
        with an *empty* body would too. Neither is what the backend sends.
        """
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        if not self.llm_available:
            return StubResponse(503, {"detail": "The LLM provider is unreachable."})
        return StubResponse(
            202,
            {
                "detail": "Daily plan generation queued.",
                "date": payload.get("date"),
                "queued": True,
            },
        )

    def _add_task_to_plan_response(self, recorded: RecordedRequest) -> StubResponse:
        """POST /daily-plans/<id>/add-task/ -- 200 with the reloaded plan.

        The 409s are reproduced because they are three distinct outcomes the UI
        has to word differently, and a stub that always succeeded would let a
        button that mishandles them look fine:

        * a task already on the plan -> `TaskAlreadyInPlan`
        * a finished task -> `TaskAlreadyDone`
        * the day is over its hour budget -> `DailyPlanFull`

        `add_daily_plan_task_conflict` is how a test asks for one of the last
        two; the first is unconditional, as on the backend.
        """
        plan_id = recorded.path.removeprefix("/tasks/daily-plans/").removesuffix(
            "/add-task/"
        )
        plan = next((p for p in self.daily_plans if p.get("id") == plan_id), None)
        if plan is None:
            return StubResponse(404, {"detail": "Not found."})
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        task_id = payload.get("task_id")
        task = next((t for t in self.tasks if t.get("id") == task_id), None)
        if task is None:
            return StubResponse(404, {"detail": "Not found."})
        if any(row.get("id") == task_id for row in plan["tasks"]):
            return StubResponse(409, {"detail": "This task is already on the plan."})
        if self.add_daily_plan_task_conflict == "done" and task.get("status") == "done":
            return StubResponse(409, {"detail": "A completed task cannot be planned."})
        if self.add_daily_plan_task_conflict == "full":
            return StubResponse(409, {"detail": "The day is already full."})
        plan["tasks"].append(task)
        plan["total_hours"] = self._sum_hours(plan["tasks"])
        return StubResponse(200, plan)

    @staticmethod
    def _sum_hours(rows: list[dict]) -> str:
        total = sum(float(row.get("estimated_hours") or 0) for row in rows)
        return f"{total:.2f}"

    def _llm_health_response(self, _recorded: RecordedRequest) -> StubResponse:
        # The shape is `LlmHealthSerializer`'s, confirmed against the backend:
        # 200 {"status": "ok", "model": ...} and 503 {"status": "unavailable",
        # "model": null, "error": ...}. There is no `available` boolean, and a
        # stub that invented one would hide a mapper written against the wrong
        # contract -- which is exactly what happened before this was corrected.
        if self.llm_available:
            return StubResponse(200, {"status": "ok", "model": DEFAULT_LLM_MODEL})
        return StubResponse(
            503,
            {
                "status": "unavailable",
                "model": None,
                "error": "The LLM provider is unreachable.",
            },
        )

    def _llm_trigger_response(self, _recorded: RecordedRequest) -> StubResponse:
        if not self.llm_available:
            return StubResponse(503, {"detail": "The LLM provider is unreachable."})
        # 202 with an empty body: queued, not done. The BFF maps this to `None`,
        # and a 200 here would hide the distinction the flash wording rests on.
        return StubResponse(202)

    def _create_task_response(self, recorded: RecordedRequest) -> StubResponse:
        payload = recorded.json if isinstance(recorded.json, dict) else {}
        self._next_id += 1
        row = {
            # A real UUID: `TaskSerializer.id` is a `UUIDField` and `TaskId`
            # validates as one, so an incrementing string here would be rejected
            # inside the mapper and read as a broken feature rather than as a
            # fixture that does not match the API.
            "id": str(uuid4()),
            "name": payload.get("name", "New task"),
            "description": payload.get("description"),
            "goal_id": payload.get("goal_id"),
            "due_date": payload.get("due_date"),
            "importance": payload.get("importance", "medium"),
            "priority": payload.get("priority", "medium"),
            "status": "todo",
            "estimated_hours": payload.get("estimated_hours"),
            "is_checked_by_llm": False,
            "llm_evaluation_failed": False,
            "is_overwhelmed": None,
        }
        self.tasks.append(row)
        return StubResponse(201, row)

    # -- auth enforcement ------------------------------------------------

    #: Endpoints reachable without an access token. ``/users/auth/logout/`` is
    #: deliberately absent: the real one answers 401 to an anonymous call, and
    #: `DRFAPIClient.logout` tolerates that, so the stub must too.
    PUBLIC_PATHS = frozenset(
        {"/users/auth/login/", "/users/auth/refresh/", "/users/auth/register/"}
    )

    def _unauthorized(self) -> StubResponse:
        return StubResponse(401, {"detail": "Given token not valid."})

    def dispatch(
        self, method: str, path: str, recorded: RecordedRequest
    ) -> StubResponse:
        """Resolve one request to a response, recording it either way."""
        with self._lock:
            self.requests.append(recorded)

            if path not in self.PUBLIC_PATHS:
                if recorded.bearer_token not in self.valid_access_tokens:
                    return self._unauthorized()

            route = self._routes.get((method.upper(), path))
            if route is None and "/transactions/" in path and path.count("/") == 3:
                route = self._routes.get(("*", "/transactions/<id>/"))
            if (
                route is None
                and path.startswith("/users/")
                and not path.startswith("/users/auth/")
                and path.count("/") == 3
                and path != "/users/me/"
            ):
                route = self._routes.get(("*", "/users/<id>/"))
            if route is None and path.startswith("/users/") and path.count("/") == 4:
                route = self._routes.get(("*", "/users/<id>/plan/"))
            if (
                route is None
                and path.startswith("/tasks/tasks/")
                and path.count("/") == 5
            ):
                # One of the three status actions. A fallback, like the detail
                # route below, and registered after them -- so the *literal*
                # action segment is what decides which transition handler runs.
                route = self._routes.get(
                    ("POST", f"/tasks/tasks/<id>/{path.rstrip('/').split('/')[-1]}/")
                )
            if (
                route is None
                and path.startswith("/tasks/tasks/")
                and path.count("/") == 4
            ):
                # The task detail route. Reached only by fallback, so the exact
                # routes registered above still win -- which matters for
                # `/tasks/tasks/llm-health/`, which has the same shape and is
                # matched first because it was registered by exact path.
                route = self._routes.get(("*", "/tasks/tasks/<id>/"))
            if (
                route is None
                and path.startswith("/tasks/goals/")
                and path.count("/") == 4
            ):
                route = self._routes.get(("*", "/tasks/goals/<id>/"))
            if (
                route is None
                and path.startswith("/tasks/daily-plans/")
                and path.count("/") == 5
            ):
                # `<id>/add-task/`. `generate/` is one segment shorter and was
                # registered by exact path above, so it never reaches here.
                route = self._routes.get(("POST", "/tasks/daily-plans/<id>/add-task/"))
        if (
            route is None
            and path.startswith("/purchases/shopping-lists/")
            and path.count("/") == 4
        ):
            # Reached only by fallback, so the exact collection routes
            # registered above always win -- which is what keeps
            # `/shopping-lists/inactive/` from being read as a list whose id
            # happens to be the string "inactive".
            route = self._routes.get(("*", "/purchases/shopping-lists/<id>/"))
        if (
            route is None
            and path.startswith("/purchases/shopping-lists/")
            and path.count("/") == 5
            and path.endswith("/add-item/")
        ):
            route = self._routes.get(
                ("POST", "/purchases/shopping-lists/<id>/add-item/")
            )
        if (
            route is None
            and path.startswith("/purchases/shopping-lists/")
            and path.count("/") == 7
            and path.endswith("/mark-purchased/")
        ):
            # The nested item action: `/shopping-lists/<list>/items/<item>/`.
            route = self._routes.get(
                (
                    "POST",
                    "/purchases/shopping-lists/<id>/items/<item_id>/mark-purchased/",
                )
            )
        if (
            route is None
            and path.startswith("/purchases/wishlist/items/")
            and path.count("/") == 6
            and path.endswith("/mark-purchased/")
        ):
            route = self._routes.get(
                ("POST", "/purchases/wishlist/items/<item_id>/mark-purchased/")
            )

        if route is None:
            return StubResponse(404, {"detail": f"Unstubbed: {method} {path}"})

        if callable(route):
            return route(recorded)
        return route


class StubDRFServer:
    """Serves :class:`StubBackend` over HTTP on a background thread."""

    def __init__(self) -> None:
        self.backend = StubBackend()
        self._server: BaseWSGIServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise RuntimeError("StubDRFServer is not started")
        return f"http://127.0.0.1:{self.port}{API_PREFIX}"

    def reset(self) -> None:
        """Restore the backend's default happy-path behaviour."""
        self.backend.reset()

    def seed_transactions(self, *transactions: dict) -> None:
        self.backend.seed_transactions(*transactions)

    def seed_users(self, *users: dict) -> None:
        self.backend.seed_users(*users)

    def seed_profile(self, profile: dict) -> None:
        """Replace the profile the stub serves on `/profile/me/`."""
        merged = dict(DEFAULT_PROFILE)
        merged.update(profile)
        self.backend.profile = merged

    def seed_tasks(self, *tasks: dict, goals: list[dict] | None = None) -> None:
        """Replace the task and goal rows the tasks endpoints serve.

        Reached through `seed_tasks` rather than by assigning `stub.tasks`
        directly: `StubDRFServer` and `StubBackend` are different objects, and an
        assignment on the former sets an attribute nothing reads. `tasks` is a
        read-only property below so that mistake raises instead of silently
        serving an empty list.

        **Deep-copied, and that is load-bearing.** The detail handlers mutate the
        stored rows in place -- a PATCH has to write through to the row the list
        endpoint serves, or an edit would vanish on the next page load. A test
        that seeded a module-level payload dict would therefore have that
        constant rewritten by the first edit, and every later test using it
        would run against corrupted fixture data. The copy keeps each test's
        rows private to that test.
        """
        self.backend.tasks = [deepcopy(task) for task in tasks]
        if goals is not None:
            self.backend.goals = [deepcopy(goal) for goal in goals]

    @property
    def tasks(self) -> list[dict]:
        return self.backend.tasks

    @property
    def goals(self) -> list[dict]:
        return self.backend.goals

    @property
    def llm_available(self) -> bool:
        return self.backend.llm_available

    @llm_available.setter
    def llm_available(self, value: bool) -> None:
        self.backend.llm_available = value

    @property
    def add_daily_plan_task_conflict(self) -> str | None:
        return self.backend.add_daily_plan_task_conflict

    @add_daily_plan_task_conflict.setter
    def add_daily_plan_task_conflict(self, value: str | None) -> None:
        self.backend.add_daily_plan_task_conflict = value

    @property
    def transition_failure(self) -> str | None:
        return self.backend.transition_failure

    @transition_failure.setter
    def transition_failure(self, value: str | None) -> None:
        self.backend.transition_failure = value

    @property
    def stub_now(self) -> str:
        return self.backend.stub_now

    @stub_now.setter
    def stub_now(self, value: str) -> None:
        self.backend.stub_now = value

    def seed_daily_plans(self, *plans: dict) -> None:
        """Replace the daily plans the stub serves.

        Deep-copied for the same reason :meth:`seed_tasks` is: ``add-task``
        mutates the stored rows in place, so a test that seeded a module-level
        constant would have it rewritten by its own first click.
        """
        self.backend.daily_plans = [deepcopy(plan) for plan in plans]

    @property
    def requests(self) -> list[RecordedRequest]:
        """Every request the stub has seen. On the server, not the backend,
        because that is the object a test is handed."""
        return self.backend.requests

    def require_token(self, token: str) -> None:
        self.backend.require_token(token)

    def on(self, method: str, path: str, response: StubResponse) -> None:
        self.backend.on(method, path, response)

    def on_call(
        self, method: str, path: str, handler: Callable[[RecordedRequest], StubResponse]
    ) -> None:
        self.backend.on_call(method, path, handler)

    def calls(self, method: str, path: str) -> list[RecordedRequest]:
        return self.backend.calls(method, path)

    def _build_app(self) -> Flask:
        """Expose ``_respond`` as a catch-all Flask route."""
        app = Flask("drf_stub")
        respond = self._respond

        @app.route(
            f"{API_PREFIX}/<path:subpath>",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        )
        def handle(subpath: str) -> Response:
            method = "GET" if request.method == "HEAD" else request.method
            return respond(method, subpath)

        return app

    def _respond(self, method: str, subpath: str) -> Response:
        path = f"/{subpath}"
        if not path.endswith("/"):
            path += "/"
        recorded = RecordedRequest(
            method=method,
            path=path,
            query=dict(request.args),
            headers={key: value for key, value in request.headers.items()},
            json=request.get_json(silent=True),
        )
        result = self.backend.dispatch(method, path, recorded)
        headers = getattr(result, "headers", {})
        if result.body is None:
            return Response(status=result.status, headers=headers)
        return Response(
            response=json.dumps(result.body),
            status=result.status,
            content_type="application/json",
            headers=headers,
        )

    def start(self, host: str = "127.0.0.1", port: int = 0) -> StubDRFServer:
        # `threaded=True` matters: the Flask app blocks on `httpx` calls into
        # this server while it serves a request, so a single-threaded server
        # would deadlock the moment the frontend calls the backend.
        self._server = make_server(host, port, self._build_app(), threaded=True)
        self.port = self._server.server_port
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="drf-stub", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.port = None
