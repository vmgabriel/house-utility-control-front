"""Profile use cases."""

from dataclasses import dataclass

from src.profile.domain.entities import UserProfile
from src.profile.domain.ports import ProfileRepositoryPort


@dataclass
class GetProfileUseCase:
    repository: ProfileRepositoryPort

    async def execute(self, access_token: str) -> UserProfile:
        return await self.repository.get_profile(access_token)


@dataclass
class UpdateProfileUseCase:
    repository: ProfileRepositoryPort

    async def execute(self, access_token: str, profile: UserProfile) -> UserProfile:
        # Basic domain validation could go here (e.g., bio length <= 500)
        if profile.bio is not None and len(profile.bio) > 500:
            raise ValueError("Bio must be at most 500 characters")
        return await self.repository.update_profile(access_token, profile)


@dataclass
class UpdatePreferencesUseCase:
    repository: ProfileRepositoryPort

    async def execute(
        self, access_token: str, language: str, currency: str, date_format: str
    ) -> UserProfile:
        return await self.repository.update_preferences(
            access_token, language, currency, date_format
        )
