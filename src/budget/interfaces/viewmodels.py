"""ViewModels for mapping domain entities to template context.

ViewModels are the only place where domain objects are turned into strings,
labels and CSS classes. Templates must never reach into domain objects
directly, which keeps presentation concerns out of the inner layers and makes
every rendering decision unit-testable without a request context.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.budget.domain.entities import (
    DashboardOverview,
    DashboardSummary,
    FundingAccount,
    Transaction,
    TransactionAnalytics,
)
from src.budget.domain.value_objects import (
    AnalyticsGroupBy,
    AnalyticsPeriod,
    TransactionType,
)


@dataclass
class TransactionViewModel:
    """ViewModel for transaction data in templates."""

    id: str
    type: str
    type_display: str
    amount: str
    amount_formatted: str
    description: str
    category: str
    date: str
    date_formatted: str
    css_class: str
    icon: str

    @classmethod
    def from_domain(cls, transaction: Transaction) -> "TransactionViewModel":
        type_config = {
            TransactionType.INCOME: {
                "display": "Income",
                "css": "text-green-600 bg-green-50",
                "icon": "arrow-down",
            },
            TransactionType.EXPENSE: {
                "display": "Expense",
                "css": "text-red-600 bg-red-50",
                "icon": "arrow-up",
            },
            TransactionType.INVESTMENT: {
                "display": "Investment",
                "css": "text-yellow-600 bg-yellow-50",
                "icon": "trending-up",
            },
            TransactionType.SAVINGS: {
                "display": "Savings",
                "css": "text-purple-600 bg-purple-50",
                "icon": "piggy-bank",
            },
        }

        # `Transaction.type` is always a valid member, so the fallback is only
        # a guard against a future enum member being added without a config.
        config = type_config.get(transaction.type, type_config[TransactionType.EXPENSE])

        # Format amount with sign
        amount_str = str(transaction.amount)
        if transaction.type == TransactionType.INCOME:
            amount_formatted = f"+${amount_str}"
        elif transaction.type == TransactionType.EXPENSE:
            amount_formatted = f"-${amount_str}"
        else:
            amount_formatted = f"${amount_str}"

        # Format date
        date_formatted = transaction.date.strftime("%b %d, %Y")

        return cls(
            id=transaction.id or "",
            type=transaction.type.value,
            type_display=config["display"],
            amount=amount_str,
            amount_formatted=amount_formatted,
            description=transaction.description,
            category=transaction.category,
            date=transaction.date.isoformat(),
            date_formatted=date_formatted,
            css_class=config["css"],
            icon=config["icon"],
        )


@dataclass
class DashboardSummaryViewModel:
    """ViewModel for dashboard summary in templates."""

    period: str
    period_display: str
    total_income: str
    total_expense: str
    total_investment: str
    total_savings: str
    net_balance: str
    net_balance_formatted: str
    start_date: str
    end_date: str
    date_range_display: str

    @classmethod
    def from_domain(cls, summary: DashboardSummary) -> "DashboardSummaryViewModel":
        period_display = {
            "daily": "Today",
            "weekly": "This Week",
            "monthly": "This Month",
        }.get(summary.period, summary.period.title())

        # `Money` rejects negative amounts, so a negative net balance cannot
        # currently reach this layer; the branch is kept so the formatting stays
        # correct if the domain ever models a signed balance.
        net_balance_formatted = f"${summary.net_balance}"
        if summary.net_balance.amount < 0:
            net_balance_formatted = f"-${abs(summary.net_balance.amount)}"

        date_range_display = (
            f"{summary.start_date.strftime('%b %d')} - "
            f"{summary.end_date.strftime('%b %d, %Y')}"
        )

        return cls(
            period=summary.period,
            period_display=period_display,
            total_income=f"${summary.total_income}",
            total_expense=f"${summary.total_expense}",
            total_investment=f"${summary.total_investment}",
            total_savings=f"${summary.total_savings}",
            net_balance=f"${summary.net_balance}",
            net_balance_formatted=net_balance_formatted,
            start_date=summary.start_date.isoformat(),
            end_date=summary.end_date.isoformat(),
            date_range_display=date_range_display,
        )


@dataclass
class DashboardOverviewViewModel:
    """ViewModel for dashboard overview in templates."""

    today: DashboardSummaryViewModel | None
    this_week: DashboardSummaryViewModel | None
    this_month: DashboardSummaryViewModel | None

    @classmethod
    def from_domain(cls, overview: DashboardOverview) -> "DashboardOverviewViewModel":
        return cls(
            today=(
                DashboardSummaryViewModel.from_domain(overview.today)
                if overview.today
                else None
            ),
            this_week=(
                DashboardSummaryViewModel.from_domain(overview.this_week)
                if overview.this_week
                else None
            ),
            this_month=(
                DashboardSummaryViewModel.from_domain(overview.this_month)
                if overview.this_month
                else None
            ),
        )


@dataclass
class FundingAccountViewModel:
    """ViewModel for a funding account row and the create-form dropdown."""

    id: str
    name: str
    account_type: str
    account_type_display: str
    is_active: bool

    @classmethod
    def from_domain(cls, account: FundingAccount) -> "FundingAccountViewModel":
        return cls(
            id=str(account.id) if account.id else "",
            name=account.name,
            account_type=account.account_type.value,
            account_type_display=account.account_type.label,
            is_active=account.is_active,
        )


#: Chart series for a ``group_by=type`` result, in the same order and with the
#: same colours the dashboard already uses, so the bar chart reads as the same
#: palette as the summary cards.
_TYPE_SERIES = (
    ("income", "Income", "rgba(34, 197, 94, 0.6)"),
    ("expense", "Expense", "rgba(239, 68, 68, 0.6)"),
    ("investment", "Investment", "rgba(234, 179, 8, 0.6)"),
    ("savings", "Savings", "rgba(168, 85, 247, 0.6)"),
)

#: Slice colours for the category pie chart. Chart.js cycles this list itself
#: when it is shorter than the dataset, so it only needs enough entries to
#: avoid two adjacent slices sharing a colour on a typical chart.
_CATEGORY_PALETTE = (
    "rgba(59, 130, 246, 0.7)",
    "rgba(16, 185, 129, 0.7)",
    "rgba(245, 158, 11, 0.7)",
    "rgba(239, 68, 68, 0.7)",
    "rgba(139, 92, 246, 0.7)",
    "rgba(236, 72, 153, 0.7)",
    "rgba(20, 184, 166, 0.7)",
    "rgba(107, 114, 128, 0.7)",
)


def _format_bucket_label(label: str, period: str) -> str:
    """Turn one backend bucket label into something a human reads.

    The backend labels buckets ``2026-09-26`` (daily), ``2026-W39`` (weekly)
    and ``2026-09`` (monthly). Anything that does not parse is passed through
    unchanged rather than dropped, so an unrecognised label shows up in the
    chart instead of silently vanishing from it.
    """
    try:
        if period == AnalyticsPeriod.DAILY.value:
            return datetime.strptime(label, "%Y-%m-%d").strftime("%b %d")
        if period == AnalyticsPeriod.WEEKLY.value:
            # strptime refuses `%G-W%V` without a weekday directive, so pin the
            # Monday and read the ISO year and week back out.
            parsed = datetime.strptime(f"{label}-1", "%G-W%V-%u")
            iso_year, iso_week, _ = parsed.isocalendar()
            return f"Week {iso_week:02d} {iso_year}"
        if period == AnalyticsPeriod.MONTHLY.value:
            return datetime.strptime(label, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return label
    return label


def _number(value: Decimal | None) -> float:
    """Render a decimal amount as the number Chart.js draws.

    Deliberately a float: Chart.js does not plot decimal strings, and converting
    here keeps the JavaScript in the template free of parsing logic.
    """
    return float(value or Decimal("0"))


@dataclass
class AnalyticsViewModel:
    """Chart.js-ready projection of one analytics result.

    The template receives ``labels`` and ``datasets`` already in the shape
    Chart.js expects, so the Alpine component only has to hand them to
    ``new Chart(...)``. No decimal string ever reaches the browser.
    """

    period: str
    group_by: str
    labels: list[str]
    datasets: list[dict]
    is_empty: bool

    @classmethod
    def from_domain(cls, analytics: TransactionAnalytics) -> "AnalyticsViewModel":
        if analytics.group_by is AnalyticsGroupBy.CATEGORY:
            labels = [row.label for row in analytics.rows]
            values = [_number(row.total) for row in analytics.rows]
            datasets = [
                {
                    "label": "Expenses by category",
                    "data": values,
                    "backgroundColor": [
                        _CATEGORY_PALETTE[index % len(_CATEGORY_PALETTE)]
                        for index in range(len(values))
                    ],
                }
            ]
        else:
            labels = [
                _format_bucket_label(row.label, analytics.period.value)
                for row in analytics.rows
            ]
            datasets = [
                {
                    "label": display,
                    "data": [_number(getattr(row, field)) for row in analytics.rows],
                    "backgroundColor": colour,
                }
                for field, display, colour in _TYPE_SERIES
            ]

        return cls(
            period=analytics.period.value,
            group_by=analytics.group_by.value,
            labels=labels,
            datasets=datasets,
            is_empty=not analytics.rows,
        )

    def to_dict(self) -> dict:
        """Serialise for the fetch endpoint consumed by the chart script."""
        return {
            "period": self.period,
            "groupBy": self.group_by,
            "labels": self.labels,
            "datasets": self.datasets,
            "isEmpty": self.is_empty,
        }
