"""Immutable value objects for the rentals domain.

This module is pure Python: no Flask, no httpx, no DRF. It must stay importable
from the domain and application layers under AGENTS.md's framework-free-core
constraint.

Validation limits mirror the backend's `apps.rentals.domain.value_objects`, and
in several places the DRF layer mirrors them too
(`apps.rentals.interfaces.serializers`):

=========================  ==========  ==========================
Value object               Rule         Backend enforcement
=========================  ==========  ==========================
`Reading`                  >= 0, 2dp    `DecimalField(8, 2)`
`MonthlyRent`              > 0, 2dp     `DecimalField(10, 2)`
`PaymentAmount`            > 0, 2dp     `DecimalField(10, 2)`
`UnitCost`                 >= 0, 4dp    `DecimalField(10, 4)`
=========================  ==========  ==========================

`UnitCost` deliberately allows zero and four decimal places while `MonthlyRent`
and `PaymentAmount` are strictly positive with two: a prepaid tariff can round
to 0.0000 per unit, and quantising it to cents would silently change the bill.
That is why there is no generic `Money` here, mirroring the backend's split.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from uuid import UUID

from src.rentals.domain.exceptions import (
    InvalidAddressError,
    InvalidDocumentError,
    InvalidPaymentError,
    InvalidReadingError,
)

#: Two-decimal quantum used by money amounts and readings.
CENT = Decimal("0.01")
#: Four-decimal quantum used by per-unit utility tariffs.
UNIT_COST_STEP = Decimal("0.0001")

#: Matches the backend's `DecimalField(max_digits=8, decimal_places=2)`.
MAX_READING = Decimal("999999.99")
#: Matches the backend's `DecimalField(max_digits=10, decimal_places=2)`.
MAX_MONEY_AMOUNT = Decimal("99999999.99")


def _to_decimal(raw: object, label: str, error: type[Exception]) -> Decimal:
    """Coerce `raw` to a finite `Decimal`.

    DRF renders decimals as JSON strings (``COERCE_DECIMAL_TO_STRING``), so the
    mappers hand us strings; accepting an existing `Decimal` or `int` as well
    keeps the value objects usable from plain Python callers and unit tests.
    """
    if isinstance(raw, Decimal):
        candidate = raw
    elif isinstance(raw, bool):
        # bool is an int subclass; True would otherwise become Decimal(1).
        raise error(f"{label} must be a decimal value, not a boolean.")
    else:
        try:
            candidate = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise error(f"{label} must be a valid decimal value.") from exc
    if not candidate.is_finite():
        raise error(f"{label} must be finite.")
    return candidate


def _require_uuid(raw: object, label: str) -> UUID:
    """Coerce `raw` to a `UUID`, rejecting anything else."""
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"{label} must be a valid UUID.") from exc
    raise TypeError(f"{label} must be a UUID.")


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class HouseId:
    """Identity of a persisted house."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid(self.value, "HouseId"))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ApartmentId:
    """Identity of a persisted apartment."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid(self.value, "ApartmentId"))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class DocumentId:
    """Identity of a persisted document."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid(self.value, "DocumentId"))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class UtilityReadingId:
    """Identity of a persisted utility reading."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid(self.value, "UtilityReadingId"))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class PaymentRecordId:
    """Identity of a persisted payment record."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid(self.value, "PaymentRecordId"))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class UserId:
    """Identity of the owner of a house.

    Deliberately duplicated rather than imported from `src.users`: bounded
    contexts never import each other, so each keeps its own copy of shared
    identity concepts.
    """

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid(self.value, "UserId"))

    def __str__(self) -> str:
        return str(self.value)


# --------------------------------------------------------------------------- #
# Business value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Address:
    """A validated postal address for a rental property.

    Mirrors the DRF `HouseDetailsSerializer`, which flattens the address into
    `street`/`city`/`state`/`country` rather than nesting an object.
    """

    street: str
    city: str
    state: str
    country: str

    #: Field name -> (max_length, required).
    _LIMITS = (("street", 200), ("city", 100), ("state", 100), ("country", 100))

    def __post_init__(self) -> None:
        for field_name, limit in self._LIMITS:
            raw = getattr(self, field_name)
            if not isinstance(raw, str):
                raise InvalidAddressError(f"Address {field_name} must be a string.")
            normalized = " ".join(raw.split())
            if not normalized:
                raise InvalidAddressError(f"Address {field_name} cannot be empty.")
            if len(normalized) > limit:
                raise InvalidAddressError(
                    f"Address {field_name} cannot exceed {limit} characters."
                )
            object.__setattr__(self, field_name, normalized)

    def as_dict(self) -> dict[str, str]:
        """Flatten to the DRF wire shape."""
        return {
            "street": self.street,
            "city": self.city,
            "state": self.state,
            "country": self.country,
        }


@dataclass(frozen=True, slots=True)
class ApartmentNumber:
    """A normalised unit identifier within a house."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise ValueError("Apartment number must be a string.")
        normalized = " ".join(self.value.split())
        if not normalized:
            raise ValueError("Apartment number cannot be empty.")
        if len(normalized) > 20:
            raise ValueError("Apartment number cannot exceed 20 characters.")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


class UtilityType(str, Enum):
    """Utilities for which meter readings are tracked.

    ``str`` mixin rather than `StrEnum`: the infrastructure layer serialises
    these members straight to the DRF JSON API. Mirrors the backend's
    `apps.rentals.domain.value_objects.UtilityType`.
    """

    WATER = "WATER"
    ELECTRICITY = "ELECTRICITY"
    GAS = "GAS"


class DocumentType(str, Enum):
    """Categories of document attachable to an apartment.

    Mirrors the backend's `DocumentType`; the DRF `ChoiceField` rejects
    anything outside these four values with a 400.
    """

    ID_CARD = "ID_CARD"
    LEASE_CONTRACT = "LEASE_CONTRACT"
    EMPLOYMENT_CERTIFICATE = "EMPLOYMENT_CERTIFICATE"
    OTHER = "OTHER"


class PaymentStatus(str, Enum):
    """Lifecycle states of a monthly rent payment."""

    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    OVERDUE = "OVERDUE"


@dataclass(frozen=True, slots=True)
class Reading:
    """A non-negative meter reading, at most two decimal places."""

    amount: Decimal

    def __post_init__(self) -> None:
        amount = _to_decimal(self.amount, "Reading", InvalidReadingError)
        if amount < 0:
            raise InvalidReadingError("Reading cannot be negative.")
        if amount > MAX_READING:
            raise InvalidReadingError("Reading must not exceed 999999.99.")
        if amount != amount.quantize(CENT):
            raise InvalidReadingError("Reading must have at most two decimal places.")
        object.__setattr__(self, "amount", amount)

    def __str__(self) -> str:
        return f"{self.amount:.2f}"


@dataclass(frozen=True, slots=True)
class MonthlyRent:
    """A strictly positive monthly rent, at most two decimal places."""

    amount: Decimal

    def __post_init__(self) -> None:
        amount = _to_decimal(self.amount, "Monthly rent", InvalidPaymentError)
        if amount <= 0:
            raise InvalidPaymentError("Monthly rent must be greater than zero.")
        if amount > MAX_MONEY_AMOUNT:
            raise InvalidPaymentError("Monthly rent must not exceed 99999999.99.")
        if amount != amount.quantize(CENT):
            raise InvalidPaymentError(
                "Monthly rent must have at most two decimal places."
            )
        object.__setattr__(self, "amount", amount)

    def __str__(self) -> str:
        return f"{self.amount:.2f}"


@dataclass(frozen=True, slots=True)
class PaymentAmount:
    """A strictly positive payment amount, at most two decimal places."""

    amount: Decimal

    def __post_init__(self) -> None:
        amount = _to_decimal(self.amount, "Payment amount", InvalidPaymentError)
        if amount <= 0:
            raise InvalidPaymentError("Payment amount must be greater than zero.")
        if amount > MAX_MONEY_AMOUNT:
            raise InvalidPaymentError("Payment amount must not exceed 99999999.99.")
        if amount != amount.quantize(CENT):
            raise InvalidPaymentError(
                "Payment amount must have at most two decimal places."
            )
        object.__setattr__(self, "amount", amount)

    def __str__(self) -> str:
        return f"{self.amount:.2f}"


@dataclass(frozen=True, slots=True)
class UnitCost:
    """A non-negative per-unit tariff, at most four decimal places.

    Zero is legal and four decimals are required, unlike :class:`MonthlyRent`.
    """

    amount: Decimal

    def __post_init__(self) -> None:
        amount = _to_decimal(self.amount, "Unit cost", InvalidPaymentError)
        if amount < 0:
            raise InvalidPaymentError("Unit cost cannot be negative.")
        if amount > MAX_MONEY_AMOUNT:
            raise InvalidPaymentError("Unit cost must not exceed 99999999.99.")
        if amount != amount.quantize(UNIT_COST_STEP):
            raise InvalidPaymentError(
                "Unit cost must have at most four decimal places."
            )
        object.__setattr__(self, "amount", amount)

    def __str__(self) -> str:
        return f"{self.amount:.4f}"


@dataclass(frozen=True, slots=True)
class Period:
    """A calendar month used to bound bill and payment aggregation.

    Mirrors the backend's `apps.rentals.application.dto.Period`, which the
    summary and bill endpoints require as `year`/`month` query parameters.
    """

    year: int
    month: int

    def __post_init__(self) -> None:
        if isinstance(self.year, bool) or not isinstance(self.year, int):
            raise TypeError("Period year must be an integer.")
        if isinstance(self.month, bool) or not isinstance(self.month, int):
            raise TypeError("Period month must be an integer.")
        if not 1 <= self.month <= 12:
            raise ValueError("Period month must be between 1 and 12.")
        if not 1900 <= self.year <= 2100:
            raise ValueError("Period year must be between 1900 and 2100.")

    @property
    def start_date(self) -> date:
        """First day of the period."""
        return date(self.year, self.month, 1)

    @property
    def end_date(self) -> date:
        """Last day of the period, inclusive."""
        if self.month == 12:
            following = date(self.year + 1, 1, 1)
        else:
            following = date(self.year, self.month + 1, 1)
        return date.fromordinal(following.toordinal() - 1)

    @property
    def label(self) -> str:
        """Human-readable month name, for templates and flash messages."""
        return date(self.year, self.month, 1).strftime("%B %Y")


@dataclass(frozen=True, slots=True)
class PaymentSummary:
    """An apartment's rent payments aggregated over one calendar month.

    Mirrors the backend's ``PaymentSummaryDetailsSerializer``. `total_paid` and
    `outstanding_balance` are monetary totals, so they are quantised to cents;
    the backend computes the same pair from the apartment's monthly rent minus
    the sum of its payments for the period.
    """

    apartment_id: ApartmentId
    period: Period
    total_paid: Decimal
    outstanding_balance: Decimal
    payment_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.apartment_id, ApartmentId):
            raise TypeError("Payment summary apartment must be an ApartmentId.")
        if not isinstance(self.period, Period):
            raise TypeError("Payment summary period must be a Period.")
        total_paid = _to_decimal(self.total_paid, "Total paid", InvalidPaymentError)
        balance = _to_decimal(
            self.outstanding_balance, "Outstanding balance", InvalidPaymentError
        )
        # A negative outstanding balance means the tenant overpaid; that is a
        # real state (credit), so it is preserved rather than clamped.
        if total_paid < 0:
            raise InvalidPaymentError("Total paid cannot be negative.")
        if total_paid > MAX_MONEY_AMOUNT:
            raise InvalidPaymentError("Total paid must not exceed 99999999.99.")
        if abs(balance) > MAX_MONEY_AMOUNT:
            raise InvalidPaymentError(
                "Outstanding balance must not exceed 99999999.99."
            )
        if (
            isinstance(self.payment_count, bool)
            or not isinstance(self.payment_count, int)
            or self.payment_count < 0
        ):
            raise ValueError("Payment count must be a non-negative integer.")
        object.__setattr__(self, "total_paid", total_paid.quantize(CENT))
        object.__setattr__(self, "outstanding_balance", balance.quantize(CENT))

    @property
    def is_settled(self) -> bool:
        """True when nothing is outstanding for the period."""
        return self.outstanding_balance <= 0

    def __str__(self) -> str:
        return (
            f"{self.period.label}: {self.total_paid:.2f} paid, "
            f"{self.outstanding_balance:.2f} outstanding"
        )


@dataclass(frozen=True, slots=True)
class UtilityBill:
    """One utility's consumption and cost aggregated over a calendar month.

    Mirrors the backend's ``UtilityBillDetailsSerializer``. Unlike
    :class:`PaymentSummary` this is scoped to a single ``utility_type``, because
    the endpoint requires it as a query parameter.
    """

    apartment_id: ApartmentId
    utility_type: UtilityType
    period: Period
    total_consumption: Reading
    total_cost: Decimal
    reading_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.apartment_id, ApartmentId):
            raise TypeError("Utility bill apartment must be an ApartmentId.")
        if not isinstance(self.utility_type, UtilityType):
            raise TypeError("Utility bill utility type must be a UtilityType.")
        if not isinstance(self.period, Period):
            raise TypeError("Utility bill period must be a Period.")
        if not isinstance(self.total_consumption, Reading):
            raise TypeError("Utility bill consumption must be a Reading.")
        total_cost = _to_decimal(self.total_cost, "Total cost", InvalidPaymentError)
        if total_cost < 0:
            raise InvalidPaymentError("Total cost cannot be negative.")
        if total_cost > MAX_MONEY_AMOUNT:
            raise InvalidPaymentError("Total cost must not exceed 99999999.99.")
        if (
            isinstance(self.reading_count, bool)
            or not isinstance(self.reading_count, int)
            or self.reading_count < 0
        ):
            raise ValueError("Reading count must be a non-negative integer.")
        object.__setattr__(self, "total_cost", total_cost.quantize(CENT))

    @property
    def average_unit_cost(self) -> Decimal:
        """Blended cost per unit across the period, or 0 when unused.

        Guarded against a zero-consumption period, which is common for a meter
        that was not read every month.
        """
        if self.total_consumption.amount == 0:
            return Decimal("0.00")
        return (self.total_cost / self.total_consumption.amount).quantize(
            UNIT_COST_STEP
        )

    def __str__(self) -> str:
        return (
            f"{self.utility_type.value} {self.period.label}: "
            f"{self.total_consumption} units, {self.total_cost:.2f}"
        )


def validate_document_url(file_url: object) -> str:
    """Validate a Nextcloud document URL.

    The backend stores this in ``models.URLField(max_length=500)`` and re-checks
    it with ``serializers.URLField``, which runs Django's ``URLValidator``.
    A bare filename such as ``lease.pdf`` would be rejected server-side, so the
    frontend must send the absolute Nextcloud URL (the public share or direct
    upload target) and this guard fails fast with a clear message instead of
    letting the user hit a 400.

    Deliberately a light shape check rather than a full URL parse: it enforces
    the two properties the backend cares about (absolute, <= 500 chars) without
    duplicating Django's scheme/host allow-list in the BFF.
    """
    if not isinstance(file_url, str):
        raise InvalidDocumentError("Document file_url must be a string.")
    normalized = file_url.strip()
    if not normalized:
        raise InvalidDocumentError("Document file_url cannot be empty.")
    if len(normalized) > 500:
        raise InvalidDocumentError("Document file_url cannot exceed 500 characters.")
    scheme, separator, remainder = normalized.partition("://")
    if not separator or not scheme or not remainder.strip("/"):
        raise InvalidDocumentError(
            "Document file_url must be an absolute URL, not a bare file path."
        )
    # Catch an absolute URL built by concatenating one absolute URL onto
    # another, e.g.
    #   http://app.example/https://cloud.example/dav/lease.pdf
    # This is syntactically a valid absolute URL -- the second `://` simply
    # lands in the path -- so the scheme/host checks above pass it. The backend
    # accepts it too and the link is simply dead, which is why it is rejected
    # here with an actionable message rather than discovered by a user.
    if "://" in remainder:
        raise InvalidDocumentError(
            "Document file_url looks like two URLs joined together. It should be "
            "a single absolute URL such as "
            "https://cloud.example/dav/lease.pdf."
        )
    return normalized
