"""Component tests for the authenticated web flows.

The Flask test client drives the real WSGI stack (including `async def` views
via asgiref) and respx stands in for the DRF backend, so these exercise the
whole chain: view -> use case -> auto-refreshing repository -> httpx -> DRF.
"""

import json
from datetime import date, timedelta

import httpx
import pytest
import respx

from src.interfaces.web.app import create_app
from src.shared.auth.jwt_cookie_manager import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
)

BASE = "http://api.test/api/v1"

# The domain rejects future dates, so anything written through the API must use
# a date derived from the clock rather than a hard-coded literal.
TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()

SUMMARY = {
    # Mirrors the real DashboardSummarySerializer: one `date`, no investment or
    # savings totals, plus freshness metadata.
    "id": "5a0b1c22-0000-4000-8000-000000000001",
    "period": "daily",
    "date": "2026-09-26",
    "total_income": "100.00",
    "total_expense": "50.00",
    "net_balance": "35.00",
    "generated_at": "2026-09-26T10:00:00Z",
    "is_stale": False,
    "stale_at": None,
    "status": "fresh",
}

TRANSACTIONS_PAGE = {
    "count": 1,
    "page": 1,
    "page_size": 20,
    "results": [
        {
            "id": "txn-1",
            "transaction_type": "expense",
            "amount": "42.50",
            "category": "Food",
            "date": "2026-09-20",
            "description": "Coffee",
        }
    ],
}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DRF_API_BASE_URL", BASE)
    monkeypatch.setenv("FLASK_DEBUG", "false")
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf_token(app):
    return app.csrf_manager.generate_token()


def login(client, csrf_token):
    """Authenticate through the real login form and return the response."""
    return client.post(
        "/auth/login",
        data={
            "email": "ana@example.com",
            "password": "password123",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )


def authenticate(client):
    """Seed the JWT cookies directly for tests that skip the login form."""
    client.set_cookie(ACCESS_COOKIE, "acc-1")
    client.set_cookie(REFRESH_COOKIE, "ref-1")


class TestLoginFlow:
    @respx.mock
    def test_login_success_sets_httponly_cookies_and_redirects(
        self, client, csrf_token
    ):
        respx.post(f"{BASE}/users/auth/login/").mock(
            return_value=httpx.Response(
                200, json={"access": "acc-1", "refresh": "ref-1"}
            )
        )
        respx.get(f"{BASE}/users/me/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "u1",
                    "email": "ana@example.com",
                    # The real backend field is `full_name`.
                    "full_name": "Ana",
                    "plan": "pro",
                    "is_active": True,
                    "is_staff": False,
                    "is_superuser": False,
                    "created_at": "2026-09-24T22:34:37.119030Z",
                    "updated_at": "2026-09-24T22:34:37.119030Z",
                },
            )
        )

        response = login(client, csrf_token)

        assert response.status_code == 302
        assert "/dashboard/" in response.headers["Location"]
        cookies = response.headers.getlist("Set-Cookie")
        assert any(
            c.startswith(f"{ACCESS_COOKIE}=acc-1") and "HttpOnly" in c for c in cookies
        )
        assert any(
            c.startswith(f"{REFRESH_COOKIE}=ref-1") and "HttpOnly" in c for c in cookies
        )

    @respx.mock
    def test_login_success_flashes_welcome(self, client, csrf_token):
        respx.post(f"{BASE}/users/auth/login/").mock(
            return_value=httpx.Response(
                200, json={"access": "acc-1", "refresh": "ref-1"}
            )
        )
        respx.get(f"{BASE}/users/me/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "u1",
                    "email": "ana@example.com",
                    "full_name": "Ana",
                    "plan": "pro",
                },
            )
        )
        followed = login(client, csrf_token)
        # The redirect target renders the flash from the login request.
        page = client.get(followed.headers["Location"])
        assert b"Welcome back, Ana!" in page.data

    @respx.mock
    def test_login_invalid_credentials_shows_error(self, client, csrf_token):
        respx.post(f"{BASE}/users/auth/login/").mock(
            return_value=httpx.Response(401, json={"detail": "Invalid"})
        )
        response = login(client, csrf_token)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]
        assert not any(
            c.startswith(f"{ACCESS_COOKIE}=")
            for c in response.headers.getlist("Set-Cookie")
        )

    @respx.mock
    def test_login_rejects_missing_csrf_token_without_calling_backend(self, client):
        route = respx.post(f"{BASE}/users/auth/login/").mock(
            return_value=httpx.Response(200, json={"access": "a", "refresh": "r"})
        )
        response = client.post(
            "/auth/login", data={"email": "ana@example.com", "password": "pw"}
        )
        assert response.status_code == 302
        assert not route.called

    @respx.mock
    def test_login_rejects_tampered_csrf_token(self, client):
        route = respx.post(f"{BASE}/users/auth/login/").mock(
            return_value=httpx.Response(200, json={"access": "a", "refresh": "r"})
        )
        response = client.post(
            "/auth/login",
            data={
                "email": "ana@example.com",
                "password": "pw",
                "csrf_token": "salt:123:deadbeef",
            },
        )
        assert response.status_code == 302
        assert not route.called


class TestLogoutFlow:
    @respx.mock
    def test_logout_clears_cookies(self, client, csrf_token):
        authenticate(client)
        respx.post(f"{BASE}/users/auth/logout/").mock(return_value=httpx.Response(204))
        response = client.post("/auth/logout", data={"csrf_token": csrf_token})
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]
        # Deletion cookies expire the token immediately.
        assert any(
            c.startswith(f"{ACCESS_COOKIE}=") and "Max-Age=0" in c
            for c in response.headers.getlist("Set-Cookie")
        )

    def test_logout_rejects_missing_csrf_token(self, client):
        authenticate(client)
        response = client.post("/auth/logout")
        assert response.status_code == 302
        # The session survives a forged logout attempt.
        assert client.get("/dashboard/").status_code != 302

    def test_logout_succeeds_even_if_backend_fails(self, client, csrf_token):
        authenticate(client)
        with respx.mock:
            respx.post(f"{BASE}/users/auth/logout/").mock(
                return_value=httpx.Response(500)
            )
            response = client.post("/auth/logout", data={"csrf_token": csrf_token})
        assert response.status_code == 302
        assert any(
            c.startswith(f"{ACCESS_COOKIE}=") and "Max-Age=0" in c
            for c in response.headers.getlist("Set-Cookie")
        )


class TestDashboardRendering:
    @respx.mock
    def test_dashboard_renders_summary_values(self, client):
        authenticate(client)
        respx.get(f"{BASE}/dashboard/overview/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "today": SUMMARY,
                    "this_week": {**SUMMARY, "period": "weekly"},
                    "this_month": None,
                },
            )
        )
        response = client.get("/dashboard/")
        assert response.status_code == 200
        body = response.data.decode()
        assert "$35.00" in body
        assert "This Week" in body
        assert "Sep 26 - Sep 26, 2026" in body
        # A missing period falls back to its title rather than erroring.
        assert "This Month" in body

    @respx.mock
    def test_dashboard_renders_negative_net_balance(self, client):
        """17 of 244 real summaries are negative; they must render, not 500."""
        authenticate(client)
        respx.get(f"{BASE}/dashboard/overview/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "as_of_date": "2026-09-26",
                    "today": {
                        **SUMMARY,
                        "total_income": "0.00",
                        "total_expense": "340.00",
                        "net_balance": "-340.00",
                    },
                    "this_week": None,
                    "this_month": None,
                },
            )
        )
        response = client.get("/dashboard/")
        assert response.status_code == 200
        assert b"-$340.00" in response.data

    @respx.mock
    def test_dashboard_degrades_gracefully_on_backend_error(self, client):
        authenticate(client)
        respx.get(f"{BASE}/dashboard/overview/").mock(return_value=httpx.Response(500))
        response = client.get("/dashboard/")
        assert response.status_code == 200
        assert b"$0.00" in response.data

    @respx.mock
    def test_dashboard_shows_zero_state_when_everything_is_null(self, client):
        authenticate(client)
        respx.get(f"{BASE}/dashboard/overview/").mock(
            return_value=httpx.Response(
                200, json={"today": None, "this_week": None, "this_month": None}
            )
        )
        response = client.get("/dashboard/")
        assert response.status_code == 200
        assert b"No data" in response.data

    @respx.mock
    def test_expired_access_token_is_refreshed_and_retried(self, client):
        authenticate(client)
        respx.post(f"{BASE}/users/auth/refresh/").mock(
            return_value=httpx.Response(
                200, json={"access": "acc-2", "refresh": "ref-2"}
            )
        )
        route = respx.get(f"{BASE}/dashboard/overview/").mock(
            side_effect=[
                httpx.Response(401, json={"detail": "Token expired"}),
                httpx.Response(
                    200,
                    json={"today": SUMMARY, "this_week": None, "this_month": None},
                ),
            ]
        )
        response = client.get("/dashboard/")

        assert response.status_code == 200
        assert b"$35.00" in response.data
        assert route.call_count == 2
        assert route.calls[1].request.headers["Authorization"] == "Bearer acc-2"
        # The rotated pair is written back to the browser.
        assert any(
            c.startswith(f"{ACCESS_COOKIE}=acc-2") and "HttpOnly" in c
            for c in response.headers.getlist("Set-Cookie")
        )


class TestTransactionsRendering:
    @respx.mock
    def test_transactions_list_renders_rows(self, client):
        authenticate(client)
        respx.get(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(200, json=TRANSACTIONS_PAGE)
        )
        response = client.get("/transactions/")
        body = response.data.decode()
        assert response.status_code == 200
        assert "Coffee" in body
        assert "-$42.50" in body
        assert "Sep 20, 2026" in body
        # The category from the API is shown alongside the type.
        assert "Food" in body
        assert "Page 1" in body

    @respx.mock
    def test_transactions_list_tolerates_null_description(self, client):
        authenticate(client)
        respx.get(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(
                200,
                json={
                    **TRANSACTIONS_PAGE,
                    "results": [
                        {**TRANSACTIONS_PAGE["results"][0], "description": None}
                    ],
                },
            )
        )
        response = client.get("/transactions/")
        assert response.status_code == 200
        assert b"No description" in response.data

    @respx.mock
    def test_transactions_empty_state(self, client):
        authenticate(client)
        respx.get(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(
                200, json={"count": 0, "next": None, "previous": None, "results": []}
            )
        )
        response = client.get("/transactions/")
        assert b"No transactions yet" in response.data

    @respx.mock
    def test_page_query_is_forwarded_and_validated(self, client):
        authenticate(client)
        route = respx.get(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(200, json=TRANSACTIONS_PAGE)
        )
        client.get("/transactions/?page=3")
        assert route.calls[0].request.url.params["page"] == "3"

        # A non-numeric page must not 500.
        assert client.get("/transactions/?page=abc").status_code == 200

    @respx.mock
    def test_transactions_backend_failure_shows_flash(self, client):
        authenticate(client)
        respx.get(f"{BASE}/transactions/").mock(return_value=httpx.Response(500))
        response = client.get("/transactions/")
        assert response.status_code == 200
        assert b"Failed to load transactions" in response.data


class TestTransactionCreation:
    @respx.mock
    def test_create_transaction_posts_payload_and_flashes(self, client, csrf_token):
        authenticate(client)
        route = respx.post(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "txn-new",
                    "transaction_type": "expense",
                    "amount": "25.00",
                    "category": "Food",
                    "description": "Lunch",
                    "date": TODAY,
                },
            )
        )
        response = client.post(
            "/transactions/create",
            data={
                "type": "expense",
                "amount": "25.00",
                "description": "Lunch",
                "category": "Food",
                "date": TODAY,
                "csrf_token": csrf_token,
            },
        )
        assert response.status_code == 302
        body = json.loads(route.calls[0].request.content)
        # Must match the backend's CreateTransactionSerializer field names.
        assert body == {
            "transaction_type": "expense",
            "amount": "25.00",
            "category": "Food",
            "description": "Lunch",
            "date": TODAY,
        }

    @respx.mock
    def test_create_transaction_defaults_blank_category(self, client, csrf_token):
        authenticate(client)
        route = respx.post(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "txn-new",
                    "transaction_type": "expense",
                    "amount": "5.00",
                    "category": "General",
                    "description": "Coffee",
                    "date": TODAY,
                },
            )
        )
        # A blank category is rejected with a 400 by the backend, so the view
        # substitutes the domain default instead.
        client.post(
            "/transactions/create",
            data={
                "type": "expense",
                "amount": "5.00",
                "description": "Coffee",
                "category": "   ",
                "date": TODAY,
                "csrf_token": csrf_token,
            },
        )
        assert json.loads(route.calls[0].request.content)["category"] == "General"

    @respx.mock
    def test_create_transaction_requires_csrf(self, client):
        authenticate(client)
        route = respx.post(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(201, json={})
        )
        response = client.post(
            "/transactions/create",
            data={
                "type": "expense",
                "amount": "25.00",
                "description": "Lunch",
                "date": TODAY,
            },
        )
        assert response.status_code == 302
        assert not route.called

    @respx.mock
    def test_create_transaction_rejects_bad_amount(self, client, csrf_token):
        authenticate(client)
        route = respx.post(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(201, json={})
        )
        response = client.post(
            "/transactions/create",
            data={
                "type": "expense",
                "amount": "not-a-number",
                "description": "Lunch",
                "date": TODAY,
                "csrf_token": csrf_token,
            },
            follow_redirects=True,
        )
        assert b"Invalid transaction data" in response.data
        assert not route.called

    @respx.mock
    def test_create_transaction_rejects_zero_amount(self, client, csrf_token):
        authenticate(client)
        route = respx.post(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(201, json={})
        )
        response = client.post(
            "/transactions/create",
            data={
                "type": "expense",
                "amount": "0.00",
                "description": "Nothing",
                "date": TODAY,
                "csrf_token": csrf_token,
            },
            follow_redirects=True,
        )
        # The domain invariant message reaches the user, not a generic failure.
        assert b"Transaction amount must be positive" in response.data
        assert not route.called

    @respx.mock
    def test_create_transaction_rejects_future_date(self, client, csrf_token):
        authenticate(client)
        route = respx.post(f"{BASE}/transactions/").mock(
            return_value=httpx.Response(201, json={})
        )
        response = client.post(
            "/transactions/create",
            data={
                "type": "expense",
                "amount": "10.00",
                "description": "Future",
                "date": TOMORROW,
                "csrf_token": csrf_token,
            },
            follow_redirects=True,
        )
        assert b"cannot be in the future" in response.data
        assert not route.called

    def test_create_transaction_requires_authentication(self, client, csrf_token):
        response = client.post(
            "/transactions/create",
            data={
                "type": "expense",
                "amount": "10.00",
                "description": "X",
                "date": TODAY,
                "csrf_token": csrf_token,
            },
        )
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]


class TestTransactionDeletion:
    @respx.mock
    def test_delete_transaction_sends_delete(self, client, csrf_token):
        authenticate(client)
        route = respx.delete(f"{BASE}/transactions/txn-1/").mock(
            return_value=httpx.Response(204)
        )
        response = client.post(
            "/transactions/txn-1/delete", data={"csrf_token": csrf_token}
        )
        assert response.status_code == 302
        assert route.called

    @respx.mock
    def test_delete_transaction_requires_csrf(self, client):
        authenticate(client)
        route = respx.delete(f"{BASE}/transactions/txn-1/").mock(
            return_value=httpx.Response(204)
        )
        client.post("/transactions/txn-1/delete")
        assert not route.called

    @respx.mock
    def test_delete_transaction_surfaces_failure(self, client, csrf_token):
        authenticate(client)
        respx.delete(f"{BASE}/transactions/txn-1/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found"})
        )
        response = client.post(
            "/transactions/txn-1/delete",
            data={"csrf_token": csrf_token},
            follow_redirects=True,
        )
        assert b"Failed to delete transaction" in response.data
