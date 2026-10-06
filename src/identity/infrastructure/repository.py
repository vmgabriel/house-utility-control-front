"""Authentication repository implementation."""

from src.identity.domain.entities import User
from src.identity.domain.ports import AuthRepositoryPort
from src.identity.infrastructure.mappers import map_user_response
from src.identity.infrastructure.schemas import DRFTokenResponse, DRFUserResponse
from src.shared.http.drf_client import DRFAPIClient


class DRFAuthRepository:
    """DRF-backed implementation of AuthRepositoryPort."""

    def __init__(self, api_client: DRFAPIClient):
        self.api_client = api_client

    async def login(self, email: str, password: str) -> tuple[str, str]:
        data = await self.api_client.login(email, password)
        tokens = DRFTokenResponse.model_validate(data)
        return tokens.access, tokens.refresh

    async def refresh_token(self, refresh_token: str) -> tuple[str, str]:
        data = await self.api_client.refresh(refresh_token)
        tokens = DRFTokenResponse.model_validate(data)
        return tokens.access, tokens.refresh

    async def logout(self, access_token: str) -> None:
        await self.api_client.logout(access_token)

    async def get_current_user(self, access_token: str) -> User:
        data = await self.api_client.get_current_user(access_token)
        user_data = DRFUserResponse.model_validate(data)
        return map_user_response(user_data)


# Structural check: the adapter must satisfy the port it claims to implement.
_: type[AuthRepositoryPort] = DRFAuthRepository
