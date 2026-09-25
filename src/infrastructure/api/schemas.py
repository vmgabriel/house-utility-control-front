"""Pydantic schemas for DRF API response validation."""

from datetime import date, datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from src.domain.entities import DEFAULT_CATEGORY
from src.domain.value_objects import TransactionType


class DRFTokenResponse(BaseModel):
    """Response from login/refresh endpoints."""

    model_config = ConfigDict(extra="allow")
    access: str
    refresh: str


class DRFUserResponse(BaseModel):
    """Response from /users/me/ endpoint."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    email: str
    # The DRF backend exposes the display name as `full_name`
    # (apps.users.interfaces.serializers.UserSerializer). `name` is accepted as
    # well so the domain field stays neutral; the backend field wins.
    name: str = Field(validation_alias=AliasChoices("full_name", "name"))
    plan: str
    is_active: bool = True
    is_staff: bool = False
    is_superuser: bool = False


class DRFTransactionResponse(BaseModel):
    """Response from transaction endpoints."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    # The backend names this field `transaction_type`
    # (apps.transactions.interfaces.serializers.TransactionSerializer).
    type: TransactionType = Field(
        validation_alias=AliasChoices("transaction_type", "type")
    )
    amount: str  # JSON string representation of Decimal
    category: str = DEFAULT_CATEGORY
    # The backend permits a null description.
    description: str | None = None
    date: date
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DRFPaginatedTransactionsResponse(BaseModel):
    """Paginated response from GET /transactions/.

    The backend paginates with ``count``/``page``/``page_size`` and exposes no
    cursor links, so ``next``/``previous`` stay optional.
    """

    model_config = ConfigDict(extra="allow")
    count: int
    next: str | None = None
    previous: str | None = None
    results: list[DRFTransactionResponse]


class DRFDashboardSummaryResponse(BaseModel):
    """Response for a single dashboard summary period.

    The backend aggregates income and expenses only, and identifies a summary
    with a single ``date``. Investment and savings totals and the start/end
    range are therefore optional, so the UI degrades to $0.00 instead of
    failing validation until the backend grows those totals.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    period: str
    total_income: str
    total_expense: str
    total_investment: str = "0.00"
    total_savings: str = "0.00"
    # A single summary carries one `date`; start_date/end_date only appear on
    # the list envelope. The field is named `summary_date` internally so it does
    # not shadow the `date` type used in its own annotation.
    summary_date: date | None = Field(
        default=None, validation_alias=AliasChoices("date", "summary_date")
    )
    start_date: date | None = None
    end_date: date | None = None
    net_balance: str = "0.00"


class DRFDashboardOverviewResponse(BaseModel):
    """Response from /dashboard/overview/."""

    model_config = ConfigDict(extra="allow")
    today: DRFDashboardSummaryResponse | None = None
    this_week: DRFDashboardSummaryResponse | None = None
    this_month: DRFDashboardSummaryResponse | None = None
