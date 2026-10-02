"""User repository ports."""

from typing import Protocol

from src.users.domain.entities import SystemUser


class UserRepositoryPort(Protocol):
    async def register(
        self, email: str, full_name: str, password: str
    ) -> SystemUser: ...

    async def list_users(
        self, access_token: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, list[SystemUser]]: ...

    async def update_user_status(
        self, access_token: str, user_id: str, is_active: bool
    ) -> SystemUser: ...

    async def update_user_plan(
        self, access_token: str, user_id: str, plan: str
    ) -> SystemUser: ...
