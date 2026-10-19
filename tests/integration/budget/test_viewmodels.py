"""Tests for ViewModels."""

from datetime import date
from decimal import Decimal

import pytest

from src.budget.domain.entities import (
    AnalyticsRow,
    DashboardOverview,
    DashboardSummary,
    FundingAccount,
    Transaction,
    TransactionAnalytics,
)
from src.budget.domain.value_objects import (
    AccountId,
    AccountType,
    AnalyticsGroupBy,
    AnalyticsPeriod,
    Money,
    SignedMoney,
    TransactionType,
    UserId,
)
from src.budget.interfaces.viewmodels import (
    AnalyticsViewModel,
    DashboardOverviewViewModel,
    DashboardSummaryViewModel,
    FundingAccountViewModel,
    TransactionViewModel,
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


class TestFundingAccountViewModel:
    def test_from_domain_maps_the_entity(self):
        vm = FundingAccountViewModel.from_domain(
            FundingAccount(
                id=AccountId("acc-1"),
                name="Visa Rewards",
                account_type=AccountType.CREDIT_CARD,
            )
        )
        assert vm.id == "acc-1"
        assert vm.name == "Visa Rewards"
        assert vm.account_type == "credit_card"
        assert vm.account_type_display == "Credit card"
        assert vm.is_active is True

    def test_missing_id_becomes_empty_string(self):
        vm = FundingAccountViewModel.from_domain(
            FundingAccount(
                id=None,
                name="Wallet",
                account_type=AccountType.CASH,
            )
        )
        assert vm.id == ""
        assert vm.account_type_display == "Cash"


class TestAnalyticsViewModel:
    @staticmethod
    def type_result(period: AnalyticsPeriod = AnalyticsPeriod.MONTHLY):
        return TransactionAnalytics(
            period=period,
            group_by=AnalyticsGroupBy.TYPE,
            rows=[
                AnalyticsRow(
                    label="2026-08",
                    income=Decimal("100.00"),
                    expense=Decimal("40.00"),
                    investment=Decimal("0.00"),
                    savings=Decimal("5.00"),
                ),
                AnalyticsRow(
                    label="2026-09",
                    income=Decimal("200.00"),
                    expense=Decimal("60.00"),
                    investment=Decimal("10.00"),
                    savings=Decimal("25.00"),
                ),
            ],
        )

    def test_type_result_becomes_four_series(self):
        vm = AnalyticsViewModel.from_domain(self.type_result())

        assert vm.period == "monthly"
        assert vm.group_by == "type"
        assert vm.labels == ["Aug 2026", "Sep 2026"]
        assert [dataset["label"] for dataset in vm.datasets] == [
            "Income",
            "Expense",
            "Investment",
            "Savings",
        ]
        assert vm.datasets[0]["data"] == [100.0, 200.0]
        assert vm.datasets[3]["data"] == [5.0, 25.0]
        assert vm.is_empty is False

    def test_to_dict_uses_the_camel_cased_fetch_shape(self):
        payload = AnalyticsViewModel.from_domain(self.type_result()).to_dict()

        assert set(payload) == {"period", "groupBy", "labels", "datasets", "isEmpty"}
        assert payload["groupBy"] == "type"
        assert payload["isEmpty"] is False

    def test_category_result_becomes_one_slice_dataset(self):
        result = TransactionAnalytics(
            period=AnalyticsPeriod.MONTHLY,
            group_by=AnalyticsGroupBy.CATEGORY,
            rows=[
                AnalyticsRow(label="Housing", total=Decimal("30.00")),
                AnalyticsRow(label="Food", total=Decimal("10.00")),
            ],
        )
        vm = AnalyticsViewModel.from_domain(result)

        assert vm.group_by == "category"
        assert vm.labels == ["Housing", "Food"]
        assert len(vm.datasets) == 1
        dataset = vm.datasets[0]
        assert dataset["data"] == [30.0, 10.0]
        assert len(dataset["backgroundColor"]) == 2

    def test_empty_result_is_flagged(self):
        result = TransactionAnalytics(
            period=AnalyticsPeriod.MONTHLY,
            group_by=AnalyticsGroupBy.TYPE,
            rows=[],
        )
        vm = AnalyticsViewModel.from_domain(result)

        assert vm.is_empty is True
        assert vm.labels == []
        assert [dataset["data"] for dataset in vm.datasets] == [[], [], [], []]

    def test_labels_are_formatted_per_period(self):
        for period, label, expected in (
            (AnalyticsPeriod.DAILY, "2026-09-26", "Sep 26"),
            (AnalyticsPeriod.WEEKLY, "2026-W39", "Week 39 2026"),
            (AnalyticsPeriod.MONTHLY, "2026-09", "Sep 2026"),
        ):
            result = TransactionAnalytics(
                period=period,
                group_by=AnalyticsGroupBy.TYPE,
                rows=[
                    AnalyticsRow(
                        label=label,
                        income=Decimal("0.00"),
                        expense=Decimal("0.00"),
                        investment=Decimal("0.00"),
                        savings=Decimal("0.00"),
                    )
                ],
            )
            assert AnalyticsViewModel.from_domain(result).labels == [expected]

    def test_an_unparsable_label_passes_through(self):
        result = TransactionAnalytics(
            period=AnalyticsPeriod.MONTHLY,
            group_by=AnalyticsGroupBy.TYPE,
            rows=[
                AnalyticsRow(
                    label="Q3",
                    income=Decimal("0.00"),
                    expense=Decimal("0.00"),
                    investment=Decimal("0.00"),
                    savings=Decimal("0.00"),
                )
            ],
        )
        assert AnalyticsViewModel.from_domain(result).labels == ["Q3"]
