"""Integration tests for the rentals web layer.

These exercise the full request path -- Flask routing, CSRF, the use cases, the
httpx adapter, and the mappers -- with only the DRF HTTP boundary mocked by
`respx`. That is deliberate: the point is to prove the layers are wired to each
other correctly, and mocking at the socket is the only level that still tests
the adapter's URL construction, status handling, and payload shapes together.

The rentals endpoints are not reachable on the running DRF server yet (it
predates the rentals app), so every response body here is hand-built from the
backend's serializers and models.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from src.infrastructure.auth.jwt_cookie_manager import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
)
from src.interfaces.web.app import create_app
from src.rentals.application.wiring import build_rentals_use_cases

BASE = "http://localhost:8000/api/v1"
TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

APARTMENT_ID = uuid4()
HOUSE_ID = uuid4()
USER_ID = uuid4()

ACCESS_TOKEN = "rentals-access-token"
REFRESH_TOKEN = "rentals-refresh-token"


class FrozenClock:
    """Deterministic clock so the "not in the future" rules are testable."""

    def now(self) -> datetime:
        return NOW

    def today(self) -> date:
        return TODAY


def house_payload(**overrides):
    payload = {
        "id": str(HOUSE_ID),
        "owner_id": str(USER_ID),
        "name": "Sobrado House",
        "street": "Rua das Flores",
        "city": "Sao Paulo",
        "state": "SP",
        "country": "BR",
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "2026-09-02T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def apartment_payload(**overrides):
    payload = {
        "id": str(APARTMENT_ID),
        "house_id": str(HOUSE_ID),
        "number": "101",
        "floor": 2,
        "monthly_rent": "2500.00",
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "2026-09-02T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def reading_payload(**overrides):
    payload = {
        "id": str(uuid4()),
        "apartment_id": str(APARTMENT_ID),
        "utility_type": "WATER",
        "reading_date": "2026-10-01",
        "current_reading": "150.50",
        "previous_reading": "120.00",
        "consumption": "30.50",
        "unit_cost": "3.2567",
        "total_cost": "99.33",
        "created_at": "2026-10-01T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def payment_payload(**overrides):
    payload = {
        "id": str(uuid4()),
        "apartment_id": str(APARTMENT_ID),
        "payment_date": "2026-10-03",
        "amount": "2500.00",
        "status": "PAID",
        "notes": None,
        "created_at": "2026-10-03T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def document_payload(**overrides):
    payload = {
        "id": str(uuid4()),
        "apartment_id": str(APARTMENT_ID),
        "document_type": "LEASE_CONTRACT",
        "file_url": "https://cloud.example.com/s/lease.pdf",
        "description": None,
        "uploaded_at": "2026-10-01T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def summary_payload(**overrides):
    payload = {
        "apartment_id": str(APARTMENT_ID),
        "year": TODAY.year,
        "month": TODAY.month,
        "total_paid": "2500.00",
        "outstanding_balance": "0.00",
        "payment_count": 1,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def app():
    """An app with a frozen clock and Nextcloud configured."""
    application = create_app()
    application.clock = FrozenClock()
    application.config.update(
        TESTING=True,
        # Public share (drop folder): a base URL and a token, no credentials.
        # A same-origin path forwarded to Nextcloud by the reverse proxy.
        # No credential lives in the application.
        NEXTCLOUD_UPLOAD_PATH="/nextcloud-dav/rentals",
    )
    # Rebuild the rentals use cases against the frozen clock. The extension
    # already holds a fully-wired `DrfRentalsClient`, so it is used directly --
    # wrapping it again would nest adapters and break every delegation.
    build_rentals_use_cases(
        application.extensions["rentals_api_client"], clock=application.clock
    ).attach_to(application)
    return application


@pytest.fixture
def authed_client(app):
    """A client carrying valid JWT cookies and a matching session.

    Cookie names come from `src/infrastructure/auth/jwt_cookie_manager.py` rather
    than being hardcoded here, so a rename upstream cannot silently turn every
    test into a silent 302-to-login pass.
    """
    with app.test_client() as test_client:
        test_client.set_cookie(ACCESS_COOKIE, ACCESS_TOKEN)
        test_client.set_cookie(REFRESH_COOKIE, REFRESH_TOKEN)
        with test_client.session_transaction() as session:
            session["user_id"] = str(USER_ID)
            session["is_staff"] = False
        yield test_client
