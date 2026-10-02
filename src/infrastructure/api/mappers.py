"""Mappers from DRF Pydantic schemas to domain entities."""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from src.domain.entities import (
    DashboardOverview,
    DashboardSummary,
    Transaction,
    User,
)
from src.domain.value_objects import Money, SignedMoney, TransactionId, UserId

from .schemas import (
    DRFDashboardOverviewResponse,
    DRFDashboardSummaryResponse,
    DRFTransactionResponse,
    DRFUserResponse,
)

#: Number of days each period spans, used to derive a summary's end date from
#: the single start date the backend sends.
_PERIOD_LENGTH_DAYS = {"daily": 1, "weekly": 7, "monthly": 31}


def map_user_response(user_data: DRFUserResponse) -> User:
    return User(
        id=UserId(user_data.id),
        email=user_data.email,
        name=user_data.name,
        plan=user_data.plan,
        is_active=user_data.is_active,
        is_staff=user_data.is_staff,
    )


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
    is ``transaction_type`` and ``category`` is required.
    """
    return {
        "transaction_type": transaction.type.value,
        "amount": str(transaction.amount),
        "category": transaction.category,
        "description": transaction.description,
        "date": transaction.date.isoformat(),
    }
