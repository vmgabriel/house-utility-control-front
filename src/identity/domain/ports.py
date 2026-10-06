"""Repository protocol (port) for the identity context."""

from typing import Protocol

from .entities import User


class AuthRepositoryPort(Protocol):
    """Port for authentication operations."""

    async def login(self, email: str, password: str) -> tuple[str, str]:
        """Authenticate user and return (access_token, refresh_token)."""
        ...

    async def refresh_token(self, refresh_token: str) -> tuple[str, str]:
        """Refresh access token and return (new_access_token, new_refresh_token)."""
        ...

    async def logout(self, access_token: str) -> None:
        """Logout user."""
        ...

    async def get_current_user(self, access_token: str) -> User:
        """Get current authenticated user."""
        ...
