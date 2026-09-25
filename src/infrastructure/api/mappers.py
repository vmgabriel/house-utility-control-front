"""Mappers from DRF Pydantic schemas to domain entities."""

from decimal import Decimal

from src.domain.entities import (
    DashboardOverview,
    DashboardSummary,
    Transaction,
    User,
)
from src.domain.value_objects import Money, TransactionId, UserId

from .schemas import (
    DRFDashboardOverviewResponse,
    DRFDashboardSummaryResponse,
    DRFTransactionResponse,
    DRFUserResponse,
)


def map_user_response(user_data: DRFUserResponse) -> User:
    return User(
        id=UserId(user_data.id),
        email=user_data.email,
        name=user_data.name,
        plan=user_data.plan,
        is_active=user_data.is_active,
    )


def map_transaction_response(tx_data: DRFTransactionResponse) -> Transaction:
    return Transaction(
        id=TransactionId(tx_data.id),
        # DRF omits user_id from transaction payloads: ownership is implied by the
        # access token used to fetch them.
        user_id=UserId(""),
        type=tx_data.type,
        amount=Money(Decimal(tx_data.amount)),
        description=tx_data.description,
        date=tx_data.date,
        created_at=tx_data.created_at,
        updated_at=tx_data.updated_at,
    )


def map_dashboard_summary_response(
    summary_data: DRFDashboardSummaryResponse,
) -> DashboardSummary:
    return DashboardSummary(
        period=summary_data.period,
        total_income=Money(Decimal(summary_data.total_income)),
        total_expense=Money(Decimal(summary_data.total_expense)),
        total_investment=Money(Decimal(summary_data.total_investment)),
        total_savings=Money(Decimal(summary_data.total_savings)),
        net_balance=Money(Decimal(summary_data.net_balance)),
        start_date=summary_data.start_date,
        end_date=summary_data.end_date,
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
    """Convert a domain Transaction to a DRF-compatible payload."""
    return {
        "type": transaction.type.value,
        # `str(Money)` normalises to 2 decimal places for the DRF decimal field.
        "amount": str(transaction.amount),
        "description": transaction.description,
        "date": transaction.date.isoformat(),
    }
