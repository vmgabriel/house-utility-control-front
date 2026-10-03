"""User management use cases."""

from dataclasses import dataclass

from src.users.domain.entities import SystemUser, UserPlan
from src.users.domain.ports import UserRepositoryPort


@dataclass
class RegisterUserUseCase:
    repository: UserRepositoryPort

    async def execute(self, email: str, full_name: str, password: str) -> SystemUser:
        # Basic validation: password length, email format could go here
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        return await self.repository.register(email, full_name, password)


@dataclass
class ListUsersUseCase:
    repository: UserRepositoryPort

    async def execute(
        self, access_token: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, list[SystemUser]]:
        return await self.repository.list_users(access_token, page, page_size)


@dataclass
class UpdateUserPlanUseCase:
    repository: UserRepositoryPort

    async def execute(
        self, access_token: str, user_id: str, plan: UserPlan
    ) -> SystemUser:
        return await self.repository.update_user_plan(access_token, user_id, plan.value)


@dataclass
class ToggleUserActiveUseCase:
    repository: UserRepositoryPort

    async def execute(
        self,
        access_token: str,
        user_id: str,
        is_active: bool,
        ban_reason: str | None = None,
    ) -> SystemUser:
        return await self.repository.update_user_status(
            access_token, user_id, is_active, ban_reason
        )
