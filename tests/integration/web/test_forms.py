"""Component tests for form rendering, CSRF wiring and view-model formatting.

These assert on the rendered HTML, so they cover what the E2E suite deliberately
does not: field names, input types, required attributes and the exact tokens
embedded in each form. The Flask test client drives the real WSGI stack and
respx stands in for DRF.
"""

import re
from datetime import date

import httpx
import pytest
import respx

from src.infrastructure.auth.jwt_cookie_manager import ACCESS_COOKIE, REFRESH_COOKIE
from src.interfaces.web.app import create_app

BASE = "http://api.test/api/v1"

TODAY = date.today().isoformat()

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

DASHBOARD_OVERVIEW = {
    "today": {
        "period": "daily",
        "total_income": "100.00",
        "total_expense": "50.00",
        "net_balance": "50.00",
        "start_date": "2026-09-26",
        "end_date": "2026-09-26",
    },
    "this_week": None,
    "this_month": None,
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


def authenticate(client):
    """Seed the JWT cookies directly, skipping the login form."""
    client.set_cookie(ACCESS_COOKIE, "acc-1")
    client.set_cookie(REFRESH_COOKIE, "ref-1")


def get_transactions_page(client):
    with respx.mock:
        respx.get(url__startswith=f"{BASE}/transactions/").mock(
            return_value=httpx.Response(200, json=TRANSACTIONS_PAGE)
        )
        return client.get("/transactions/").data.decode("utf-8")


def get_dashboard(client):
    with respx.mock:
        respx.get(f"{BASE}/dashboard/overview/").mock(
            return_value=httpx.Response(200, json=DASHBOARD_OVERVIEW)
        )
        return client.get("/dashboard/").data.decode("utf-8")


def field(html: str, name: str) -> str:
    """The opening tag of the input/select named ``name``."""
    match = re.search(
        rf"<(?:input|select)[^>]*\bname=\"{name}\"[^>]*>", html, re.DOTALL
    )
    assert match, f"no field named {name!r} in the rendered page"
    return match.group(0)


class TestLoginForm:
    def test_login_page_renders(self, client):
        response = client.get("/auth/login")
        assert response.status_code == 200
        assert "Sign In" in response.data.decode("utf-8")

    def test_login_form_has_required_fields(self, client):
        html = client.get("/auth/login").data.decode("utf-8")

        assert 'name="email"' in html
        assert 'type="email"' in html
        assert 'name="password"' in html
        assert 'type="password"' in html

    def test_login_fields_are_required_and_typed(self, client):
        html = client.get("/auth/login").data.decode("utf-8")

        assert "required" in field(html, "email")
        assert "required" in field(html, "password")
        assert "autocomplete" in field(html, "email")

    def test_login_form_has_a_csrf_token(self, client):
        html = client.get("/auth/login").data.decode("utf-8")

        csrf = field(html, "csrf_token")
        assert 'type="hidden"' in csrf
        # The token must actually be signed, not rendered empty.
        assert re.search(r'name="csrf_token"[^>]*value="[^"]+"', csrf)

    def test_login_form_has_a_submit_button(self, client):
        html = client.get("/auth/login").data.decode("utf-8")

        assert 'type="submit"' in html
        assert "Sign In" in html

    def test_login_rejects_a_missing_csrf_token(self, client):
        response = client.post(
            "/auth/login",
            data={"email": "ana@example.com", "password": "password123"},
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/auth/login")

    def test_login_rejects_a_forged_csrf_token(self, client):
        response = client.post(
            "/auth/login",
            data={
                "email": "ana@example.com",
                "password": "password123",
                "csrf_token": "forged:0:" + "0" * 64,
            },
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/auth/login")


class TestCreateTransactionForm:
    def test_transactions_page_renders(self, client):
        authenticate(client)
        response = client.get("/transactions/")
        # Without a stub the backend is unreachable, which the view degrades
        # from; the page itself must still render.
        assert response.status_code == 200

    def test_create_transaction_form_has_all_fields(self, client):
        authenticate(client)
        html = get_transactions_page(client)

        for name in ("type", "amount", "description", "category", "date"):
            assert f'name="{name}"' in html, f"missing {name}"

    def test_create_transaction_form_has_type_options(self, client):
        authenticate(client)
        html = get_transactions_page(client)

        for value in ("expense", "income", "investment", "savings"):
            assert f'value="{value}"' in html

    def test_create_transaction_form_has_category_datalist(self, client):
        authenticate(client)
        html = get_transactions_page(client)

        assert 'id="category-options"' in html
        for suggestion in ("General", "Food", "Transport", "Salary"):
            assert f'value="{suggestion}"' in html

    def test_category_field_is_required_and_bound_to_alpine(self, client):
        authenticate(client)
        html = get_transactions_page(client)

        category = field(html, "category")
        # The API rejects a blank category, so the field is bound to an Alpine
        # default rather than starting empty. The E2E suite asserts the
        # resulting prefill; here we only pin the binding.
        assert 'x-model="category"' in category
        assert "required" in category
        assert 'list="category-options"' in category
        assert re.search(r"category: 'General'", html)

    def test_create_transaction_form_has_a_csrf_token(self, client):
        authenticate(client)
        html = get_transactions_page(client)

        csrf = re.search(
            r"<form[^>]*action=\"/transactions/create\"[^>]*>"
            r"\s*<input type=\"hidden\" name=\"csrf_token\" value=\"([^\"]+)\"",
            html,
        )
        assert csrf, "the create form has no CSRF token"
        assert csrf.group(1)

    def test_amount_field_constrains_input(self, client):
        authenticate(client)
        html = get_transactions_page(client)

        amount = field(html, "amount")
        assert 'type="number"' in amount
        assert 'step="0.01"' in amount
        assert 'min="0.01"' in amount
        assert "required" in amount

    def test_date_field_cannot_be_in_the_future(self, client):
        authenticate(client)
        html = get_transactions_page(client)

        assert f'max="{TODAY}"' in html

    def test_delete_form_includes_a_csrf_token(self, client):
        authenticate(client)
        html = get_transactions_page(client)

        delete_form = re.search(
            r"<form[^>]*action=\"/transactions/txn-1/delete\"[^>]*>(.*?)</form>",
            html,
            re.DOTALL,
        )
        assert delete_form, "no delete form for the listed transaction"
        assert 'name="csrf_token"' in delete_form.group(1)
        # A destructive action must be confirmed before it is sent.
        assert "confirm(" in delete_form.group(0)
        # And the row must be addressable by screen readers.
        assert 'class="sr-only"' in delete_form.group(1)


class TestNavigation:
    def test_logout_form_includes_a_csrf_token(self, client):
        authenticate(client)
        html = get_dashboard(client)

        logout = re.search(
            r"<form[^>]*action=\"/auth/logout\"[^>]*>(.*?)</form>", html, re.DOTALL
        )
        assert logout, "no logout form in the navigation"
        assert 'name="csrf_token"' in logout.group(1)
        assert "Logout" in logout.group(1)

    def test_navigation_links_to_every_section(self, client):
        authenticate(client)
        html = get_dashboard(client)

        assert 'href="/dashboard/"' in html
        assert 'href="/transactions/"' in html


class TestDashboardMarkup:
    def test_dashboard_renders_one_card_per_period(self, client):
        authenticate(client)
        html = get_dashboard(client)

        assert "<h1" in html and "Dashboard" in html
        # A period with no data still renders, using its fallback label.
        assert "Today" in html
        assert "This Week" in html
        assert "This Month" in html
        assert "No data" in html

    def test_dashboard_renders_the_figures_it_was_given(self, client):
        authenticate(client)
        html = get_dashboard(client)

        assert "$50.00" in html
        assert "$100.00" in html
        assert "Sep 26 - Sep 26, 2026" in html

    def test_dashboard_defers_card_content_to_alpine(self, client):
        """Cards hydrate client-side, so the markup must hide the placeholder.

        Without this the user would see the skeleton shimmer and then the real
        figures, because the empty `<template x-if>` renders as blank space.
        """
        authenticate(client)
        html = get_dashboard(client)

        assert "[x-cloak] { display: none !important; }" in html
        assert 'x-data="{ loaded: false }"' in html
