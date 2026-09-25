"""Authentication use cases."""

from dataclasses import dataclass

from ...domain.entities import User
from ...domain.ports import AuthRepositoryPort


@dataclass
class AuthenticateUserUseCase:
    """Use case for user authentication."""

    auth_repository: AuthRepositoryPort

    async def execute(self, email: str, password: str) -> tuple[str, str, User]:
        """
        Authenticate user and return tokens and user data.
        Returns: (access_token, refresh_token, user)
        """
        access_token, refresh_token = await self.auth_repository.login(email, password)
        user = await self.auth_repository.get_current_user(access_token)
        return access_token, refresh_token, user


@dataclass
class RefreshTokenUseCase:
    """Use case for refreshing access tokens."""

    auth_repository: AuthRepositoryPort

    async def execute(self, refresh_token: str) -> tuple[str, str]:
        """Refresh access token and return new tokens."""
        return await self.auth_repository.refresh_token(refresh_token)


@dataclass
class LogoutUserUseCase:
    """Use case for user logout."""

    auth_repository: AuthRepositoryPort

    async def execute(self, access_token: str) -> None:
        """Logout user."""
        await self.auth_repository.logout(access_token)
