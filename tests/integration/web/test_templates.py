"""Tests for template rendering."""

import pytest

from src.interfaces.web.app import create_app


@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


class TestLoginTemplate:
    def test_login_page_renders_with_csrf_token(self, client):
        response = client.get("/auth/login")
        assert response.status_code == 200
        assert b"Sign In" in response.data
        assert b"csrf_token" in response.data

    def test_login_page_sets_a_csrf_cookie(self, client):
        response = client.get("/auth/login")
        assert any(
            cookie.startswith("bt_csrf_token=")
            for cookie in response.headers.getlist("Set-Cookie")
        )

    def test_register_page_renders(self, client):
        response = client.get("/auth/register")
        assert response.status_code == 200
        assert b"Create Account" in response.data


class TestDashboardTemplate:
    def test_dashboard_redirects_when_not_authenticated(self, client):
        response = client.get("/dashboard/")
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]


class TestTransactionsTemplate:
    def test_transactions_redirects_when_not_authenticated(self, client):
        response = client.get("/transactions/")
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]


class TestRootRoute:
    def test_root_redirects_to_dashboard(self, client):
        response = client.get("/")
        assert response.status_code == 302
        assert "/dashboard/" in response.headers["Location"]

    def test_healthz_responds_without_backend(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.get_json()["status"] == "ok"
