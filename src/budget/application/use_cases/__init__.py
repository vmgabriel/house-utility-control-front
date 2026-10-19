"""Budget use cases: single entry points to the application layer.

Each use case is a small, framework-free object exposing a single
``execute`` coroutine. Use cases depend on domain ports (protocols) only;
the concrete adapters live in the infrastructure layer.
"""

from .accounts import CreateFundingAccountUseCase, GetFundingAccountsUseCase
from .analytics import GetTransactionAnalyticsUseCase
from .dashboard import (
    GetDashboardOverviewUseCase,
    GetDashboardSummariesUseCase,
)
from .transactions import (
    CreateTransactionUseCase,
    DeleteTransactionUseCase,
    FetchTransactionsUseCase,
    GetTransactionUseCase,
    UpdateTransactionUseCase,
)

__all__ = [
    "FetchTransactionsUseCase",
    "GetTransactionUseCase",
    "CreateTransactionUseCase",
    "UpdateTransactionUseCase",
    "DeleteTransactionUseCase",
    "GetDashboardOverviewUseCase",
    "GetDashboardSummariesUseCase",
    "GetFundingAccountsUseCase",
    "CreateFundingAccountUseCase",
    "GetTransactionAnalyticsUseCase",
]
