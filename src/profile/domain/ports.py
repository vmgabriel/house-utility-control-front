"""Profile repository ports."""

from typing import Protocol

from src.profile.domain.entities import UserProfile


class ProfileRepositoryPort(Protocol):
    async def get_profile(self, access_token: str) -> UserProfile: ...
    async def update_profile(
        self, access_token: str, profile: UserProfile
    ) -> UserProfile: ...
    async def update_preferences(
        self, access_token: str, language: str, currency: str, date_format: str
    ) -> UserProfile: ...
