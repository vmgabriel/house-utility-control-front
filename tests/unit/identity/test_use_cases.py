"""Tests for the identity context's authentication use cases."""

from unittest.mock import AsyncMock

import pytest

from src.identity.application.use_cases import (
    AuthenticateUserUseCase,
    LogoutUserUseCase,
    RefreshTokenUseCase,
)
from src.identity.domain.entities import User
from src.identity.domain.value_objects import UserId


class TestAuthenticateUserUseCase:
    @pytest.mark.asyncio
    async def test_authenticate_user_success(self):
        mock_auth_repo = AsyncMock()
        mock_auth_repo.login.return_value = ("access-token", "refresh-token")
        mock_auth_repo.get_current_user.return_value = User(
            id=UserId("user-123"),
            email="test@example.com",
            name="Test User",
            plan="free",
        )

        use_case = AuthenticateUserUseCase(auth_repository=mock_auth_repo)
        access_token, refresh_token, user = await use_case.execute(
            "test@example.com", "password123"
        )

        assert access_token == "access-token"
        assert refresh_token == "refresh-token"
        assert user.email == "test@example.com"
        mock_auth_repo.login.assert_called_once_with("test@example.com", "password123")


class TestRefreshTokenUseCase:
    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        mock_auth_repo = AsyncMock()
        mock_auth_repo.refresh_token.return_value = ("new-access", "new-refresh")

        use_case = RefreshTokenUseCase(auth_repository=mock_auth_repo)
        new_access, new_refresh = await use_case.execute("old-refresh")

        assert new_access == "new-access"
        assert new_refresh == "new-refresh"


class TestLogoutUserUseCase:
    @pytest.mark.asyncio
    async def test_logout_user_success(self):
        mock_auth_repo = AsyncMock()

        use_case = LogoutUserUseCase(auth_repository=mock_auth_repo)
        await use_case.execute("access-token")

        mock_auth_repo.logout.assert_called_once_with("access-token")
