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
class ApartmentDetails:
    id: str
    house_id: str
    number: str
    floor: int
    monthly_rent: Decimal
    created_at: str
    updated_at: str


@dataclass
class CreateApartmentRequest:
    house_id: str
    number: str
    monthly_rent: Decimal
    floor: int | None = 1


@dataclass
class CreateHouseRequest:
    name: str
    street: str
    city: str
    state: str
    country: str


@dataclass
class CustomTokenObtainPairRequest:
    email: str
    password: str


class DashboardSummaryStatusEnum(Enum):
    fresh = "fresh"
    stale = "stale"


class DateFormatEnum(Enum):
    YYYY_MM_DD = "YYYY-MM-DD"
    DD_MM_YYYY = "DD/MM/YYYY"
    MM_DD_YYYY = "MM/DD/YYYY"


class DocumentTypeEnum(Enum):
    ID_CARD = "ID_CARD"
    LEASE_CONTRACT = "LEASE_CONTRACT"
    EMPLOYMENT_CERTIFICATE = "EMPLOYMENT_CERTIFICATE"
    OTHER = "OTHER"


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
class HouseDetails:
    id: str
    owner_id: str
    name: str
    street: str
    city: str
    state: str
    country: str
    created_at: str
    updated_at: str


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
class PatchedUpdateApartmentRequest:
    number: str | None = None
    floor: int | None = None
    monthly_rent: Decimal | None = None


@dataclass
class PatchedUpdateHouseRequest:
    name: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None


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


@dataclass
class PaymentSummaryDetails:
    apartment_id: str
    year: int
    month: int
    total_paid: Decimal
    outstanding_balance: Decimal
    payment_count: int


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
class RecordPaymentRequest:
    payment_date: str
    amount: Decimal
    notes: str | None = None


@dataclass
class RegistrationRequest:
    email: str
    full_name: str
    password: str


class StatusE00Enum(Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    OVERDUE = "OVERDUE"


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
class UploadDocumentRequest:
    document_type: DocumentTypeEnum
    file_url: str
    description: str | None = None


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


class UtilityTypeEnum(Enum):
    WATER = "WATER"
    ELECTRICITY = "ELECTRICITY"
    GAS = "GAS"


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
    status: DashboardSummaryStatusEnum


@dataclass
class DocumentDetails:
    id: str
    apartment_id: str
    document_type: DocumentTypeEnum
    file_url: str
    description: str
    uploaded_at: str


@dataclass
class PatchedChangeUserPlanRequest:
    plan: PlanEnum | None = None


@dataclass
class PatchedUpdatePaymentRequest:
    amount: Decimal | None = None
    status: StatusE00Enum | None = None
    notes: str | None = None


@dataclass
class PatchedUpdateTransactionRequest:
    amount: Decimal | None = None
    transaction_type: TransactionTypeEnum | None = None
    category: str | None = None
    date: str | None = None
    description: str | None = None


@dataclass
class PaymentRecordDetails:
    id: str
    apartment_id: str
    payment_date: str
    amount: Decimal
    status: StatusE00Enum
    notes: str
    created_at: str


@dataclass
class RecordUtilityReadingRequest:
    utility_type: UtilityTypeEnum
    reading_date: str
    current_reading: Decimal
    previous_reading: Decimal
    unit_cost: Decimal


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
class UtilityBillDetails:
    apartment_id: str
    utility_type: UtilityTypeEnum
    year: int
    month: int
    total_consumption: Decimal
    total_cost: Decimal
    reading_count: int


@dataclass
class UtilityReadingDetails:
    id: str
    apartment_id: str
    utility_type: UtilityTypeEnum
    reading_date: str
    current_reading: Decimal
    previous_reading: Decimal
    consumption: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    created_at: str


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
