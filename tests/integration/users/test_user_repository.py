"""Integration tests for the user repository."""

import json

import httpx
import pytest
import respx

from src.infrastructure.api.drf_client import DRFAPIClient, UnauthorizedError
from src.users.domain.entities import SystemUser, UserPlan
from src.users.infrastructure.repository import DRFUserRepository

USER_JSON = {
    "id": "user-1",
    "email": "a@example.com",
    "full_name": "Ana",
    "plan": "free",
    "is_active": True,
    "is_staff": False,
}


@pytest.fixture
def repository():
    api_client = DRFAPIClient(base_url="http://testserver/api/v1")
    return DRFUserRepository(api_client)


class TestRegister:
    @pytest.mark.asyncio
    @respx.mock
    async def test_register_posts_payload_and_maps_response(self, repository):
        route = respx.post("http://testserver/api/v1/users/auth/register/").mock(
            return_value=httpx.Response(201, json=USER_JSON)
        )
        user = await repository.register("a@example.com", "Ana", "password123")
        assert isinstance(user, SystemUser)
        assert user.plan is UserPlan.FREE
        sent = json.loads(route.calls.last.request.content)
        assert sent == {
            "email": "a@example.com",
            "full_name": "Ana",
            "password": "password123",
        }

    @pytest.mark.asyncio
    @respx.mock
    async def test_register_raises_on_error(self, repository):
        respx.post("http://testserver/api/v1/users/auth/register/").mock(
            return_value=httpx.Response(400, json={"email": ["Already taken."]})
        )
        with pytest.raises(Exception, match="Registration failed"):
            await repository.register("a@example.com", "Ana", "password123")


class TestListUsers:
    @pytest.mark.asyncio
    @respx.mock
    async def test_list_maps_count_and_results(self, repository):
        respx.get("http://testserver/api/v1/users/").mock(
            return_value=httpx.Response(200, json={"count": 1, "results": [USER_JSON]})
        )
        count, users = await repository.list_users("token", page=1, page_size=20)
        assert count == 1
        assert users[0].email == "a@example.com"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_raises_unauthorized_on_401(self, repository):
        respx.get("http://testserver/api/v1/users/").mock(
            return_value=httpx.Response(401, json={"detail": "bad token"})
        )
        with pytest.raises(UnauthorizedError):
            await repository.list_users("token", 1, 20)


class TestUpdateUserStatus:
    @pytest.mark.asyncio
    @respx.mock
    async def test_toggle_status_sends_payload(self, repository):
        route = respx.patch("http://testserver/api/v1/users/user-1/").mock(
            return_value=httpx.Response(200, json={**USER_JSON, "is_active": False})
        )
        user = await repository.update_user_status("token", "user-1", False)
        assert user.is_active is False
        sent = json.loads(route.calls.last.request.content)
        assert sent == {"is_active": False}

    @pytest.mark.asyncio
    @respx.mock
    async def test_toggle_status_sends_ban_reason_when_given(self, repository):
        route = respx.patch("http://testserver/api/v1/users/user-1/").mock(
            return_value=httpx.Response(200, json={**USER_JSON, "is_active": False})
        )
        await repository.update_user_status("token", "user-1", False, "spam")
        sent = json.loads(route.calls.last.request.content)
        assert sent == {"is_active": False, "ban_reason": "spam"}


class TestUpdateUserPlan:
    @pytest.mark.asyncio
    @respx.mock
    async def test_update_plan_sends_payload(self, repository):
        route = respx.patch("http://testserver/api/v1/users/user-1/plan/").mock(
            return_value=httpx.Response(200, json={**USER_JSON, "plan": "premium"})
        )
        user = await repository.update_user_plan("token", "user-1", "premium")
        assert user.plan is UserPlan.PREMIUM
        sent = json.loads(route.calls.last.request.content)
        assert sent == {"plan": "premium"}
