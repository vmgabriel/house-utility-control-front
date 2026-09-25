"""Integration tests for auth repository."""

import httpx
import pytest
import respx

from src.domain.entities import User
from src.infrastructure.api.drf_client import DRFAPIClient
from src.infrastructure.repositories.auth_repository import DRFAuthRepository


@pytest.fixture
def auth_repository():
    api_client = DRFAPIClient(base_url="http://testserver/api/v1")
    return DRFAuthRepository(api_client)


class TestDRFAuthRepository:
    @pytest.mark.asyncio
    @respx.mock
    async def test_login_returns_tokens(self, auth_repository):
        respx.post("http://testserver/api/v1/users/auth/login/").mock(
            return_value=httpx.Response(200, json={"access": "acc", "refresh": "ref"})
        )
        access, refresh = await auth_repository.login("user@example.com", "password")
        assert access == "acc"
        assert refresh == "ref"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_current_user_maps_to_domain_entity(self, auth_repository):
        respx.get("http://testserver/api/v1/users/me/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "user-123",
                    "email": "user@example.com",
                    "name": "Test User",
                    "plan": "free",
                    "is_active": True,
                },
            )
        )
        user = await auth_repository.get_current_user("access-token")
        assert isinstance(user, User)
        assert user.id == "user-123"
        assert user.email == "user@example.com"
        assert user.plan == "free"
