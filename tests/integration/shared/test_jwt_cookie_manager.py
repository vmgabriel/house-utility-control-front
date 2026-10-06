"""Tests for JWT cookie manager."""

from flask import Flask, request

from src.shared.auth.jwt_cookie_manager import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    CookieConfig,
    JWTCookieManager,
)


class TestJWTCookieManager:
    def test_set_tokens_creates_httponly_cookies(self):
        app = Flask(__name__)
        manager = JWTCookieManager(
            CookieConfig(secure=False)
        )  # secure=False for testing

        with app.test_request_context():
            response = app.make_response("ok")
            manager.set_tokens(response, "access-jwt", "refresh-jwt")

            # Parse Set-Cookie headers
            cookies = {
                c.split("=")[0]: c for c in response.headers.getlist("Set-Cookie")
            }
            assert ACCESS_COOKIE in cookies
            assert REFRESH_COOKIE in cookies
            assert "HttpOnly" in cookies[ACCESS_COOKIE]
            assert "HttpOnly" in cookies[REFRESH_COOKIE]

    def test_get_access_token_from_request(self):
        app = Flask(__name__)
        manager = JWTCookieManager()

        with app.test_request_context():
            request.cookies = {ACCESS_COOKIE: "my-access"}
            assert manager.get_access_token(request) == "my-access"

    def test_clear_tokens_removes_cookies(self):
        app = Flask(__name__)
        manager = JWTCookieManager()

        with app.test_request_context():
            response = app.make_response("ok")
            manager.clear_tokens(response)
            cookies = response.headers.getlist("Set-Cookie")
            assert any(ACCESS_COOKIE in c and "Max-Age=0" in c for c in cookies)
