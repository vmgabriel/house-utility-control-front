"""Secure JWT cookie management for the Flask BFF."""

from dataclasses import dataclass

from flask import Request, Response

ACCESS_COOKIE = "bt_access_token"
REFRESH_COOKIE = "bt_refresh_token"


@dataclass(frozen=True, slots=True)
class CookieConfig:
    """Configuration for JWT cookies."""

    secure: bool = True
    samesite: str = "Lax"
    httponly: bool = True
    access_max_age: int = 3600  # 1 hour (matches DRF access token expiry)
    refresh_max_age: int = 7 * 24 * 3600  # 7 days (matches DRF refresh token expiry)
    path: str = "/"


class JWTCookieManager:
    """Manages secure HttpOnly JWT cookies.

    The tokens never reach client-side JavaScript: templates read the CSRF
    cookie instead, and every state-changing call is authenticated by the
    browser replaying the HttpOnly cookies.
    """

    def __init__(self, config: CookieConfig | None = None):
        self.config = config or CookieConfig()

    def set_tokens(
        self, response: Response, access_token: str, refresh_token: str
    ) -> None:
        """Set both access and refresh tokens as HttpOnly cookies."""
        response.set_cookie(
            ACCESS_COOKIE,
            access_token,
            max_age=self.config.access_max_age,
            secure=self.config.secure,
            httponly=self.config.httponly,
            samesite=self.config.samesite,
            path=self.config.path,
        )
        response.set_cookie(
            REFRESH_COOKIE,
            refresh_token,
            max_age=self.config.refresh_max_age,
            secure=self.config.secure,
            httponly=self.config.httponly,
            samesite=self.config.samesite,
            path=self.config.path,
        )

    def clear_tokens(self, response: Response) -> None:
        """Clear both token cookies."""
        response.delete_cookie(
            ACCESS_COOKIE, path=self.config.path, samesite=self.config.samesite
        )
        response.delete_cookie(
            REFRESH_COOKIE, path=self.config.path, samesite=self.config.samesite
        )

    def get_access_token(self, request: Request) -> str | None:
        """Extract access token from cookies."""
        return request.cookies.get(ACCESS_COOKIE)

    def get_refresh_token(self, request: Request) -> str | None:
        """Extract refresh token from cookies."""
        return request.cookies.get(REFRESH_COOKIE)
