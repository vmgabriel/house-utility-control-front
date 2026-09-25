"""Tests for domain value objects."""

from decimal import Decimal

import pytest

from src.domain.value_objects import Money, TransactionType


class TestMoney:
    def test_create_valid_money(self):
        money = Money(Decimal("100.50"))
        assert money.amount == Decimal("100.50")

    def test_create_money_from_string(self):
        money = Money.from_string("42.50")
        assert money.amount == Decimal("42.50")

    def test_money_cannot_be_negative(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            Money(Decimal("-10.00"))

    def test_money_cannot_have_more_than_2_decimals(self):
        with pytest.raises(ValueError, match="more than 2 decimal places"):
            Money(Decimal("10.123"))

    def test_money_string_representation(self):
        money = Money(Decimal("42.5"))
        assert str(money) == "42.50"


class TestTransactionType:
    def test_transaction_types(self):
        assert TransactionType.INCOME.value == "income"
        assert TransactionType.EXPENSE.value == "expense"
        assert TransactionType.INVESTMENT.value == "investment"
        assert TransactionType.SAVINGS.value == "savings"
