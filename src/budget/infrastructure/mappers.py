"""Mappers from DRF schemas to budget domain entities."""

import calendar
from datetime import date, timedelta
from decimal import Decimal

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
    AnalyticsGroupBy,
    Money,
    SignedMoney,
    TransactionId,
    UserId,
)

from .schemas import (
    DRFAnalyticsResponse,
    DRFDashboardOverviewResponse,
    DRFDashboardSummaryResponse,
    DRFFundingAccountResponse,
    DRFTransactionResponse,
)

#: Number of days each period spans, used to derive a summary's end date from
#: the single start date the backend sends.
_PERIOD_LENGTH_DAYS = {"daily": 1, "weekly": 7, "monthly": 31}


def map_transaction_response(tx_data: DRFTransactionResponse) -> Transaction:
    return Transaction(
        id=TransactionId(tx_data.id),
        # DRF omits user_id from transaction payloads: ownership is implied by the
        # access token used to fetch them.
        user_id=UserId(""),
        type=tx_data.type,
        amount=Money(Decimal(tx_data.amount)),
        description=tx_data.description or "",
        category=tx_data.category,
        date=tx_data.date,
        created_at=tx_data.created_at,
        updated_at=tx_data.updated_at,
        # The backend sends this flat and null when the transaction is unlinked.
        account_id=AccountId(tx_data.account_id) if tx_data.account_id else None,
    )


def _summary_date_range(
    summary_data: DRFDashboardSummaryResponse,
) -> tuple[date, date]:
    """Resolve the (start, end) dates a summary covers.

    An individual backend summary carries a single ``date`` that is the *start*
    of its period: the day itself for ``daily``, the Monday for ``weekly``, and
    the 1st for ``monthly`` (verified against DashboardSummary rows). An
    explicit ``start_date``/``end_date`` pair, which only the list envelope
    sends, always wins. Otherwise the end is derived from the period so the UI
    shows "Sep 21 - Sep 27" for a week rather than a misleading single day.
    """
    if summary_data.start_date and summary_data.end_date:
        return summary_data.start_date, summary_data.end_date

    anchor = (
        summary_data.summary_date or summary_data.start_date or summary_data.end_date
    )
    if anchor is None:
        today = date.today()
        return today, today

    start = summary_data.start_date or anchor
    if summary_data.end_date:
        return start, summary_data.end_date

    period = (summary_data.period or "daily").strip().lower()
    if period == "monthly":
        return start, start.replace(day=calendar.monthrange(start.year, start.month)[1])
    return start, start + timedelta(days=_PERIOD_LENGTH_DAYS.get(period, 1) - 1)


def map_dashboard_summary_response(
    summary_data: DRFDashboardSummaryResponse,
) -> DashboardSummary:
    start_date, end_date = _summary_date_range(summary_data)
    return DashboardSummary(
        period=summary_data.period,
        total_income=Money(Decimal(summary_data.total_income)),
        total_expense=Money(Decimal(summary_data.total_expense)),
        # Not aggregated by the backend yet; defaults to "0.00" in the schema.
        total_investment=Money(Decimal(summary_data.total_investment)),
        total_savings=Money(Decimal(summary_data.total_savings)),
        # A balance, so it may be negative.
        net_balance=SignedMoney(Decimal(summary_data.net_balance)),
        start_date=start_date,
        end_date=end_date,
    )


def map_dashboard_overview_response(
    overview_data: DRFDashboardOverviewResponse,
) -> DashboardOverview:
    return DashboardOverview(
        today=(
            map_dashboard_summary_response(overview_data.today)
            if overview_data.today
            else None
        ),
        this_week=(
            map_dashboard_summary_response(overview_data.this_week)
            if overview_data.this_week
            else None
        ),
        this_month=(
            map_dashboard_summary_response(overview_data.this_month)
            if overview_data.this_month
            else None
        ),
    )


def transaction_to_drf_payload(transaction: Transaction) -> dict:
    """Convert a domain Transaction to a DRF-compatible payload.

    Field names follow the backend's ``CreateTransactionSerializer``: the type
    is ``transaction_type`` and ``category`` is required. ``account_id`` is
    included **only when set**: the create serializer treats it as optional, and
    an explicit ``null`` on update is rejected, so an unlinked transaction must
    omit the key rather than send an empty one.
    """
    payload = {
        "transaction_type": transaction.type.value,
        "amount": str(transaction.amount),
        "category": transaction.category,
        "description": transaction.description,
        "date": transaction.date.isoformat(),
    }
    if transaction.account_id:
        payload["account_id"] = str(transaction.account_id)
    return payload


def map_funding_account_response(
    account_data: DRFFundingAccountResponse,
) -> FundingAccount:
    """Map a DRF funding account onto the domain entity."""
    return FundingAccount(
        id=AccountId(account_data.id),
        name=account_data.name,
        account_type=account_data.account_type,
        is_active=account_data.is_active,
    )


def funding_account_to_drf_payload(account: FundingAccount) -> dict:
    """Build the ``CreateAccountSerializer`` body for a new account."""
    return {"name": account.name, "account_type": account.account_type.value}


#: The full type palette, in the order the backend's own charts use. Kept here
#: so a bucket that the backend sent without one of the keys is still
#: zero-filled instead of rendering as a gap in the stack.
_ANALYTICS_TYPES = ("income", "expense", "investment", "savings")


def _type_row(row) -> AnalyticsRow:
    """Map one ``group_by=type`` row, zero-filling absent type keys.

    The backend omits nothing in practice -- ``GetTransactionAnalytics``
    defaults every missing type to ``"0.00"`` -- but a bucket with no activity
    at all has been observed to arrive with the key missing, and a missing key
    must render as zero rather than crash the mapper.
    """
    return AnalyticsRow(
        label=row.label,
        **{name: Decimal(getattr(row, name) or "0.00") for name in _ANALYTICS_TYPES},
    )


def map_analytics_response(
    analytics_data: DRFAnalyticsResponse,
) -> TransactionAnalytics:
    """Map a DRF analytics payload onto the domain read model.

    Category rows are sorted by descending total, with the label breaking ties,
    which is the order a pie chart reads best in and is stable across calls.
    Type rows keep the backend's chronological order.
    """
    if analytics_data.group_by is AnalyticsGroupBy.CATEGORY:
        rows = [
            AnalyticsRow(
                label=row.label,
                total=Decimal(row.total or "0.00"),
            )
            for row in analytics_data.data
        ]
        rows.sort(key=lambda row: (-(row.total or Decimal("0")), row.label))
    else:
        rows = [_type_row(row) for row in analytics_data.data]

    return TransactionAnalytics(
        period=analytics_data.period,
        group_by=analytics_data.group_by,
        rows=rows,
    )
