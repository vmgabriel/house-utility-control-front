"""Unit tests for the user management use cases."""

from unittest.mock import AsyncMock

import pytest

from src.users.application.use_cases import (
    ListUsersUseCase,
    RegisterUserUseCase,
    ToggleUserActiveUseCase,
    UpdateUserPlanUseCase,
)
from src.users.domain.entities import SystemUser, UserPlan

USER = SystemUser(
    id="user-1",
    email="a@example.com",
    full_name="Ana",
    plan=UserPlan.FREE,
    is_active=True,
    is_staff=False,
)


class TestRegisterUserUseCase:
    @pytest.mark.asyncio
    async def test_register_delegates_to_repository(self):
        repo = AsyncMock()
        repo.register.return_value = USER
        uc = RegisterUserUseCase(repository=repo)
        result = await uc.execute("a@example.com", "Ana", "password123")
        assert result is USER
        repo.register.assert_called_once_with("a@example.com", "Ana", "password123")

    @pytest.mark.asyncio
    async def test_register_rejects_short_password(self):
        repo = AsyncMock()
        uc = RegisterUserUseCase(repository=repo)
        with pytest.raises(ValueError):
            await uc.execute("a@example.com", "Ana", "short")
        repo.register.assert_not_called()


class TestListUsersUseCase:
    @pytest.mark.asyncio
    async def test_list_delegates_to_repository(self):
        repo = AsyncMock()
        repo.list_users.return_value = (1, [USER])
        uc = ListUsersUseCase(repository=repo)
        count, users = await uc.execute("token", page=2, page_size=10)
        assert count == 1
        assert users == [USER]
        repo.list_users.assert_called_once_with("token", 2, 10)


class TestUpdateUserPlanUseCase:
    @pytest.mark.asyncio
    async def test_update_plan_passes_plan_value(self):
        repo = AsyncMock()
        repo.update_user_plan.return_value = USER
        uc = UpdateUserPlanUseCase(repository=repo)
        await uc.execute("token", "user-1", UserPlan.PREMIUM)
        repo.update_user_plan.assert_called_once_with("token", "user-1", "premium")


class TestToggleUserActiveUseCase:
    @pytest.mark.asyncio
    async def test_toggle_delegates_to_repository(self):
        repo = AsyncMock()
        repo.update_user_status.return_value = USER
        uc = ToggleUserActiveUseCase(repository=repo)
        await uc.execute("token", "user-1", False)
        repo.update_user_status.assert_called_once_with("token", "user-1", False)
