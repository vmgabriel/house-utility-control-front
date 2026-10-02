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
from dataclasses import dataclass, field
from typing import Any

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
        self.on("GET", "/users/me/", self._me_response())
        self.on_call("GET", "/transactions/", self._list_response)
        self.on_call("POST", "/transactions/", self._create_response)
        self.on_call("GET", "/dashboard/overview/", self._overview_response)
        self.on_call("GET", "/profile/me/", self._profile_response)
        self.on_call("PATCH", "/profile/me/", self._update_profile_response)
        self.on_call(
            "PATCH", "/profile/me/preferences/", self._update_preferences_response
        )
        # Item routes are per-id, so they are resolved dynamically.
        self.on_call("*", "/transactions/<id>/", self._item_response)

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

    # -- auth enforcement ------------------------------------------------

    #: Endpoints reachable without an access token. ``/users/auth/logout/`` is
    #: deliberately absent: the real one answers 401 to an anonymous call, and
    #: `DRFAPIClient.logout` tolerates that, so the stub must too.
    PUBLIC_PATHS = frozenset({"/users/auth/login/", "/users/auth/refresh/"})

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

    def seed_profile(self, profile: dict) -> None:
        """Replace the profile the stub serves on `/profile/me/`."""
        merged = dict(DEFAULT_PROFILE)
        merged.update(profile)
        self.backend.profile = merged

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
        if result.body is None:
            return Response(status=result.status, headers=result.headers)
        return Response(
            response=json.dumps(result.body),
            status=result.status,
            content_type="application/json",
            headers=result.headers,
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
