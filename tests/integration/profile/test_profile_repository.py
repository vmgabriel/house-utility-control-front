"""Integration tests for the profile repository."""

import httpx
import pytest
import respx

from src.infrastructure.api.drf_client import DRFAPIClient, UnauthorizedError
from src.profile.domain.entities import UserProfile
from src.profile.infrastructure.repository import DRFProfileRepository

PROFILE_JSON = {
    "id": "user-123",
    "first_name": "Test",
    "last_name": "User",
    "timezone": "UTC",
    "language": "es",
    "currency": "USD",
    "date_format": "YYYY-MM-DD",
    "avatar_url": None,
    "bio": None,
}


@pytest.fixture
def repository():
    api_client = DRFAPIClient(base_url="http://testserver/api/v1")
    return DRFProfileRepository(api_client)


class TestGetProfile:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_profile_maps_response(self, repository):
        respx.get("http://testserver/api/v1/profile/me/").mock(
            return_value=httpx.Response(200, json=PROFILE_JSON)
        )
        profile = await repository.get_profile("access-token")
        assert isinstance(profile, UserProfile)
        assert profile.id == "user-123"
        assert profile.language == "es"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_profile_raises_unauthorized_on_401(self, repository):
        respx.get("http://testserver/api/v1/profile/me/").mock(
            return_value=httpx.Response(401, json={"detail": "bad token"})
        )
        with pytest.raises(UnauthorizedError):
            await repository.get_profile("access-token")

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_profile_raises_on_server_error(self, repository):
        respx.get("http://testserver/api/v1/profile/me/").mock(
            return_value=httpx.Response(500, json={"detail": "oops"})
        )
        with pytest.raises(Exception, match="Failed to fetch profile"):
            await repository.get_profile("access-token")


class TestUpdateProfile:
    @pytest.mark.asyncio
    @respx.mock
    async def test_update_profile_sends_payload_and_maps_response(self, repository):
        route = respx.patch("http://testserver/api/v1/profile/me/").mock(
            return_value=httpx.Response(
                200, json={**PROFILE_JSON, "first_name": "Nuevo", "bio": None}
            )
        )
        profile = UserProfile(
            id="user-123",
            first_name="Nuevo",
            last_name="User",
            timezone="UTC",
            language="es",
            currency="USD",
            date_format="YYYY-MM-DD",
            avatar_url=None,
            bio="",
        )
        updated = await repository.update_profile("access-token", profile)
        assert updated.first_name == "Nuevo"
        import json

        sent = json.loads(route.calls.last.request.content)
        assert sent["first_name"] == "Nuevo"
        # Empty string is kept (it clears the field); None would be dropped.
        assert sent["bio"] == ""
        assert "avatar_url" not in sent


class TestUpdatePreferences:
    @pytest.mark.asyncio
    @respx.mock
    async def test_update_preferences_sends_payload(self, repository):
        route = respx.patch("http://testserver/api/v1/profile/me/preferences/").mock(
            return_value=httpx.Response(
                200, json={**PROFILE_JSON, "language": "en", "currency": "EUR"}
            )
        )
        updated = await repository.update_preferences(
            "access-token", "en", "EUR", "DD/MM/YYYY"
        )
        assert updated.language == "en"
        assert updated.currency == "EUR"
        import json

        sent = json.loads(route.calls.last.request.content)
        assert sent == {
            "language": "en",
            "currency": "EUR",
            "date_format": "DD/MM/YYYY",
        }
