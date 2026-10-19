"""Integration tests for the analytics data and funding-account routes.

The Flask test client drives the real view -> use case -> repository -> shared
client chain, with respx standing in for DRF, so these assert the route
contracts and the dashboard's consolidated markup rather than the templates in
isolation.
"""

import json
from datetime import date

import httpx
import pytest
import respx

from src.budget.domain.value_objects import AnalyticsPeriod
from src.budget.interfaces.blueprints.budget import _default_range
from src.interfaces.web.app import create_app
from src.shared.auth.jwt_cookie_manager import ACCESS_COOKIE, REFRESH_COOKIE

BASE = "http://api.test/api/v1"
ACCOUNTS_URL = f"{BASE}/transactions/accounts/"
ANALYTICS_URL = f"{BASE}/transactions/analytics/"
OVERVIEW_URL = f"{BASE}/dashboard/overview/"
CREATE_ACCOUNT_PATH = "/budget/accounts/create"


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


def empty_overview_response():
    return httpx.Response(
        200, json={"today": None, "this_week": None, "this_month": None}
    )


def mock_dashboard_load():
    """Stub the two calls the dashboard makes on every rendered request."""
    respx.get(OVERVIEW_URL).mock(return_value=empty_overview_response())
    respx.get(ACCOUNTS_URL).mock(return_value=empty_accounts_response())


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
    def test_an_overlong_range_falls_back_to_the_default(self, client):
        """A range over the backend's ceiling must never reach it."""
        authenticate(client)
        route = respx.get(ANALYTICS_URL).mock(
            return_value=httpx.Response(
                200, json={"period": "monthly", "group_by": "type", "data": []}
            )
        )

        response = client.get(
            "/budget/analytics/data/"
            "?period=monthly&start_date=2025-01-01&end_date=2026-09-30"
        )

        assert response.status_code == 200
        params = route.calls[0].request.url.params
        start = date.fromisoformat(params["start_date"])
        end = date.fromisoformat(params["end_date"])
        assert (end - start).days <= 366

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


class TestDefaultAnalyticsRange:
    """The default windows are what the dashboard asks for on first paint."""

    @pytest.mark.parametrize("period", list(AnalyticsPeriod))
    def test_every_default_range_fits_the_backend_ceiling(self, period):
        start, end = _default_range(period)
        assert end >= start
        # 366 is the backend's MAX_RANGE_DAYS, measured as a date difference.
        assert (end - start).days <= 366

    def test_monthly_covers_twelve_calendar_months_including_this_one(self):
        start, end = _default_range(AnalyticsPeriod.MONTHLY)
        assert start.day == 1
        months = (end.year - start.year) * 12 + (end.month - start.month) + 1
        assert months == 12


class TestDashboardConsolidation:
    @respx.mock
    def test_analytics_and_accounts_live_on_the_dashboard(self, client):
        authenticate(client)
        respx.get(OVERVIEW_URL).mock(return_value=empty_overview_response())
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

        response = client.get("/dashboard/")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        # The chart component reads its endpoint from a data attribute.
        assert 'data-analytics-url="/budget/analytics/data/"' in body
        assert 'id="barChart"' in body
        assert 'id="pieChart"' in body
        assert 'data-testid="period-daily"' in body
        assert 'data-testid="group-category"' in body
        # Account management is a dialog on this page, populated server-side.
        assert 'data-testid="manage-accounts"' in body
        assert 'data-testid="accounts-modal"' in body
        assert "Visa Rewards" in body
        assert "Credit card" in body

    @respx.mock
    def test_a_failing_accounts_endpoint_degrades_in_the_modal(self, client):
        authenticate(client)
        respx.get(OVERVIEW_URL).mock(return_value=empty_overview_response())
        respx.get(ACCOUNTS_URL).mock(
            return_value=httpx.Response(500, json={"detail": "boom"})
        )

        response = client.get("/dashboard/")

        assert response.status_code == 200
        assert b"Funding accounts could not be loaded right now." in response.data

    def test_the_standalone_analytics_page_is_gone(self, client):
        authenticate(client)
        assert client.get("/budget/analytics/").status_code == 404

    def test_the_standalone_accounts_page_is_gone(self, client):
        authenticate(client)
        assert client.get("/budget/accounts/").status_code == 404


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

        response = client.post(
            CREATE_ACCOUNT_PATH,
            data={"name": "Amex", "account_type": "debit", "csrf_token": csrf_token},
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard/")
        # Matches CreateAccountSerializer: only the writable fields.
        assert json.loads(route.calls[0].request.content) == {
            "name": "Amex",
            "account_type": "debit",
        }

        mock_dashboard_load()
        followed = client.get(response.headers["Location"])
        assert b"Funding account created successfully!" in followed.data

    @respx.mock
    def test_requires_csrf(self, client):
        authenticate(client)
        route = respx.post(ACCOUNTS_URL).mock(return_value=httpx.Response(201, json={}))

        response = client.post(
            CREATE_ACCOUNT_PATH, data={"name": "Amex", "account_type": "debit"}
        )

        assert response.status_code == 302
        assert not route.called
        mock_dashboard_load()
        followed = client.get(response.headers["Location"])
        assert b"Invalid or expired CSRF token." in followed.data

    @respx.mock
    def test_rejects_a_blank_name_before_calling_drf(self, client, csrf_token):
        authenticate(client)
        route = respx.post(ACCOUNTS_URL).mock(return_value=httpx.Response(201, json={}))

        response = client.post(
            CREATE_ACCOUNT_PATH,
            data={"name": "   ", "account_type": "cash", "csrf_token": csrf_token},
        )

        assert response.status_code == 302
        assert not route.called
        mock_dashboard_load()
        followed = client.get(response.headers["Location"])
        assert b"Account name cannot be blank" in followed.data

    @respx.mock
    def test_rejects_an_unknown_account_type(self, client, csrf_token):
        authenticate(client)
        route = respx.post(ACCOUNTS_URL).mock(return_value=httpx.Response(201, json={}))

        response = client.post(
            CREATE_ACCOUNT_PATH,
            data={"name": "Crypto", "account_type": "crypto", "csrf_token": csrf_token},
        )

        assert response.status_code == 302
        assert not route.called
        mock_dashboard_load()
        followed = client.get(response.headers["Location"])
        assert b"Choose a valid account type." in followed.data

    @respx.mock
    def test_surfaces_a_backend_rejection(self, client, csrf_token):
        authenticate(client)
        respx.post(ACCOUNTS_URL).mock(
            return_value=httpx.Response(400, json={"name": ["Already exists."]})
        )

        response = client.post(
            CREATE_ACCOUNT_PATH,
            data={"name": "Amex", "account_type": "debit", "csrf_token": csrf_token},
        )

        assert response.status_code == 302
        mock_dashboard_load()
        followed = client.get(response.headers["Location"])
        assert b"Funding account validation failed" in followed.data
        assert b"Funding account created successfully!" not in followed.data

    def test_requires_authentication(self, client, csrf_token):
        response = client.post(
            CREATE_ACCOUNT_PATH,
            data={"name": "Amex", "account_type": "debit", "csrf_token": csrf_token},
        )
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]
