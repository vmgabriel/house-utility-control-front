"""Tests for CSRF token manager."""

import hashlib
import hmac
import secrets
import time

import pytest

from src.infrastructure.auth.csrf import CSRFTokenManager


class TestCSRFTokenManager:
    def test_generate_token_has_three_parts(self):
        manager = CSRFTokenManager("secret")
        token = manager.generate_token()
        parts = token.split(":")
        assert len(parts) == 3

    def test_validate_token_accepts_fresh_token(self):
        manager = CSRFTokenManager("secret")
        token = manager.generate_token()
        assert manager.validate_token(token) is True

    def test_validate_token_rejects_tampered_token(self):
        manager = CSRFTokenManager("secret")
        token = manager.generate_token()
        tampered = token[:-4] + "xxxx"
        assert manager.validate_token(tampered) is False

    def test_validate_token_rejects_wrong_secret(self):
        manager1 = CSRFTokenManager("secret-1")
        manager2 = CSRFTokenManager("secret-2")
        token = manager1.generate_token()
        assert manager2.validate_token(token) is False

    def test_validate_token_rejects_expired_token(self):
        manager = CSRFTokenManager("secret")
        # Craft an old token manually
        salt = secrets.token_urlsafe(32)
        old_timestamp = str(int(time.time()) - (8 * 24 * 3600))  # 8 days ago
        message = f"{salt}:{old_timestamp}".encode()
        signature = hmac.new(b"secret", message, hashlib.sha256).hexdigest()
        expired_token = f"{salt}:{old_timestamp}:{signature}"
        assert manager.validate_token(expired_token) is False

    def test_validate_token_rejects_empty_or_malformed(self):
        manager = CSRFTokenManager("secret")
        assert manager.validate_token("") is False
        assert manager.validate_token("not:a:valid:token:structure") is False

    def test_constructor_rejects_empty_secret(self):
        with pytest.raises(ValueError):
            CSRFTokenManager("")
