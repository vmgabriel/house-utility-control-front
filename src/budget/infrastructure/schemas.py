"""Pydantic schemas for the budget DRF endpoints.

Hand-written and authoritative over the DRF OpenAPI schema, which declares
several fields required and non-nullable that the live API returns as `null`.
See AGENTS.md, rule 7.
"""

from datetime import date, datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from src.budget.domain.entities import DEFAULT_CATEGORY
from src.budget.domain.value_objects import (
    AccountType,
    AnalyticsGroupBy,
    AnalyticsPeriod,
    TransactionType,
)


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
    # Flat on purpose in the backend serializer: no nested object to
    # dereference, and both are null until the transaction is linked.
    account_id: str | None = None
    account_name: str | None = None


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
    """Response from /dashboard/overview/.

    Each period is optional because the backend sends `null` for a window the
    user has no transactions in, which is every window on a freshly registered
    account. Declaring these required, as the OpenAPI schema does, would turn
    the normal empty dashboard into a validation error.
    """

    model_config = ConfigDict(extra="allow")
    today: DRFDashboardSummaryResponse | None = None
    this_week: DRFDashboardSummaryResponse | None = None
    this_month: DRFDashboardSummaryResponse | None = None


class DRFFundingAccountResponse(BaseModel):
    """Response from the funding-account endpoints.

    ``created_at``/``updated_at`` are optional because the create response and
    the list response are validated by the same schema and only the list is
    guaranteed to carry them; the UI never reads either.
    """

    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    account_type: AccountType
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DRFFundingAccountListResponse(BaseModel):
    """Envelope from GET /transactions/accounts/.

    Not paginated like transactions: the backend answers ``count`` plus a
    ``results`` list, with no page fields.
    """

    model_config = ConfigDict(extra="allow")
    count: int
    results: list[DRFFundingAccountResponse]


class DRFAnalyticsRowResponse(BaseModel):
    """One analytics row.

    Type rows send income/expense/investment/savings and omit ``total``;
    category rows send only ``total``. Both are declared optional so one schema
    validates either shape, matching the backend's ``AnalyticsRowSerializer``.
    """

    model_config = ConfigDict(extra="allow")
    label: str
    income: str | None = None
    expense: str | None = None
    investment: str | None = None
    savings: str | None = None
    total: str | None = None


class DRFAnalyticsResponse(BaseModel):
    """Response from GET /transactions/analytics/."""

    model_config = ConfigDict(extra="allow")
    period: AnalyticsPeriod
    group_by: AnalyticsGroupBy
    data: list[DRFAnalyticsRowResponse]
