"""DRF Profile Repository Implementation."""

from src.infrastructure.api.drf_client import DRFAPIClient, UnauthorizedError
from src.profile.domain.entities import UserProfile
from src.profile.domain.ports import ProfileRepositoryPort  # noqa: F401
from src.profile.infrastructure.schemas import DRFProfileResponse


def _to_domain(data: DRFProfileResponse) -> UserProfile:
    return UserProfile(
        id=data.id,
        first_name=data.first_name,
        last_name=data.last_name,
        timezone=data.timezone,
        language=data.language,
        currency=data.currency,
        date_format=data.date_format,
        avatar_url=data.avatar_url,
        bio=data.bio,
    )


class DRFProfileRepository:
    def __init__(self, api_client: DRFAPIClient):
        self.api_client = api_client

    async def get_profile(self, access_token: str) -> UserProfile:
        status, body = await self.api_client._request(
            "GET", "/profile/me/", access_token=access_token
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise Exception("Failed to fetch profile")
        return _to_domain(DRFProfileResponse.model_validate(body))

    async def update_profile(
        self, access_token: str, profile: UserProfile
    ) -> UserProfile:
        payload = {
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "timezone": profile.timezone,
            "avatar_url": profile.avatar_url,
            "bio": profile.bio,
        }
        # Filter out None values for partial update, but keep "" for clearing
        payload = {k: v for k, v in payload.items() if v is not None}

        status, body = await self.api_client._request(
            "PATCH", "/profile/me/", access_token=access_token, json_data=payload
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise Exception("Failed to update profile")
        return _to_domain(DRFProfileResponse.model_validate(body))

    async def update_preferences(
        self, access_token: str, language: str, currency: str, date_format: str
    ) -> UserProfile:
        payload = {
            "language": language,
            "currency": currency,
            "date_format": date_format,
        }
        status, body = await self.api_client._request(
            "PATCH",
            "/profile/me/preferences/",
            access_token=access_token,
            json_data=payload,
        )
        if status == 401:
            raise UnauthorizedError("Unauthorized", status_code=401)
        if status >= 400:
            raise Exception("Failed to update preferences")
        return _to_domain(DRFProfileResponse.model_validate(body))
