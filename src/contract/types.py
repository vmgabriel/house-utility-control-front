# GENERATED FILE: DO NOT EDIT MANUALLY.
#
# Auto-generated from the DRF OpenAPI schema by `make sync-api-contract`.
#
# This is a contract-VERIFICATION artifact used to detect API drift. It is not
# imported at runtime. drf-spectacular declares several fields required and
# non-nullable that the live API actually returns as null, so the authoritative
# runtime types remain the hand-written pydantic schemas in
# src/infrastructure/api/schemas.py and the per-context equivalents. The
# divergences are catalogued in src/contract/overrides.py.
#
# Run `make sync-api-contract` to regenerate after an intentional API change.
# Run `make verify-api-contract` to fail CI when the backend has drifted.

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any


@dataclass
class CustomTokenObtainPairRequest:
    email: str
    password: str


class DateFormatEnum(Enum):
    YYYY_MM_DD = "YYYY-MM-DD"
    DD_MM_YYYY = "DD/MM/YYYY"
    MM_DD_YYYY = "MM/DD/YYYY"


@dataclass
class ErrorPayload:
    code: str
    message: str
    details: Any | None = None


@dataclass
class ErrorResponse:
    error: ErrorPayload
    detail: str | None = None


@dataclass
class LogoutResponse:
    message: str


@dataclass
class PatchedAdminUpdateUserRequest:
    email: str | None = None
    full_name: str | None = None
    is_active: bool | None = None


@dataclass
class PatchedBanUserRequest:
    reason: str | None = None


@dataclass
class PatchedUpdatePreferencesRequest:
    language: str | None = None
    currency: str | None = None
    date_format: DateFormatEnum | None = None


@dataclass
class PatchedUpdateProfileRequest:
    first_name: str | None = None
    last_name: str | None = None
    timezone: str | None = None
    avatar_url: str | None = None
    bio: str | None = None


class PeriodEnum(Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class PlanEnum(Enum):
    free = "free"
    pro = "pro"
    premium = "premium"


@dataclass
class Profile:
    id: str
    first_name: str
    last_name: str
    timezone: str
    language: str
    currency: str
    date_format: DateFormatEnum
    avatar_url: str
    bio: str
    created_at: str
    updated_at: str


@dataclass
class RegistrationRequest:
    email: str
    full_name: str
    password: str


class StatusEnum(Enum):
    fresh = "fresh"
    stale = "stale"


@dataclass
class TokenRefreshRequest:
    refresh: str


@dataclass
class TokenRefreshResponse:
    access: str
    refresh: str


@dataclass
class TokenResponse:
    access: str
    refresh: str
    user_id: str
    email: str
    full_name: str


class TransactionTypeEnum(Enum):
    income = "income"
    expense = "expense"
    investment = "investment"
    savings = "savings"


@dataclass
class User:
    id: str
    email: str
    full_name: str
    plan: PlanEnum
    is_active: bool
    is_staff: bool
    is_superuser: bool
    created_at: str
    updated_at: str


@dataclass
class UserPage:
    count: int
    page: int
    page_size: int
    results: list[User]


@dataclass
class CreateTransactionRequest:
    amount: Decimal
    transaction_type: TransactionTypeEnum
    category: str
    date: str
    description: str | None = None


@dataclass
class DashboardSummary:
    id: str
    period: PeriodEnum
    date: str
    total_income: Decimal
    total_expense: Decimal
    net_balance: Decimal
    generated_at: str
    is_stale: bool
    stale_at: str
    status: StatusEnum


@dataclass
class PatchedChangeUserPlanRequest:
    plan: PlanEnum | None = None


@dataclass
class PatchedUpdateTransactionRequest:
    amount: Decimal | None = None
    transaction_type: TransactionTypeEnum | None = None
    category: str | None = None
    date: str | None = None
    description: str | None = None


@dataclass
class Transaction:
    id: str
    amount: Decimal
    transaction_type: TransactionTypeEnum
    category: str
    date: str
    description: str
    created_at: str
    updated_at: str


@dataclass
class TransactionPage:
    count: int
    page: int
    page_size: int
    results: list[Transaction]


@dataclass
class DashboardList:
    period: PeriodEnum
    start_date: str
    end_date: str
    summary_count: int
    is_empty: bool
    has_stale_data: bool
    summaries: list[DashboardSummary]


@dataclass
class DashboardOverview:
    as_of_date: str
    today: DashboardSummary
    this_week: DashboardSummary
    this_month: DashboardSummary
