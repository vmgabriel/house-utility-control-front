"""Async HTTP client for the DRF backend."""

from logging import getLogger
from typing import Any

import httpx

from src.shared.domain.exceptions import AuthenticationError, DomainException

logger = getLogger()


class DRFAPIClientError(DomainException):
    """Base error for DRF API client failures."""

    def __init__(
        self, message: str, status_code: int | None = None, detail: Any = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class UnauthorizedError(DRFAPIClientError):
    """Raised on 401 responses."""


class NotFoundError(DRFAPIClientError):
    """Raised on 404 responses."""


class ValidationError(DRFAPIClientError):
    """Raised on 400 responses (DRF validation errors)."""


class ServiceUnavailableError(DRFAPIClientError):
    """Raised when the DRF backend never returned a response at all.

    This is deliberately distinct from every other ``DRFAPIClientError``. The
    rest mean "the backend answered, and the answer was a problem" -- a 500
    from DRF still carries a status, and the user may just wait and retry. This
    one means the connection failed, was refused, or timed out, so there is no
    application state to speak of. The web layer maps it to a 503 page rather
    than an inline flash, because there is no honest local data to fall back
    on.
    """


class DRFAPIClient:
    """Async HTTP client wrapper for the DRF backend.

    Each request opens its own ``httpx.AsyncClient``. That trades connection
    pooling for not having to manage a long-lived event loop and client
    lifetime from the synchronous Flask entrypoint.
    """

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _build_headers(self, access_token: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        json_data: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, Any]:
        """Execute an HTTP request and return (status_code, parsed_json_or_None).

        Raises :class:`ServiceUnavailableError` when the request could not be
        completed at the transport level, so no ``httpx`` exception ever escapes
        this adapter. That is the boundary's job: callers above it work in terms
        of statuses, not sockets.
        """
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._build_headers(access_token),
                    json=json_data,
                    params=params,
                )
        except httpx.TransportError as exc:
            # Covers the whole transport family: ConnectError, every flavour of
            # TimeoutException, and read/protocol errors. All of them mean the
            # same thing to this layer -- no response exists. Chained with
            # `from` so the original traceback survives in the logs.
            #
            # Only the method and path are recorded. The URL is deliberately
            # left out: it is the one field most likely to end up echoed into a
            # support ticket, and the status code the web layer shows the user
            # is already the useful part.
            raise ServiceUnavailableError(
                f"DRF backend unreachable on {method} {path}: {type(exc).__name__}"
            ) from exc

        # Handle empty responses (e.g., 204 No Content)
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = None

        return response.status_code, body

    async def login(self, email: str, password: str) -> dict:
        status, body = await self._request(
            "POST",
            "/users/auth/login/",
            json_data={"email": email, "password": password},
        )
        if status == 401 or status == 400:
            raise AuthenticationError("Invalid credentials")
        if status >= 400:
            raise DRFAPIClientError("Login failed", status_code=status, detail=body)
        return body

    async def refresh(self, refresh_token: str) -> dict:
        status, body = await self._request(
            "POST", "/users/auth/refresh/", json_data={"refresh": refresh_token}
        )
        if status >= 400:
            raise AuthenticationError("Token refresh failed")
        return body

    async def logout(self, access_token: str) -> None:
        status, _ = await self._request(
            "POST", "/users/auth/logout/", access_token=access_token
        )
        if status >= 400 and status != 401:
            raise DRFAPIClientError("Logout failed", status_code=status)

    async def get_current_user(self, access_token: str) -> dict:
        status, body = await self._request(
            "GET", "/users/me/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to fetch user", status_code=status, detail=body
            )
        return body

    async def list_transactions(
        self, access_token: str, page: int = 1, page_size: int = 20
    ) -> dict:
        status, body = await self._request(
            "GET",
            "/transactions/",
            access_token=access_token,
            params={"page": page, "page_size": page_size},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list transactions", status_code=status, detail=body
            )
        return body

    async def get_transaction(self, access_token: str, transaction_id: str) -> dict:
        status, body = await self._request(
            "GET", f"/transactions/{transaction_id}/", access_token=access_token
        )
        if status == 404:
            raise NotFoundError("Transaction not found", status_code=404)
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to get transaction", status_code=status, detail=body
            )
        return body

    async def create_transaction(self, access_token: str, payload: dict) -> dict:
        status, body = await self._request(
            "POST", "/transactions/", access_token=access_token, json_data=payload
        )
        if status == 400:
            raise ValidationError("Validation failed", status_code=400, detail=body)
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to create transaction", status_code=status, detail=body
            )
        return body

    async def update_transaction(
        self, access_token: str, transaction_id: str, payload: dict
    ) -> dict:
        status, body = await self._request(
            "PATCH",
            f"/transactions/{transaction_id}/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 404:
            raise NotFoundError("Transaction not found", status_code=404)
        if status == 400:
            raise ValidationError("Validation failed", status_code=400, detail=body)
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to update transaction", status_code=status, detail=body
            )
        return body

    async def delete_transaction(self, access_token: str, transaction_id: str) -> None:
        status, body = await self._request(
            "DELETE", f"/transactions/{transaction_id}/", access_token=access_token
        )
        if status == 404:
            raise NotFoundError("Transaction not found", status_code=404)
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        # 204 No Content is the expected success status; anything else >= 400 fails.
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to delete transaction", status_code=status, detail=body
            )

    # ------------------------------------------------------------------ #
    # Rentals
    #
    # These stay thin on purpose: they own the HTTP round trip and the status
    # translation, and hand back the parsed body. Turning bodies into domain
    # objects is `src/rentals/infrastructure/drf_client.py`'s job, and the
    # mappers it calls live in the application layer, so no rentals vocabulary
    # leaks into this shared client.
    # ------------------------------------------------------------------ #
    async def list_rentals_houses(self, access_token: str) -> Any:
        status, body = await self._request(
            "GET", "/rentals/houses/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list houses", status_code=status, detail=body
            )
        return body

    async def create_rentals_house(self, access_token: str, payload: dict) -> Any:
        status, body = await self._request(
            "POST",
            "/rentals/houses/",
            access_token=access_token,
            json_data=payload,
        )
        if status in (400, 409):
            raise ValidationError(
                "House validation failed", status_code=status, detail=body
            )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to create house", status_code=status, detail=body
            )
        return body

    async def list_rentals_apartments(self, access_token: str, house_id: str) -> Any:
        status, body = await self._request(
            "GET",
            "/rentals/apartments/",
            access_token=access_token,
            params={"house_id": house_id},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list apartments", status_code=status, detail=body
            )
        return body

    async def create_rentals_apartment(self, access_token: str, payload: dict) -> Any:
        status, body = await self._request(
            "POST",
            "/rentals/apartments/",
            access_token=access_token,
            json_data=payload,
        )
        if status in (400, 409):
            raise ValidationError(
                "Apartment validation failed", status_code=status, detail=body
            )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to create apartment", status_code=status, detail=body
            )
        return body

    async def get_rentals_apartment(self, access_token: str, apartment_id: str) -> Any:
        status, body = await self._request(
            "GET",
            f"/rentals/apartments/{apartment_id}/",
            access_token=access_token,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Apartment not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to fetch apartment", status_code=status, detail=body
            )
        return body

    async def list_rentals_utility_readings(
        self, access_token: str, apartment_id: str
    ) -> Any:
        status, body = await self._request(
            "GET",
            f"/rentals/apartments/{apartment_id}/utilities/",
            access_token=access_token,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Apartment not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list utility readings",
                status_code=status,
                detail=body,
            )
        return body

    async def create_rentals_utility_reading(
        self, access_token: str, apartment_id: str, payload: dict
    ) -> Any:
        status, body = await self._request(
            "POST",
            f"/rentals/apartments/{apartment_id}/utilities/",
            access_token=access_token,
            json_data=payload,
        )
        if status in (400, 409):
            raise ValidationError(
                "Reading validation failed", status_code=status, detail=body
            )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Apartment not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to record utility reading", status_code=status, detail=body
            )
        return body

    async def get_rentals_utility_bill(
        self,
        access_token: str,
        apartment_id: str,
        utility_type: str,
        year: int,
        month: int,
    ) -> Any:
        status, body = await self._request(
            "GET",
            f"/rentals/apartments/{apartment_id}/utilities/bill/",
            access_token=access_token,
            params={"utility_type": utility_type, "year": year, "month": month},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Apartment not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to fetch utility bill", status_code=status, detail=body
            )
        return body

    async def list_rentals_payment_records(
        self, access_token: str, apartment_id: str
    ) -> Any:
        status, body = await self._request(
            "GET",
            f"/rentals/apartments/{apartment_id}/payments/",
            access_token=access_token,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Apartment not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list payments", status_code=status, detail=body
            )
        return body

    async def create_rentals_payment(
        self, access_token: str, apartment_id: str, payload: dict
    ) -> Any:
        status, body = await self._request(
            "POST",
            f"/rentals/apartments/{apartment_id}/payments/",
            access_token=access_token,
            json_data=payload,
        )
        if status in (400, 409):
            raise ValidationError(
                "Payment validation failed", status_code=status, detail=body
            )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Apartment not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to record payment", status_code=status, detail=body
            )
        return body

    async def get_rentals_payment_summary(
        self, access_token: str, apartment_id: str, year: int, month: int
    ) -> Any:
        status, body = await self._request(
            "GET",
            f"/rentals/apartments/{apartment_id}/payments/summary/",
            access_token=access_token,
            params={"year": year, "month": month},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Apartment not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to fetch payment summary", status_code=status, detail=body
            )
        return body

    async def list_rentals_documents(self, access_token: str, apartment_id: str) -> Any:
        status, body = await self._request(
            "GET",
            f"/rentals/apartments/{apartment_id}/documents/",
            access_token=access_token,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Apartment not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list documents", status_code=status, detail=body
            )
        return body

    async def create_rentals_document(
        self, access_token: str, apartment_id: str, payload: dict
    ) -> Any:
        # Note there is no multipart branch and no file parameter anywhere on
        # this route: documents are uploaded straight from the browser to
        # Nextcloud, and only the resulting URL reaches the BFF.
        status, body = await self._request(
            "POST",
            f"/rentals/apartments/{apartment_id}/documents/",
            access_token=access_token,
            json_data=payload,
        )
        if status in (400, 409):
            raise ValidationError(
                "Document validation failed", status_code=status, detail=body
            )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Apartment not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to register document", status_code=status, detail=body
            )
        return body

    async def get_dashboard_overview(self, access_token: str) -> dict:
        status, body = await self._request(
            "GET", "/dashboard/overview/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to get dashboard overview", status_code=status, detail=body
            )
        return body

    async def list_funding_accounts(self, access_token: str) -> dict:
        """List the caller's active funding accounts."""
        status, body = await self._request(
            "GET", "/transactions/accounts/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list funding accounts", status_code=status, detail=body
            )
        return body

    async def create_funding_account(self, access_token: str, payload: dict) -> dict:
        """Create a funding account; answers 201 with the persisted row."""
        status, body = await self._request(
            "POST",
            "/transactions/accounts/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status in (400, 409):
            raise ValidationError(
                "Funding account validation failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to create funding account", status_code=status, detail=body
            )
        return body

    async def get_transaction_analytics(
        self,
        access_token: str,
        start_date: str,
        end_date: str,
        group_by: str,
        period: str,
    ) -> dict:
        """Fetch bounded, chart-ready aggregates.

        Every argument is a plain string: this client is the shared kernel and
        imports nothing from the budget vocabulary, so the adapter formats the
        dates and unwraps the enums before calling in.
        """
        status, body = await self._request(
            "GET",
            "/transactions/analytics/",
            access_token=access_token,
            params={
                "start_date": start_date,
                "end_date": end_date,
                "group_by": group_by,
                "period": period,
            },
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to fetch transaction analytics",
                status_code=status,
                detail=body,
            )
        return body

    async def get_dashboard_summaries(self, access_token: str, period: str) -> list:
        """Fetch summaries for a period.

        The backend wraps results in a list envelope
        (``DashboardListSerializer``: period, start_date, end_date,
        summary_count, is_empty, has_stale_data, summaries). A bare list is also
        accepted so the adapter survives a simpler payload.
        """
        status, body = await self._request(
            "GET", f"/dashboard/{period}/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                f"Failed to get {period} summaries", status_code=status, detail=body
            )
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            summaries = body.get("summaries")
            if isinstance(summaries, list):
                return summaries
        return []

    # ------------------------------------------------------------------ #
    # Tasks, goals, and daily plans
    #
    # One method per endpoint, mirroring the rentals block above. These are the
    # only place a tasks URL is written; the adapter in `src/tasks/infrastructure`
    # never builds a path of its own, so a rename here cannot leave half the
    # context still pointing at the old route.
    # ------------------------------------------------------------------ #
    async def list_tasks(self, access_token: str) -> Any:
        """List the caller's backlog (not-done tasks, backend-ordered)."""
        status, body = await self._request(
            "GET", "/tasks/tasks/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list tasks", status_code=status, detail=body
            )
        return body

    async def list_tasks_by_goal(self, access_token: str, goal_id: str) -> Any:
        """Every task filed under one goal, finished ones included.

        `goal_id` is the **only** filter sent. `TaskFilterQuerySerializer`
        declares `status` and `goal_id` as independent optional query
        parameters, and omitting `status` is what makes this return the goal's
        finished work too -- which is what a goal detail page needs, since its
        progress bar is the share of *all* the goal's hours that are done.

        There is no `/goals/<id>/tasks/` endpoint; this is the only way to ask.
        """
        status, body = await self._request(
            "GET",
            "/tasks/tasks/",
            access_token=access_token,
            params={"goal_id": goal_id},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 400:
            raise ValidationError(
                f"Invalid goal filter: {goal_id!r}", status_code=400, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list tasks for goal", status_code=status, detail=body
            )
        return body

    async def list_tasks_by_status(self, access_token: str, status_filter: str) -> Any:
        """List the caller's tasks narrowed to one status.

        `status_filter` is sent verbatim and must be one of the backend's
        `TASK_STATUS_CHOICES`: **``todo`` / ``doing`` / ``done``**. The obvious
        guesses are wrong in both directions -- there is no ``completed`` and no
        ``pending``, because those are the words the *frontend* uses for the two
        states the backend calls ``done`` and ``todo``. An unrecognised value is
        a 400 ("Unknown task status."), which is why the caller passes an enum
        member rather than a hand-written string.
        """
        status, body = await self._request(
            "GET",
            "/tasks/tasks/",
            access_token=access_token,
            params={"status": status_filter},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 400:
            raise ValidationError(
                f"Unknown task status filter: {status_filter!r}",
                status_code=400,
                detail=body,
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list tasks by status", status_code=status, detail=body
            )
        return body

    async def update_task(self, access_token: str, task_id: str, payload: dict) -> Any:
        """Apply a partial update and return the reloaded task.

        PATCH rather than PUT, and the distinction is the whole point of the
        method: `UpdateTaskSerializer` is used with `partial=True`, so an absent
        key leaves the stored value alone and only the keys the caller sent are
        written. A PUT would mean every omitted field had been cleared.
        """
        status, body = await self._request(
            "PATCH",
            f"/tasks/tasks/{task_id}/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Task not found", status_code=404)
        if status in (400, 409):
            raise ValidationError("Task update failed", status_code=status, detail=body)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to update task", status_code=status, detail=body
            )
        return body

    async def delete_task(self, access_token: str, task_id: str) -> None:
        """Delete one task. Returns `None`; the backend answers `204`.

        A 404 is raised rather than swallowed, deliberately. The caller has to
        be able to say "that task no longer exists" as distinct from "that task
        was deleted" -- see `DeleteTaskUseCase`.
        """
        status, body = await self._request(
            "DELETE", f"/tasks/tasks/{task_id}/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Task not found", status_code=404)
        if status in (400, 409):
            raise ValidationError("Task delete failed", status_code=status, detail=body)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to delete task", status_code=status, detail=body
            )
        return None

    async def get_task(self, access_token: str, task_id: str) -> Any:
        status, body = await self._request(
            "GET", f"/tasks/tasks/{task_id}/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Task not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to fetch task", status_code=status, detail=body
            )
        return body

    async def create_task(self, access_token: str, payload: dict) -> Any:
        print(f"payload for create task - {payload}")
        logger.info(f"payload for create task - {payload}")
        status, body = await self._request(
            "POST", "/tasks/tasks/", access_token=access_token, json_data=payload
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status in (400, 409):
            raise ValidationError(
                "Task validation failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to create task", status_code=status, detail=body
            )
        return body

    async def list_goals(self, access_token: str) -> Any:
        status, body = await self._request(
            "GET", "/tasks/goals/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list goals", status_code=status, detail=body
            )
        return body

    async def get_goal(self, access_token: str, goal_id: str) -> Any:
        """Read one goal.

        A 404 is raised rather than swallowed: the goal may be missing, or it may
        belong to somebody else, and the caller has to be able to say "that goal
        no longer exists" instead of rendering an empty page under a heading the
        user never had.
        """
        status, body = await self._request(
            "GET", f"/tasks/goals/{goal_id}/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Goal not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to fetch goal", status_code=status, detail=body
            )
        return body

    async def list_daily_plans(self, access_token: str) -> Any:
        """Every daily plan the caller has, newest first.

        The date is a query parameter on the same endpoint rather than a path
        segment, so the list is fetched whole and filtered client-side. That
        looks wasteful for one day and is not: the daily view also wants the
        previous and next day for the date stepper, and three filtered round
        trips to render one page is worse than one unfiltered one.
        """
        status, body = await self._request(
            "GET", "/tasks/daily-plans/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list daily plans", status_code=status, detail=body
            )
        return body

    async def create_daily_plan(self, access_token: str, plan_date: str) -> Any:
        """Open the plan for one day, or return the one that is already open.

        `CreateDailyPlanSerializer` requires `date`, and the view behind it is
        `GetOrCreateDailyPlan` -- so this answers 201 with the day's plan whether
        it created it or found it, and the caller cannot tell the two apart. That
        is deliberate on the backend's part and it is why this method is not named
        `create_...` in any stronger sense than that.

        The ISO string is passed in rather than a `date` because this client is
        the shared kernel and imports nothing from any bounded context's
        vocabulary (AGENTS.md, constraint 1); the caller formats it.
        """
        status, body = await self._request(
            "POST",
            "/tasks/daily-plans/",
            access_token=access_token,
            json_data={"date": plan_date},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Daily plan not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to open the daily plan", status_code=status, detail=body
            )
        return body

    async def generate_daily_plan(self, access_token: str, plan_date: str) -> Any:
        """Ask the assistant to plan one day. Answers 202; the work is queued.

        ``date`` is optional in ``GenerateDailyPlanSerializer`` and this method
        always sends it, so the day the backend plans is the day the button was
        rendered for and never "whatever day the request happened to land on".

        **202, not 200, and never a plan.** The view hands the work to Celery and
        returns; the plan is written by a worker afterwards. There is also no 409
        for a day that is already planned -- re-planning one is allowed, and the
        BFF decides not to offer the button rather than relying on the endpoint to
        refuse.

        A 503 is raised like any other failure here; the adapter decides what it
        means, and the tasks adapter maps it to `LlmUnavailableError`.
        """
        status, body = await self._request(
            "POST",
            "/tasks/daily-plans/generate/",
            access_token=access_token,
            json_data={"date": plan_date},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to queue daily plan generation", status_code=status, detail=body
            )
        return body

    async def add_task_to_daily_plan(
        self, access_token: str, plan_id: str, task_id: str
    ) -> Any:
        """Schedule one task onto the day named by ``plan_id``.

        The body is the single key `DailyPlanTaskSerializer` declares,
        ``{"task_id": ...}``; there is no ordering or estimate to send, because
        the backend reads all of that from the task row.

        **A 409 is not treated as a failure of the request.** The backend answers
        it for three real outcomes -- the task is already scheduled, the day is
        full, the task is already done -- each with its own `detail`, and each one
        is something the user needs to be told rather than a bug. The status is
        therefore preserved on the raised error (with the body as `detail`) so
        the adapter can recognise it and the view can show the backend's own
        wording.
        """
        status, body = await self._request(
            "POST",
            f"/tasks/daily-plans/{plan_id}/add-task/",
            access_token=access_token,
            json_data={"task_id": task_id},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError(
                "Daily plan or task not found", status_code=404, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to schedule the task on the daily plan",
                status_code=status,
                detail=body,
            )
        return body

    async def mark_task_doing(self, access_token: str, task_id: str) -> Any:
        """Move a task to ``doing``; returns the reloaded `TaskSerializer`.

        The 409 is *not* a generic client error here. It is `TaskAlreadyDone`,
        a business outcome with its own meaning, and the status is preserved on
        the raised error so the adapter can recognise it and the board can tell
        the user what actually happened instead of "request failed".

        Deliberately a named method rather than `set_task_status(status)`: the
        three transitions are three distinct backend endpoints with three
        distinct rules, and a generic setter would hide that behind a parameter
        that looks interchangeable when it is not.
        """
        status, body = await self._request(
            "POST",
            f"/tasks/tasks/{task_id}/mark-doing/",
            access_token=access_token,
            json_data={},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Task not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Cannot move this task to doing", status_code=status, detail=body
            )
        return body

    async def mark_task_done(self, access_token: str, task_id: str) -> Any:
        """Move a task to ``done``; returns the reloaded `TaskSerializer`.

        Same shape as :meth:`mark_task_doing`, one status apart: 200 with the
        task, 409 `TaskAlreadyDone` when it is already finished.
        """
        status, body = await self._request(
            "POST",
            f"/tasks/tasks/{task_id}/mark-done/",
            access_token=access_token,
            json_data={},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Task not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Cannot complete this task", status_code=status, detail=body
            )
        return body

    async def reopen_task(self, access_token: str, task_id: str) -> Any:
        """Return a finished task to ``todo``; returns the reloaded task.

        The only route out of `done`. Note the 409 it raises for a task that was
        *not* finished (`TaskNotCompleted`) -- that is the backend refusing to
        use reopen as a general "set to todo", and it is why the board has no
        "doing -> todo" arrow rather than a broken one.
        """
        status, body = await self._request(
            "POST",
            f"/tasks/tasks/{task_id}/reopen/",
            access_token=access_token,
            json_data={},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Task not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Cannot reopen this task", status_code=status, detail=body
            )
        return body

    async def get_llm_health(self, access_token: str) -> Any:
        """Report whether the LLM provider behind the backend is reachable.

        Deliberately separate from the two trigger endpoints rather than derived
        from their failures: the navbar indicator polls this on every page load,
        including pages where the user has never touched an LLM feature, so it
        has to be a cheap question with a cheap answer.

        Note the path is a *literal* segment under `/tasks/tasks/`. DRF resolves
        it before any `<uuid:pk>` converter on a sibling detail route, so
        `llm-health` cannot be captured as a task id -- but only because it is
        matched first. A task detail route written to match any string would
        swallow it.
        """
        status, body = await self._request(
            "GET", "/tasks/tasks/llm-health/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            # 503 included: a health probe that "fails" is the normal way this
            # endpoint reports an unreachable provider, so it must not be an
            # exception the caller has to special-case.
            raise DRFAPIClientError(
                "LLM health check failed", status_code=status, detail=body
            )
        return body

    async def evaluate_task(self, access_token: str, task_id: str) -> Any:
        """Queue the LLM evaluation for one task.

        Returns ``None`` for the backend's empty 202 body, which is the honest
        reading: the work has been queued and there is no result to hand back.
        """
        status, body = await self._request(
            "POST",
            f"/tasks/tasks/{task_id}/evaluate/",
            access_token=access_token,
            json_data={},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Task not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Cannot evaluate this task", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to trigger task evaluation", status_code=status, detail=body
            )
        return body

    async def decompose_task(self, access_token: str, task_id: str) -> Any:
        """Queue the LLM decomposition of one task into subtasks."""
        status, body = await self._request(
            "POST",
            f"/tasks/tasks/{task_id}/decompose/",
            access_token=access_token,
            json_data={},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Task not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Cannot decompose this task", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to trigger task decomposition", status_code=status, detail=body
            )
        return body

    # ------------------------------------------------------------------ #
    # Purchases: shopping lists, wishlist, and the shared tag catalogue
    #
    # One method per endpoint, mirroring the blocks above. Every purchases URL is
    # written exactly once, here, so a backend rename cannot leave half the
    # context pointing at the old route while the other half has moved.
    #
    # Two response shapes are easy to get wrong and are commented where they
    # occur, because both look like a 200-with-a-body and neither is what the
    # name suggests:
    #
    # * a soft delete answers **200 with the list**, not 204 -- `is_active`
    #   flips to false and the rows survive.
    # * mark-item-purchased answers **200 with the whole list** (or the whole
    #   wishlist), not with the item. That response is also the only place the
    #   aggregate's recomputed `status` and `completed_at` appear.
    # ------------------------------------------------------------------ #
    async def list_shopping_lists(self, access_token: str) -> Any:
        """The caller's **active** shopping lists.

        `GET /shopping-lists/` is filtered to `is_active=true` server-side; a
        soft-deleted list is simply absent. There is no query parameter that
        widens it, which is why the archived set is a separate method.
        """
        status, body = await self._request(
            "GET", "/purchases/shopping-lists/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list shopping lists", status_code=status, detail=body
            )
        return body

    async def list_inactive_shopping_lists(self, access_token: str) -> Any:
        """The caller's soft-deleted lists, which they can restore."""
        status, body = await self._request(
            "GET", "/purchases/shopping-lists/inactive/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list archived shopping lists",
                status_code=status,
                detail=body,
            )
        return body

    async def get_shopping_list(self, access_token: str, list_id: str) -> Any:
        """One list with its items nested.

        A 404 means "missing" *or* "not yours" -- every purchases route is
        owner-scoped that way -- and the two are deliberately not separable here.
        """
        status, body = await self._request(
            "GET", f"/purchases/shopping-lists/{list_id}/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to fetch shopping list", status_code=status, detail=body
            )
        return body

    async def create_shopping_list(self, access_token: str, payload: dict) -> Any:
        """Create a list. `priority` is optional; the API defaults it to medium."""
        status, body = await self._request(
            "POST",
            "/purchases/shopping-lists/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status in (400, 409):
            raise ValidationError(
                "Shopping list validation failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to create shopping list", status_code=status, detail=body
            )
        return body

    async def update_shopping_list(
        self, access_token: str, list_id: str, payload: dict
    ) -> Any:
        """PATCH the list's name or priority. An empty body is a 400."""
        status, body = await self._request(
            "PATCH",
            f"/purchases/shopping-lists/{list_id}/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Shopping list update failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to update shopping list", status_code=status, detail=body
            )
        return body

    async def delete_shopping_list(self, access_token: str, list_id: str) -> Any:
        """Soft-delete a list. Answers **200 with the list**, not 204.

        A soft delete: `is_active` flips to false, the rows and their items
        survive, and `restore_shopping_list` brings them back. The body is
        returned rather than discarded because it is the archived list the
        overview renders after the redirect.
        """
        status, body = await self._request(
            "DELETE", f"/purchases/shopping-lists/{list_id}/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to archive shopping list", status_code=status, detail=body
            )
        return body

    async def restore_shopping_list(self, access_token: str, list_id: str) -> Any:
        """Bring an archived list back, with its items intact."""
        status, body = await self._request(
            "POST",
            f"/purchases/shopping-lists/{list_id}/restore/",
            access_token=access_token,
            json_data={},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to restore shopping list", status_code=status, detail=body
            )
        return body

    async def add_shopping_list_item(
        self, access_token: str, list_id: str, payload: dict
    ) -> Any:
        """Add an item. Answers **201 with the whole list**, items nested.

        The body is not the new item -- it is the reloaded aggregate, and it is
        the only place the new item's id appears. `quantity` must be >= 1 and
        `details` is optional.
        """
        status, body = await self._request(
            "POST",
            f"/purchases/shopping-lists/{list_id}/add-item/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Shopping list item validation failed",
                status_code=status,
                detail=body,
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to add item to shopping list", status_code=status, detail=body
            )
        return body

    async def update_shopping_list_item(
        self, access_token: str, list_id: str, item_id: str, payload: dict
    ) -> Any:
        """PATCH one item. Answers 200 with the **item**, not the list.

        The one route in this block that does not return the aggregate: the
        backend's `ShoppingListItemDetailView` serialises `ShoppingListItemSerializer`.
        """
        status, body = await self._request(
            "PATCH",
            f"/purchases/shopping-lists/{list_id}/items/{item_id}/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list item not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Shopping list item update failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to update shopping list item", status_code=status, detail=body
            )
        return body

    async def delete_shopping_list_item(
        self, access_token: str, list_id: str, item_id: str
    ) -> Any:
        """Remove an item. Answers 200 with the reloaded **list**, not 204."""
        status, body = await self._request(
            "DELETE",
            f"/purchases/shopping-lists/{list_id}/items/{item_id}/",
            access_token=access_token,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list item not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to delete shopping list item", status_code=status, detail=body
            )
        return body

    async def mark_shopping_list_item_purchased(
        self, access_token: str, list_id: str, item_id: str
    ) -> Any:
        """Tick an item off. Answers **200 with the whole reloaded list**.

        The same response carries the aggregate's recomputed `status` and
        `completed_at`, so the caller learns in one round trip whether that tick
        completed the list.
        """
        status, body = await self._request(
            "POST",
            f"/purchases/shopping-lists/{list_id}/items/{item_id}/mark-purchased/",
            access_token=access_token,
            json_data={},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list item not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to mark shopping list item as purchased",
                status_code=status,
                detail=body,
            )
        return body

    async def add_shopping_list_item_tags(
        self, access_token: str, list_id: str, item_id: str, tag_names: list[str]
    ) -> Any:
        """Attach tags by name. Answers 200 with the **item**.

        The backend's `AddTagsToItem` creates any tag that does not exist yet,
        which is why this takes names and not ids.
        """
        status, body = await self._request(
            "POST",
            f"/purchases/shopping-lists/{list_id}/items/{item_id}/tags/",
            access_token=access_token,
            json_data={"tag_names": tag_names},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list item not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Tagging the item failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to tag shopping list item", status_code=status, detail=body
            )
        return body

    async def remove_shopping_list_item_tags(
        self, access_token: str, list_id: str, item_id: str, tag_ids: list[str]
    ) -> Any:
        """Detach tags by id. Answers 200 with the item.

        `DELETE` with a body, which is what `RemoveTagsSerializer` expects. By id
        rather than by name: attaching creates catalogue entries on demand, so a
        name is not a stable handle for an existing attachment the way an id is.
        """
        status, body = await self._request(
            "DELETE",
            f"/purchases/shopping-lists/{list_id}/items/{item_id}/tags/",
            access_token=access_token,
            json_data={"tag_ids": tag_ids},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Shopping list item not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Removing tags failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to remove tags from shopping list item",
                status_code=status,
                detail=body,
            )
        return body

    async def get_wishlist(self, access_token: str) -> Any:
        """The caller's wishlist, created on first access.

        `GET /wishlist/` is routed to the backend's `GetOrCreateWishlist` and
        answers **200 either way** -- it cannot be distinguished from a create by
        status code, which is why this is not named `create_...`.
        """
        status, body = await self._request(
            "GET", "/purchases/wishlist/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to fetch wishlist", status_code=status, detail=body
            )
        return body

    async def update_wishlist(self, access_token: str, payload: dict) -> Any:
        """Rename the wishlist. Not addressed by uuid -- it is the caller's own."""
        status, body = await self._request(
            "PATCH",
            "/purchases/wishlist/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status in (400, 409):
            raise ValidationError(
                "Wishlist update failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to update wishlist", status_code=status, detail=body
            )
        return body

    async def add_wishlist_item(self, access_token: str, payload: dict) -> Any:
        """Add a wanted item. Answers **201 with the whole wishlist**.

        `estimated_price` is sent as a **string** because `AddWishlistItemSerializer`
        declares a `DecimalField`; the API's own responses render it the same way.
        An absent price is *omitted* rather than sent as `null`: the field is
        `required=False`, and omitting it is what "no estimate" means on create.
        """
        status, body = await self._request(
            "POST",
            "/purchases/wishlist/add-item/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status in (400, 409):
            raise ValidationError(
                "Wishlist item validation failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to add item to wishlist", status_code=status, detail=body
            )
        return body

    async def update_wishlist_item(
        self, access_token: str, item_id: str, payload: dict
    ) -> Any:
        """PATCH one wishlist item. Answers 200 with the item, not the wishlist."""
        status, body = await self._request(
            "PATCH",
            f"/purchases/wishlist/items/{item_id}/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Wishlist item not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Wishlist item update failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to update wishlist item", status_code=status, detail=body
            )
        return body

    async def delete_wishlist_item(self, access_token: str, item_id: str) -> Any:
        """Remove a wishlist item. Answers 200 with the reloaded wishlist."""
        status, body = await self._request(
            "DELETE",
            f"/purchases/wishlist/items/{item_id}/",
            access_token=access_token,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Wishlist item not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to delete wishlist item", status_code=status, detail=body
            )
        return body

    async def mark_wishlist_item_purchased(
        self, access_token: str, item_id: str
    ) -> Any:
        """Tick a wishlist item off. Answers **200 with the whole wishlist**."""
        status, body = await self._request(
            "POST",
            f"/purchases/wishlist/items/{item_id}/mark-purchased/",
            access_token=access_token,
            json_data={},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Wishlist item not found", status_code=404)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to mark wishlist item as purchased",
                status_code=status,
                detail=body,
            )
        return body

    async def add_wishlist_item_tags(
        self, access_token: str, item_id: str, tag_names: list[str]
    ) -> Any:
        """Attach tags by name to a wishlist item. Answers 200 with the item."""
        status, body = await self._request(
            "POST",
            f"/purchases/wishlist/items/{item_id}/tags/",
            access_token=access_token,
            json_data={"tag_names": tag_names},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Wishlist item not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Tagging the item failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to tag wishlist item", status_code=status, detail=body
            )
        return body

    async def remove_wishlist_item_tags(
        self, access_token: str, item_id: str, tag_ids: list[str]
    ) -> Any:
        """Detach tags by id from a wishlist item. Answers 200 with the item."""
        status, body = await self._request(
            "DELETE",
            f"/purchases/wishlist/items/{item_id}/tags/",
            access_token=access_token,
            json_data={"tag_ids": tag_ids},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status == 404:
            raise NotFoundError("Wishlist item not found", status_code=404)
        if status in (400, 409):
            raise ValidationError(
                "Removing tags failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to remove tags from wishlist item",
                status_code=status,
                detail=body,
            )
        return body

    async def list_purchases_tags(self, access_token: str) -> Any:
        """The shared tag catalogue.

        Global, not owner-scoped: `Tag` has no owner column and the backend's
        `TagListView` takes no user.
        """
        status, body = await self._request(
            "GET", "/purchases/tags/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to list tags", status_code=status, detail=body
            )
        return body

    async def create_purchases_tag(self, access_token: str, name: str) -> Any:
        """Add a tag to the catalogue. **201 for a new tag, 200 for an existing one.**

        The backend's `CreateTag` is idempotent by name, and the status code is
        the only place that difference is visible -- so it is preserved here
        rather than collapsed, and the adapter does not pretend it can tell them
        apart.
        """
        status, body = await self._request(
            "POST",
            "/purchases/tags/",
            access_token=access_token,
            json_data={"name": name},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status in (400, 409):
            raise ValidationError(
                "Tag validation failed", status_code=status, detail=body
            )
        if status >= 400:
            raise DRFAPIClientError(
                "Failed to create tag", status_code=status, detail=body
            )
        return body
