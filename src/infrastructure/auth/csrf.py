"""CSRF token generation and validation for state-changing requests."""

import hashlib
import hmac
import secrets
import time

from flask import Request, Response

CSRF_COOKIE = "bt_csrf_token"
CSRF_HEADER = "X-CSRF-Token"
CSRF_TOKEN_MAX_AGE = 7 * 24 * 3600  # 7 days


class CSRFTokenManager:
    """Generates and validates CSRF tokens using HMAC.

    The token embeds a random salt, an issue timestamp and an HMAC-SHA256
    signature, so it is stateless: no server-side session store is needed and
    the token cannot be forged without the Flask secret key.
    """

    def __init__(self, secret_key: str):
        if not secret_key:
            raise ValueError("CSRFTokenManager requires a non-empty secret_key")
        self.secret_key = secret_key.encode("utf-8")

    def generate_token(self) -> str:
        """Generate a new CSRF token (random salt + HMAC signature)."""
        salt = secrets.token_urlsafe(32)
        timestamp = str(int(time.time()))
        message = f"{salt}:{timestamp}".encode()
        signature = hmac.new(self.secret_key, message, hashlib.sha256).hexdigest()
        return f"{salt}:{timestamp}:{signature}"

    def validate_token(self, token: str) -> bool:
        """Validate a CSRF token (checks signature and age)."""
        if not token:
            return False
        try:
            parts = token.split(":")
            if len(parts) != 3:
                return False
            salt, timestamp, signature = parts

            # Check age
            token_age = int(time.time()) - int(timestamp)
            if token_age < 0 or token_age > CSRF_TOKEN_MAX_AGE:
                return False

            # Verify signature (constant-time comparison)
            message = f"{salt}:{timestamp}".encode()
            expected = hmac.new(self.secret_key, message, hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected)
        except (ValueError, AttributeError):
            return False

    def set_csrf_cookie(self, response: Response, token: str) -> None:
        """Set CSRF token as a readable cookie.

        Deliberately not HttpOnly: the browser must be able to read this value
        and echo it back in the ``X-CSRF-Token`` header, which is what proves
        the request came from our own page and not a cross-site form post.
        """
        response.set_cookie(
            CSRF_COOKIE,
            token,
            max_age=CSRF_TOKEN_MAX_AGE,
            secure=True,
            httponly=False,
            samesite="Lax",
            path="/",
        )

    def get_token_from_request(self, request: Request) -> str | None:
        """Extract CSRF token from header or cookie."""
        return request.headers.get(CSRF_HEADER) or request.cookies.get(CSRF_COOKIE)
