"""Integration tests for the credit-card payment repository."""

import json

import httpx
import pytest
import respx

from src.budget.infrastructure.repositories.credit_card_payment_repository import (
    DRFCreditCardPaymentRepository,
)
from src.shared.http.drf_client import (
    DRFAPIClient,
    NotFoundError,
    ValidationError,
)

BASE = "http://testserver/api/v1"
PAY_URL = f"{BASE}/accounts/card-1/pay-credit-card/"


@pytest.fixture
def payment_repository():
    api_client = DRFAPIClient(base_url=BASE)
    return DRFCreditCardPaymentRepository(api_client)


class TestPayCreditCard:
    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_the_payload_and_maps_the_confirmation(
        self, payment_repository
    ):
        route = respx.post(PAY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "detail": "Credit card payment completed.",
                    "account_id": "card-1",
                    "funding_account_id": "debit-1",
                    "amount": "125.50",
                },
            )
        )

        result = await payment_repository.pay_credit_card(
            "access-token", "card-1", "debit-1", "125.50"
        )

        body = json.loads(route.calls[0].request.content)
        assert body == {"funding_account_id": "debit-1", "amount": "125.50"}
        assert route.calls[0].request.headers["Authorization"] == "Bearer access-token"

        assert result.detail == "Credit card payment completed."
        assert result.account_id == "card-1"
        assert result.funding_account_id == "debit-1"
        assert str(result.amount) == "125.50"

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_foreign_or_missing_card_raises_not_found(self, payment_repository):
        respx.post(PAY_URL).mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )

        with pytest.raises(NotFoundError):
            await payment_repository.pay_credit_card(
                "access-token", "card-1", "debit-1", "10.00"
            )

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_rejected_payment_raises_a_validation_error(
        self, payment_repository
    ):
        respx.post(PAY_URL).mock(
            return_value=httpx.Response(
                400, json={"detail": "Account is not a credit card."}
            )
        )

        with pytest.raises(ValidationError):
            await payment_repository.pay_credit_card(
                "access-token", "card-1", "debit-1", "10.00"
            )
