"""Unit tests for the profile use cases."""

from unittest.mock import AsyncMock

import pytest

from src.profile.application.use_cases import (
    GetProfileUseCase,
    UpdatePreferencesUseCase,
    UpdateProfileUseCase,
)
from src.profile.domain.entities import UserProfile

PROFILE = UserProfile(
    id="user-123",
    first_name="Test",
    last_name="User",
    timezone="UTC",
    language="es",
    currency="USD",
    date_format="YYYY-MM-DD",
    avatar_url=None,
    bio=None,
)


class TestGetProfileUseCase:
    @pytest.mark.asyncio
    async def test_get_profile_delegates_to_repository(self):
        repo = AsyncMock()
        repo.get_profile.return_value = PROFILE
        uc = GetProfileUseCase(repository=repo)
        result = await uc.execute("token")
        assert result is PROFILE
        repo.get_profile.assert_called_once_with("token")


class TestUpdateProfileUseCase:
    @pytest.mark.asyncio
    async def test_update_profile_delegates_to_repository(self):
        repo = AsyncMock()
        repo.update_profile.return_value = PROFILE
        uc = UpdateProfileUseCase(repository=repo)
        result = await uc.execute("token", PROFILE)
        assert result is PROFILE
        repo.update_profile.assert_called_once_with("token", PROFILE)

    @pytest.mark.asyncio
    async def test_update_profile_rejects_oversized_bio(self):
        repo = AsyncMock()
        uc = UpdateProfileUseCase(repository=repo)
        from dataclasses import replace

        too_long = replace(PROFILE, bio="x" * 501)
        with pytest.raises(ValueError):
            await uc.execute("token", too_long)
        repo.update_profile.assert_not_called()


class TestUpdatePreferencesUseCase:
    @pytest.mark.asyncio
    async def test_update_preferences_delegates_to_repository(self):
        repo = AsyncMock()
        repo.update_preferences.return_value = PROFILE
        uc = UpdatePreferencesUseCase(repository=repo)
        result = await uc.execute("token", "en", "EUR", "DD/MM/YYYY")
        assert result is PROFILE
        repo.update_preferences.assert_called_once_with(
            "token", "en", "EUR", "DD/MM/YYYY"
        )
