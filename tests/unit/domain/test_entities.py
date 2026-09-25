"""Tests for domain entities."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.domain.entities import Transaction, User
from src.domain.exceptions import (
    InvalidTransactionAmountError,
    InvalidTransactionDateError,
)
from src.domain.value_objects import Money, TransactionType, UserId


class TestTransaction:
    def test_validate_valid_transaction(self):
        transaction = Transaction(
            id=None,
            user_id=UserId("user-123"),
            type=TransactionType.EXPENSE,
            amount=Money(Decimal("50.00")),
            description="Groceries",
            date=date.today(),
        )
        transaction.validate(date.today())  # Should not raise

    def test_validate_zero_amount_raises_error(self):
        transaction = Transaction(
            id=None,
            user_id=UserId("user-123"),
            type=TransactionType.EXPENSE,
            amount=Money(Decimal("0.00")),
            description="Test",
            date=date.today(),
        )
        with pytest.raises(InvalidTransactionAmountError):
            transaction.validate(date.today())

    def test_validate_future_date_raises_error(self):
        future_date = date.today() + timedelta(days=1)
        transaction = Transaction(
            id=None,
            user_id=UserId("user-123"),
            type=TransactionType.EXPENSE,
            amount=Money(Decimal("50.00")),
            description="Test",
            date=future_date,
        )
        with pytest.raises(InvalidTransactionDateError):
            transaction.validate(date.today())


class TestUser:
    def test_create_user(self):
        user = User(
            id=UserId("user-123"),
            email="test@example.com",
            name="Test User",
            plan="free",
        )
        assert user.id == "user-123"
        assert user.email == "test@example.com"
        assert user.is_active is True
