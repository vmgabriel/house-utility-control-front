"""DRF User Repository Implementation."""

from src.shared.http.drf_client import DRFAPIClient, UnauthorizedError
from src.users.domain.entities import SystemUser
from src.users.domain.ports import UserRepositoryPort  # noqa: F401
from src.users.infrastructure.schemas import DRFUserListResponse, DRFUserResponse


class DRFUserRepository:
    def __init__(self, api_client: DRFAPIClient):
        self.api_client = api_client

    async def register(self, email: str, full_name: str, password: str) -> SystemUser:
        status, body = await self.api_client._request(
            "POST",
            "/users/auth/register/",
            json_data={"email": email, "full_name": full_name, "password": password},
        )
        if status >= 400:
            raise Exception(f"Registration failed: {body}")
        return self._map_to_entity(DRFUserResponse.model_validate(body))

    async def list_users(
        self, access_token: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, list[SystemUser]]:
        status, body = await self.api_client._request(
            "GET",
            "/users/",
            access_token=access_token,
            params={"page": page, "page_size": page_size},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise Exception("Failed to list users")
        data = DRFUserListResponse.model_validate(body)
        return data.count, [self._map_to_entity(u) for u in data.results]

    async def update_user_status(
        self,
        access_token: str,
        user_id: str,
        is_active: bool,
        ban_reason: str | None = None,
    ) -> SystemUser:
        payload: dict = {"is_active": is_active}
        if ban_reason is not None:
            payload["ban_reason"] = ban_reason

        status, body = await self.api_client._request(
            "PATCH",
            f"/users/{user_id}/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise Exception("Failed to update user status")
        return self._map_to_entity(DRFUserResponse.model_validate(body))

    async def update_user_plan(
        self, access_token: str, user_id: str, plan: str
    ) -> SystemUser:
        status, body = await self.api_client._request(
            "PATCH",
            f"/users/{user_id}/plan/",
            access_token=access_token,
            json_data={"plan": plan},
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise Exception("Failed to update user plan")
        return self._map_to_entity(DRFUserResponse.model_validate(body))

    def _map_to_entity(self, data: DRFUserResponse) -> SystemUser:
        return SystemUser(
            id=data.id,
            email=data.email,
            full_name=data.full_name,
            plan=data.plan,
            is_active=data.is_active,
            is_staff=data.is_staff,
        )
