"""Rate limiting on the public auth endpoints.

The suite pins `RATELIMIT_ENABLED=false` (tests/conftest.py) because the E2E
suite signs in far more often than the limit allows. This is the one place that
turns it back on, so the limit is proven to be real rather than merely
configured.
"""

from __future__ import annotations

import pytest

from src.interfaces.web.app import create_app
from src.shared.rate_limit import limiter

BASE = "http://api.test/api/v1"


@pytest.fixture
def limited_app(monkeypatch):
    monkeypatch.setenv("DRF_API_BASE_URL", BASE)
    monkeypatch.setenv("FLASK_DEBUG", "false")
    monkeypatch.setenv("RATELIMIT_ENABLED", "true")

    app = create_app()
    app.config["TESTING"] = True
    # The limiter is a module-level singleton with in-process storage shared by
    # every app built in this process; clear it so ordering cannot matter.
    limiter.reset()
    try:
        yield app
    finally:
        limiter.reset()


def test_login_is_rate_limited_after_five_attempts(limited_app):
    client = limited_app.test_client()

    # CSRF is irrelevant here: the limiter runs before the view, so an empty
    # POST still counts. Five pass (and bounce to the form), the sixth is 429.
    statuses = [client.post("/auth/login", data={}).status_code for _ in range(6)]

    assert statuses[:5] == [302] * 5
    assert statuses[5] == 429


def test_register_is_rate_limited_after_five_attempts(limited_app):
    client = limited_app.test_client()

    statuses = [client.post("/users/register", data={}).status_code for _ in range(6)]

    assert statuses[:5] == [302] * 5
    assert statuses[5] == 429
