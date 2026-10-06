"""Integration tests for auth repository."""

import httpx
import pytest
import respx

from src.identity.domain.entities import User
from src.identity.infrastructure.repository import DRFAuthRepository
from src.shared.http.drf_client import DRFAPIClient


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

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_current_user_accepts_real_backend_payload(self, auth_repository):
        """The DRF backend returns `full_name`, not `name`, plus extra fields.

        Captured from the running backend (budget-tracker
        `apps.users.interfaces.serializers.UserSerializer`); this is the exact
        payload that used to raise ValidationError on `name`.
        """
        respx.get("http://testserver/api/v1/users/me/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "dd6bc55b-6835-4e9-9c0a-1f0f2c3d4e5f",
                    "email": "ana@example.com",
                    "full_name": "Ana Vargas",
                    "plan": "free",
                    "is_active": True,
                    "is_staff": False,
                    "is_superuser": False,
                    "created_at": "2026-09-24T22:34:37.119030Z",
                    "updated_at": "2026-09-24T22:34:37.119030Z",
                },
            )
        )
        user = await auth_repository.get_current_user("access-token")
        assert user.id == "dd6bc55b-6835-4e9-9c0a-1f0f2c3d4e5f"
        assert user.email == "ana@example.com"
        assert user.name == "Ana Vargas"
        assert user.plan == "free"
        assert user.is_active is True
