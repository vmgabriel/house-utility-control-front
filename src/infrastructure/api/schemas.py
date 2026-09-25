"""Pydantic schemas for DRF API response validation."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from src.domain.value_objects import TransactionType


class DRFTokenResponse(BaseModel):
    """Response from login/refresh endpoints."""

    model_config = ConfigDict(extra="allow")
    access: str
    refresh: str


class DRFUserResponse(BaseModel):
    """Response from /users/me/ endpoint."""

    model_config = ConfigDict(extra="allow")
    id: str
    email: str
    name: str
    plan: str
    is_active: bool = True


class DRFTransactionResponse(BaseModel):
    """Response from transaction endpoints."""

    model_config = ConfigDict(extra="allow")
    id: str
    type: TransactionType
    amount: str  # JSON string representation of Decimal
    description: str
    date: date
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DRFPaginatedTransactionsResponse(BaseModel):
    """Paginated response from GET /transactions/."""

    model_config = ConfigDict(extra="allow")
    count: int
    next: str | None = None
    previous: str | None = None
    results: list[DRFTransactionResponse]


class DRFDashboardSummaryResponse(BaseModel):
    """Response for a single dashboard summary period."""

    model_config = ConfigDict(extra="allow")
    period: str
    total_income: str
    total_expense: str
    total_investment: str
    total_savings: str
    net_balance: str
    start_date: date
    end_date: date


class DRFDashboardOverviewResponse(BaseModel):
    """Response from /dashboard/overview/."""

    model_config = ConfigDict(extra="allow")
    today: DRFDashboardSummaryResponse | None = None
    this_week: DRFDashboardSummaryResponse | None = None
    this_month: DRFDashboardSummaryResponse | None = None
