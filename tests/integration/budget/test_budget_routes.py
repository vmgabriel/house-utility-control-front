"""Integration tests for the analytics and funding-account routes.

The Flask test client drives the real view -> use case -> repository -> shared
client chain, with respx standing in for DRF, so these assert the route
contracts rather than the templates in isolation.
"""

import json

import httpx
import pytest
import respx

from src.interfaces.web.app import create_app
from src.shared.auth.jwt_cookie_manager import ACCESS_COOKIE, REFRESH_COOKIE

BASE = "http://api.test/api/v1"
ACCOUNTS_URL = f"{BASE}/transactions/accounts/"
ANALYTICS_URL = f"{BASE}/transactions/analytics/"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DRF_API_BASE_URL", BASE)
    monkeypatch.setenv("FLASK_DEBUG", "false")
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf_token(app):
    return app.csrf_manager.generate_token()


def authenticate(client):
    client.set_cookie(ACCESS_COOKIE, "acc-1")
    client.set_cookie(REFRESH_COOKIE, "ref-1")


def empty_accounts_response():
    return httpx.Response(200, json={"count": 0, "results": []})


class TestAnalyticsPage:
    def test_redirects_when_not_authenticated(self, client):
        response = client.get("/budget/analytics/")
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]

    def test_renders_the_toggles_canvases_and_endpoint(self, client):
        authenticate(client)
        response = client.get("/budget/analytics/")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'id="barChart"' in body
        assert 'id="pieChart"' in body
        # The Alpine component reads its endpoint from the data attribute.
        assert 'data-analytics-url="/budget/analytics/data/"' in body
        assert 'data-testid="period-daily"' in body
        assert 'data-testid="group-category"' in body


class TestAnalyticsData:
    def test_requires_authentication(self, client):
        response = client.get("/budget/analytics/data/")
        assert response.status_code == 401
        assert response.get_json()["detail"] == "Authentication required."

    @respx.mock
    def test_returns_a_chart_ready_payload(self, client):
        authenticate(client)
        route = respx.get(ANALYTICS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "period": "monthly",
                    "group_by": "type",
                    "data": [
                        {
                            "label": "2026-09",
                            "income": "100.00",
                            "expense": "40.00",
                            "investment": "0.00",
                            "savings": "10.00",
                        }
                    ],
                },
            )
        )

        response = client.get("/budget/analytics/data/?period=monthly&group_by=type")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["period"] == "monthly"
        assert payload["groupBy"] == "type"
        assert payload["labels"] == ["Sep 2026"]
        assert payload["datasets"][0]["label"] == "Income"
        assert payload["datasets"][0]["data"] == [100.0]

        params = route.calls[0].request.url.params
        assert params["period"] == "monthly"
        assert params["group_by"] == "type"
        # A bounded range is always sent; the backend caps it at 366 days.
        assert params["start_date"]
        assert params["end_date"]

    @respx.mock
    def test_invalid_toggles_fall_back_to_the_defaults(self, client):
        authenticate(client)
        route = respx.get(ANALYTICS_URL).mock(
            return_value=httpx.Response(
                200, json={"period": "monthly", "group_by": "type", "data": []}
            )
        )

        response = client.get("/budget/analytics/data/?period=hourly&group_by=weekday")

        assert response.status_code == 200
        params = route.calls[0].request.url.params
        assert params["period"] == "monthly"
        assert params["group_by"] == "type"

    @respx.mock
    def test_an_inverted_range_falls_back_to_the_default(self, client):
        authenticate(client)
        route = respx.get(ANALYTICS_URL).mock(
            return_value=httpx.Response(
                200, json={"period": "monthly", "group_by": "type", "data": []}
            )
        )

        response = client.get(
            "/budget/analytics/data/"
            "?period=monthly&start_date=2026-09-30&end_date=2026-09-01"
        )

        assert response.status_code == 200
        params = route.calls[0].request.url.params
        assert params["start_date"] < params["end_date"]

    @respx.mock
    def test_a_backend_failure_returns_a_json_error(self, client):
        authenticate(client)
        respx.get(ANALYTICS_URL).mock(
            return_value=httpx.Response(500, json={"detail": "boom"})
        )

        response = client.get("/budget/analytics/data/")

        # The browser's fetch handles a JSON error; an HTML login redirect
        # would be unusable here.
        assert response.status_code in (500, 502)
        assert response.get_json()["detail"]


class TestAccountsPage:
    def test_redirects_when_not_authenticated(self, client):
        response = client.get("/budget/accounts/")
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]

    @respx.mock
    def test_lists_accounts_with_their_type_label(self, client):
        authenticate(client)
        respx.get(ACCOUNTS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [
                        {
                            "id": "acc-1",
                            "name": "Visa Rewards",
                            "account_type": "credit_card",
                            "is_active": True,
                        }
                    ],
                },
            )
        )

        response = client.get("/budget/accounts/")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Visa Rewards" in body
        assert "Credit card" in body
        assert "No funding accounts yet" not in body

    @respx.mock
    def test_shows_the_empty_state(self, client):
        authenticate(client)
        respx.get(ACCOUNTS_URL).mock(return_value=empty_accounts_response())

        response = client.get("/budget/accounts/")

        assert response.status_code == 200
        assert b"No funding accounts yet" in response.data

    @respx.mock
    def test_degrades_when_the_backend_fails(self, client):
        authenticate(client)
        respx.get(ACCOUNTS_URL).mock(
            return_value=httpx.Response(500, json={"detail": "boom"})
        )

        response = client.get("/budget/accounts/")

        assert response.status_code == 200
        assert b"Funding accounts could not be loaded right now." in response.data
        assert b"No funding accounts yet" in response.data


class TestCreateFundingAccount:
    @respx.mock
    def test_posts_the_create_payload_and_flashes(self, client, csrf_token):
        authenticate(client)
        route = respx.post(ACCOUNTS_URL).mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "acc-9",
                    "name": "Amex",
                    "account_type": "debit",
                    "is_active": True,
                },
            )
        )
        respx.get(ACCOUNTS_URL).mock(return_value=empty_accounts_response())

        response = client.post(
            "/budget/accounts/",
            data={"name": "Amex", "account_type": "debit", "csrf_token": csrf_token},
        )

        assert response.status_code == 302
        # Matches CreateAccountSerializer: only the writable fields.
        assert json.loads(route.calls[0].request.content) == {
            "name": "Amex",
            "account_type": "debit",
        }

        followed = client.get(response.headers["Location"])
        assert b"Funding account created successfully!" in followed.data

    @respx.mock
    def test_requires_csrf(self, client):
        authenticate(client)
        route = respx.post(ACCOUNTS_URL).mock(return_value=httpx.Response(201, json={}))
        respx.get(ACCOUNTS_URL).mock(return_value=empty_accounts_response())

        response = client.post(
            "/budget/accounts/", data={"name": "Amex", "account_type": "debit"}
        )

        assert response.status_code == 302
        assert not route.called
        followed = client.get(response.headers["Location"])
        assert b"Invalid or expired CSRF token." in followed.data

    @respx.mock
    def test_rejects_a_blank_name_before_calling_drf(self, client, csrf_token):
        authenticate(client)
        route = respx.post(ACCOUNTS_URL).mock(return_value=httpx.Response(201, json={}))
        respx.get(ACCOUNTS_URL).mock(return_value=empty_accounts_response())

        response = client.post(
            "/budget/accounts/",
            data={"name": "   ", "account_type": "cash", "csrf_token": csrf_token},
            follow_redirects=True,
        )

        assert b"Account name cannot be blank" in response.data
        assert not route.called

    @respx.mock
    def test_rejects_an_unknown_account_type(self, client, csrf_token):
        authenticate(client)
        route = respx.post(ACCOUNTS_URL).mock(return_value=httpx.Response(201, json={}))
        respx.get(ACCOUNTS_URL).mock(return_value=empty_accounts_response())

        response = client.post(
            "/budget/accounts/",
            data={"name": "Crypto", "account_type": "crypto", "csrf_token": csrf_token},
            follow_redirects=True,
        )

        assert b"Choose a valid account type." in response.data
        assert not route.called

    @respx.mock
    def test_surfaces_a_backend_rejection(self, client, csrf_token):
        authenticate(client)
        respx.post(ACCOUNTS_URL).mock(
            return_value=httpx.Response(400, json={"name": ["Already exists."]})
        )
        respx.get(ACCOUNTS_URL).mock(return_value=empty_accounts_response())

        response = client.post(
            "/budget/accounts/",
            data={"name": "Amex", "account_type": "debit", "csrf_token": csrf_token},
            follow_redirects=True,
        )

        assert b"Funding account validation failed" in response.data
        assert b"Funding account created successfully!" not in response.data

    def test_requires_authentication(self, client, csrf_token):
        response = client.post(
            "/budget/accounts/",
            data={"name": "Amex", "account_type": "debit", "csrf_token": csrf_token},
        )
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]
