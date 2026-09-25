"""Async HTTP client for the DRF backend."""

from typing import Any

import httpx

from src.domain.exceptions import AuthenticationError, DomainException


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
        """Execute an HTTP request and return (status_code, parsed_json_or_None)."""
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method,
                url,
                headers=self._build_headers(access_token),
                json=json_data,
                params=params,
            )

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
