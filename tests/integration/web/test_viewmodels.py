"""Tests for ViewModels."""

from datetime import date
from decimal import Decimal

import pytest

from src.domain.entities import (
    DashboardOverview,
    DashboardSummary,
    Transaction,
    User,
)
from src.domain.value_objects import Money, SignedMoney, TransactionType, UserId
from src.interfaces.web.viewmodels import (
    DashboardOverviewViewModel,
    DashboardSummaryViewModel,
    TransactionViewModel,
    UserViewModel,
)


def build_transaction(tx_type: TransactionType, amount: str) -> Transaction:
    return Transaction(
        id=None,
        user_id=UserId("u1"),
        type=tx_type,
        amount=Money(Decimal(amount)),
        description="Test",
        date=date(2026, 9, 26),
    )


class TestUserViewModel:
    def test_from_domain_maps_plan_display(self):
        user = User(id=UserId("u1"), email="test@example.com", name="Test", plan="pro")
        vm = UserViewModel.from_domain(user)
        assert vm.plan_display == "Pro Plan"
        assert vm.email == "test@example.com"

    def test_unknown_plan_falls_back_to_title_case(self):
        user = User(id=UserId("u1"), email="a@b.com", name="T", plan="lifetime")
        assert UserViewModel.from_domain(user).plan_display == "Lifetime"


class TestTransactionViewModel:
    def test_from_domain_formats_income_with_plus_sign(self):
        tx = Transaction(
            id=None,
            user_id=UserId("u1"),
            type=TransactionType.INCOME,
            amount=Money(Decimal("100.00")),
            description="Salary",
            date=date(2026, 9, 26),
        )
        vm = TransactionViewModel.from_domain(tx)
        assert vm.amount_formatted == "+$100.00"
        assert vm.type_display == "Income"
        assert "green" in vm.css_class

    def test_from_domain_formats_expense_with_minus_sign(self):
        tx = Transaction(
            id=None,
            user_id=UserId("u1"),
            type=TransactionType.EXPENSE,
            amount=Money(Decimal("50.00")),
            description="Groceries",
            date=date(2026, 9, 26),
        )
        vm = TransactionViewModel.from_domain(tx)
        assert vm.amount_formatted == "-$50.00"
        assert vm.type_display == "Expense"
        assert "red" in vm.css_class

    def test_from_domain_formats_date(self):
        tx = Transaction(
            id=None,
            user_id=UserId("u1"),
            type=TransactionType.EXPENSE,
            amount=Money(Decimal("50.00")),
            description="Test",
            date=date(2026, 9, 26),
        )
        vm = TransactionViewModel.from_domain(tx)
        assert vm.date_formatted == "Sep 26, 2026"

    def test_investment_and_savings_have_no_sign(self):
        for tx_type, expected, colour in (
            (TransactionType.INVESTMENT, "$30.00", "yellow"),
            (TransactionType.SAVINGS, "$20.00", "purple"),
        ):
            vm = TransactionViewModel.from_domain(
                build_transaction(
                    tx_type,
                    "30.00" if tx_type is TransactionType.INVESTMENT else "20.00",
                )
            )
            assert vm.amount_formatted == expected
            assert colour in vm.css_class

    def test_amount_is_normalised_to_two_decimals(self):
        vm = TransactionViewModel.from_domain(
            build_transaction(TransactionType.EXPENSE, "42.5")
        )
        assert vm.amount == "42.50"
        assert vm.amount_formatted == "-$42.50"

    def test_missing_id_becomes_empty_string(self):
        assert (
            TransactionViewModel.from_domain(
                build_transaction(TransactionType.EXPENSE, "1.00")
            ).id
            == ""
        )

    def test_date_iso_is_exposed_for_form_values(self):
        vm = TransactionViewModel.from_domain(
            build_transaction(TransactionType.EXPENSE, "1.00")
        )
        assert vm.date == "2026-09-26"
        assert vm.type == "expense"

    def test_category_is_exposed(self):
        tx = build_transaction(TransactionType.EXPENSE, "10.00")
        assert TransactionViewModel.from_domain(tx).category == "General"

        tx = build_transaction(TransactionType.EXPENSE, "10.00")
        tx.category = "Food"
        assert TransactionViewModel.from_domain(tx).category == "Food"


class TestDashboardSummaryViewModel:
    def test_from_domain_formats_net_balance(self):
        summary = DashboardSummary(
            period="daily",
            total_income=Money(Decimal("150.00")),
            total_expense=Money(Decimal("100.00")),
            total_investment=Money(Decimal("0.00")),
            total_savings=Money(Decimal("0.00")),
            net_balance=SignedMoney(Decimal("50.00")),
            start_date=date(2026, 9, 26),
            end_date=date(2026, 9, 26),
        )
        vm = DashboardSummaryViewModel.from_domain(summary)
        assert vm.net_balance_formatted == "$50.00"
        assert vm.period_display == "Today"
        assert vm.date_range_display == "Sep 26 - Sep 26, 2026"
        assert vm.total_income == "$150.00"

    def test_period_display_mapping(self):
        for period, expected in (
            ("daily", "Today"),
            ("weekly", "This Week"),
            ("monthly", "This Month"),
            ("yearly", "Yearly"),
        ):
            summary = DashboardSummary(
                period=period,
                total_income=Money(Decimal("0.00")),
                total_expense=Money(Decimal("0.00")),
                total_investment=Money(Decimal("0.00")),
                total_savings=Money(Decimal("0.00")),
                net_balance=SignedMoney(Decimal("0.00")),
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 30),
            )
            assert (
                DashboardSummaryViewModel.from_domain(summary).period_display
                == expected
            )

    def test_negative_net_balance_formatting(self):
        # A balance is legitimately negative, so this is a normal case now rather
        # than a branch that needs the Money invariant bypassed.
        summary = DashboardSummary(
            period="daily",
            total_income=Money(Decimal("50.00")),
            total_expense=Money(Decimal("100.00")),
            total_investment=Money(Decimal("0.00")),
            total_savings=Money(Decimal("0.00")),
            net_balance=SignedMoney(Decimal("-50.00")),
            start_date=date(2026, 9, 26),
            end_date=date(2026, 9, 26),
        )
        vm = DashboardSummaryViewModel.from_domain(summary)
        assert vm.net_balance_formatted == "-$50.00"
        assert vm.period_display == "Today"

    def test_zero_net_balance_has_no_minus_sign(self):
        summary = DashboardSummary(
            period="daily",
            total_income=Money(Decimal("0.00")),
            total_expense=Money(Decimal("0.00")),
            total_investment=Money(Decimal("0.00")),
            total_savings=Money(Decimal("0.00")),
            net_balance=SignedMoney(Decimal("0.00")),
            start_date=date(2026, 9, 26),
            end_date=date(2026, 9, 26),
        )
        assert (
            DashboardSummaryViewModel.from_domain(summary).net_balance_formatted
            == "$0.00"
        )

    def test_transaction_amounts_stay_non_negative(self):
        # Money keeps its invariant: only balances may go negative.
        with pytest.raises(ValueError, match="cannot be negative"):
            Money(Decimal("-0.01"))


class TestDashboardOverviewViewModel:
    def test_from_domain_handles_null_slots(self):
        overview = DashboardOverview(today=None, this_week=None, this_month=None)
        vm = DashboardOverviewViewModel.from_domain(overview)
        assert vm.today is None
        assert vm.this_week is None
        assert vm.this_month is None

    def test_from_domain_maps_only_populated_slots(self):
        summary = DashboardSummary(
            period="weekly",
            total_income=Money(Decimal("10.00")),
            total_expense=Money(Decimal("0.00")),
            total_investment=Money(Decimal("0.00")),
            total_savings=Money(Decimal("0.00")),
            net_balance=SignedMoney(Decimal("10.00")),
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 27),
        )
        vm = DashboardOverviewViewModel.from_domain(
            DashboardOverview(today=None, this_week=summary, this_month=None)
        )
        assert vm.today is None
        assert vm.this_week.period_display == "This Week"
        assert vm.this_month is None
