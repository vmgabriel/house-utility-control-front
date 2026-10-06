"""Async HTTP client for the DRF backend."""

from typing import Any

import httpx

from src.shared.domain.exceptions import AuthenticationError, DomainException


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
