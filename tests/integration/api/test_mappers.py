"""Integration tests for DRF <-> domain mappers."""

from datetime import date, datetime
from decimal import Decimal

from src.domain.entities import DashboardOverview, Transaction
from src.domain.value_objects import Money, TransactionId, TransactionType
from src.infrastructure.api.mappers import (
    map_dashboard_overview_response,
    map_dashboard_summary_response,
    map_transaction_response,
    map_user_response,
    transaction_to_drf_payload,
)
from src.infrastructure.api.schemas import (
    DRFDashboardOverviewResponse,
    DRFDashboardSummaryResponse,
    DRFTokenResponse,
    DRFTransactionResponse,
    DRFUserResponse,
)


class TestMappingToDomain:
    def test_map_user_response(self):
        user = map_user_response(
            DRFUserResponse(
                id="u1", email="a@b.com", name="Ana", plan="pro", is_active=False
            )
        )
        assert (user.id, user.email, user.name, user.plan) == (
            "u1",
            "a@b.com",
            "Ana",
            "pro",
        )
        assert user.is_active is False

    def test_user_is_active_defaults_to_true(self):
        user = map_user_response(
            DRFUserResponse(id="u1", email="a@b.com", name="Ana", plan="free")
        )
        assert user.is_active is True

    def test_map_transaction_response_parses_money_and_dates(self):
        tx = map_transaction_response(
            DRFTransactionResponse(
                id="t1",
                type="expense",
                amount="42.50",
                description="Coffee",
                date="2026-09-26",
                created_at="2026-09-26T10:00:00Z",
                updated_at="2026-09-26T11:30:00Z",
            )
        )
        assert isinstance(tx, Transaction)
        assert tx.type is TransactionType.EXPENSE
        assert tx.amount == Money(Decimal("42.50"))
        assert tx.date == date(2026, 9, 26)
        assert tx.created_at == datetime.fromisoformat("2026-09-26T10:00:00+00:00")

    def test_map_transaction_response_handles_missing_timestamps(self):
        tx = map_transaction_response(
            DRFTransactionResponse(
                id="t1",
                type="income",
                amount="10.00",
                description="Refund",
                date="2026-09-26",
            )
        )
        assert tx.created_at is None
        assert tx.updated_at is None

    def test_map_dashboard_summary_response(self):
        summary = map_dashboard_summary_response(
            DRFDashboardSummaryResponse(
                period="weekly",
                total_income="100.00",
                total_expense="50.00",
                total_investment="10.00",
                total_savings="5.00",
                net_balance="35.00",
                start_date="2026-09-21",
                end_date="2026-09-27",
            )
        )
        assert summary.period == "weekly"
        assert str(summary.net_balance) == "35.00"
        assert summary.start_date == date(2026, 9, 21)

    def test_map_dashboard_overview_response_with_all_null(self):
        overview = map_dashboard_overview_response(
            DRFDashboardOverviewResponse.model_validate(
                {"today": None, "this_week": None, "this_month": None}
            )
        )
        assert isinstance(overview, DashboardOverview)
        assert (overview.today, overview.this_week, overview.this_month) == (
            None,
            None,
            None,
        )


class TestMappingFromDomain:
    def test_transaction_to_drf_payload_normalises_amount(self):
        tx = Transaction(
            id=None,
            user_id="u1",
            type=TransactionType.EXPENSE,
            amount=Money(Decimal("42.5")),  # single decimal place
            description="Coffee",
            date=date(2026, 9, 26),
        )
        payload = transaction_to_drf_payload(tx)

        assert payload == {
            "type": "expense",
            "amount": "42.50",
            "description": "Coffee",
            "date": "2026-09-26",
        }
        assert isinstance(payload["amount"], str)

    def test_payload_is_a_create_payload_without_id(self):
        # `transaction_to_drf_payload` targets POST/PATCH bodies, so it must not
        # send an id; DRF assigns it. That also means it cannot be validated
        # against the *response* schema, which requires one.
        tx = Transaction(
            id=TransactionId("t9"),
            user_id="u1",
            type=TransactionType.SAVINGS,
            amount=Money(Decimal("7.05")),
            description="Emergency fund",
            date=date(2026, 1, 31),
        )
        payload = transaction_to_drf_payload(tx)
        assert "id" not in payload

        # With the server-assigned id, the same fields parse as a response.
        parsed = DRFTransactionResponse.model_validate({"id": "t9", **payload})
        assert parsed.amount == "7.05"
        assert parsed.date == date(2026, 1, 31)


class TestSchemaTolerance:
    def test_unknown_drf_fields_are_ignored(self):
        # DRF is free to add fields; the adapter must not break.
        schema = DRFTokenResponse.model_validate(
            {"access": "a", "refresh": "r", "jti": "abc", "expires_in": 3600}
        )
        assert (schema.access, schema.refresh) == ("a", "r")

    def test_invalid_amount_rejected(self):
        import pydantic
        import pytest

        with pytest.raises(pydantic.ValidationError):
            DRFTransactionResponse(
                id="t1",
                type="nonsense",
                amount="1.00",
                description="x",
                date="2026-09-26",
            )
