"""ViewModels for mapping domain entities to template context.

ViewModels are the only place where domain objects are turned into strings,
labels and CSS classes. Templates must never reach into domain objects
directly, which keeps presentation concerns out of the inner layers and makes
every rendering decision unit-testable without a request context.
"""

from dataclasses import dataclass

from src.budget.domain.entities import DashboardOverview, DashboardSummary, Transaction
from src.budget.domain.value_objects import TransactionType


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
