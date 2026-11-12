"""Tests for the credit-card payment use case."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.budget.application.use_cases.payments import PayCreditCardUseCase
from src.budget.domain.entities import CreditCardPayment
from src.budget.domain.exceptions import InvalidPaymentAmountError
from src.budget.domain.value_objects import AccountId, Money


def payment() -> CreditCardPayment:
    return CreditCardPayment(
        detail="Credit card payment completed.",
        account_id=AccountId("card-1"),
        funding_account_id=AccountId("debit-1"),
        amount=Money(Decimal("125.50")),
    )


class TestPayCreditCardUseCase:
    @pytest.mark.asyncio
    async def test_normalizes_the_amount_and_calls_the_repository(self):
        repository = AsyncMock()
        repository.pay_credit_card.return_value = payment()
        use_case = PayCreditCardUseCase(payment_repository=repository)

        result = await use_case.execute("access-token", "card-1", "debit-1", "125.5")

        repository.pay_credit_card.assert_awaited_once_with(
            "access-token", "card-1", "debit-1", "125.50"
        )
        assert result == payment()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("amount", ["", "   ", "not-a-number"])
    async def test_rejects_a_missing_or_unparseable_amount(self, amount):
        repository = AsyncMock()
        use_case = PayCreditCardUseCase(payment_repository=repository)

        with pytest.raises(InvalidPaymentAmountError):
            await use_case.execute("access-token", "card-1", "debit-1", amount)

        repository.pay_credit_card.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("amount", ["0", "-10.00", "NaN", "Infinity"])
    async def test_rejects_a_non_positive_or_non_finite_amount(self, amount):
        repository = AsyncMock()
        use_case = PayCreditCardUseCase(payment_repository=repository)

        with pytest.raises(InvalidPaymentAmountError):
            await use_case.execute("access-token", "card-1", "debit-1", amount)

        repository.pay_credit_card.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_more_than_two_decimal_places(self):
        repository = AsyncMock()
        use_case = PayCreditCardUseCase(payment_repository=repository)

        with pytest.raises(InvalidPaymentAmountError):
            await use_case.execute("access-token", "card-1", "debit-1", "10.001")

        repository.pay_credit_card.assert_not_awaited()
