"""Request-id correlation on the real WSGI stack.

The unit test covers the middleware in isolation; this proves `create_app`
actually installs it and that the header reaches the client.
"""

from __future__ import annotations

import pytest

from src.interfaces.web.app import create_app

BASE = "http://api.test/api/v1"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DRF_API_BASE_URL", BASE)
    monkeypatch.setenv("FLASK_DEBUG", "false")
    app = create_app()
    app.config["TESTING"] = True
    return app


def test_response_carries_a_minted_request_id(app):
    response = app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


def test_incoming_request_id_is_propagated_unchanged(app):
    response = app.test_client().get("/healthz", headers={"X-Request-ID": "trace-42"})
    assert response.headers["X-Request-ID"] == "trace-42"
