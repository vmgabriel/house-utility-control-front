"""User management use cases."""

from dataclasses import dataclass

from src.users.domain.entities import SystemUser, UserPlan

__all__ = [
    "RegisterUserUseCase",
    "ListUsersUseCase",
    "UpdateUserPlanUseCase",
    "ToggleUserActiveUseCase",
    "GetAdminStatsUseCase",
]
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


@dataclass
class GetAdminStatsUseCase:
    """Headline counts for the admin landing page.

    Derived from the user list rather than a dedicated aggregate endpoint, so the
    numbers are only as fresh as that list and are capped by ``page_size``. The
    backend exposes no stats route, and adding one is a backend change, not a
    frontend one -- so this trades exactness for not inventing an endpoint.

    On a large deployment the totals silently under-report rather than failing,
    which is why ``page_size`` is deliberately far above the admin list's 20. A
    true fix is ``GET /users/stats/`` on the DRF side.
    """

    repository: UserRepositoryPort

    #: Upper bound on the users pulled for the counts. Well above any realistic
    #: admin dataset, and a single request either way.
    PAGE_SIZE = 10_000

    async def execute(self, access_token: str) -> dict:
        _total, users = await self.repository.list_users(
            access_token, page=1, page_size=self.PAGE_SIZE
        )

        return {
            "total_users": len(users),
            "active_users": sum(1 for u in users if u.is_active),
            "banned_users": sum(1 for u in users if not u.is_active),
            "by_plan": {
                plan.value: sum(1 for u in users if u.plan is plan) for plan in UserPlan
            },
        }
