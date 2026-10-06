"""ViewModels for the rentals templates.

The only place domain objects become strings, labels, and CSS classes. Templates
never touch domain objects directly, which keeps presentation out of the inner
layers and makes every rendering decision unit-testable without a request
context (see `src/interfaces/web/viewmodels.py` for the same idea in the legacy
flat structure).

**Money is formatted here, never in a template.** Each `from_domain` takes the
caller's `CurrencyPreference`, built from the profile context's `currency` and
`language`, so a COP user sees `COP 700.000` and a BRL user sees `R$700.000,00`
from the same entity. A template that wrote `R$` would be a hardcoded default
that silently misreports every amount for every other user.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.rentals.domain.entities import (
    Apartment,
    Document,
    House,
    PaymentRecord,
    UtilityReading,
)
from src.rentals.domain.value_objects import (
    PaymentStatus,
    PaymentSummary,
    UtilityBill,
    UtilityType,
)
from src.shared.utils.currency import (
    DEFAULT_CURRENCY,
    CurrencyPreference,
    group_amount,
)

#: Used when a caller forgets to pass a preference, so a ViewModel built in a
#: unit test or a new template still renders a sane amount.
DEFAULT_PREFERENCE = CurrencyPreference(code=DEFAULT_CURRENCY)

DOCUMENT_TYPE_LABELS: dict[str, str] = {
    "ID_CARD": "ID card",
    "LEASE_CONTRACT": "Lease contract",
    "EMPLOYMENT_CERTIFICATE": "Employment certificate",
    "OTHER": "Other",
}

UTILITY_LABELS: dict[str, str] = {
    "WATER": "Water",
    "ELECTRICITY": "Electricity",
    "GAS": "Gas",
}

PAYMENT_STATUS_STYLES: dict[str, tuple[str, str]] = {
    # (tailwind classes, label)
    "PAID": ("bg-green-100 text-green-800", "Paid"),
    "PARTIAL": ("bg-yellow-100 text-yellow-800", "Partial"),
    "PENDING": ("bg-gray-100 text-gray-800", "Pending"),
    "OVERDUE": ("bg-red-100 text-red-800", "Overdue"),
}


def _money(amount: Decimal, currency: CurrencyPreference) -> str:
    """Render a monetary amount in the user's currency and locale."""
    return currency.format(amount)


def _rate(amount: Decimal, currency: CurrencyPreference) -> str:
    """Render a per-unit tariff: a symbol plus four decimals, never rounded.

    Deliberately not `currency.format`, which quantises to the currency's display
    places. A tariff of ``3.2567`` per unit rounded to two decimals becomes
    ``3.26``, and multiplied across a large consumption the bill would no longer
    match the readings that produced it. For a zero-decimal currency such as COP,
    quantising to 0 places would erase the rate entirely.
    """
    digits = group_amount(amount, places=4, language=currency.language)
    return f"{currency.symbol}{digits}"


def _quantity(amount: Decimal) -> str:
    """Render a meter quantity, which is never money.

    Readings are physical units, so they keep two decimals and no symbol: a
    water meter reading of ``150.50`` must not acquire a currency just because
    the page happens to show rent elsewhere.
    """
    return f"{amount:.2f}"


def _readable_date(value: date) -> str:
    """Render an ISO date as ``YYYY-MM-DD``.

    Deliberately not locale-formatted: the backend stores dates without a
    timezone and the rest of the app renders ISO strings, so a mixed format here
    would be the only one in the product.
    """
    return value.isoformat()


@dataclass(frozen=True, slots=True)
class AddressViewModel:
    street: str
    city: str
    state: str
    country: str

    @property
    def one_line(self) -> str:
        return ", ".join(
            part for part in (self.street, self.city, self.state, self.country) if part
        )


@dataclass(frozen=True, slots=True)
class HouseViewModel:
    id: str
    name: str
    address: AddressViewModel

    @classmethod
    def from_domain(cls, house: House) -> HouseViewModel:
        return cls(
            id=str(house.id),
            name=house.name,
            address=AddressViewModel(
                street=house.address.street,
                city=house.address.city,
                state=house.address.state,
                country=house.address.country,
            ),
        )


@dataclass(frozen=True, slots=True)
class ApartmentViewModel:
    id: str
    house_id: str
    number: str
    floor: int
    monthly_rent: str

    @classmethod
    def from_domain(
        cls,
        apartment: Apartment,
        currency: CurrencyPreference = DEFAULT_PREFERENCE,
    ) -> ApartmentViewModel:
        return cls(
            id=str(apartment.id),
            house_id=str(apartment.house_id),
            number=str(apartment.number),
            floor=apartment.floor,
            monthly_rent=_money(apartment.monthly_rent.amount, currency),
        )


@dataclass(frozen=True, slots=True)
class PaymentSummaryViewModel:
    period_label: str
    total_paid: str
    outstanding_balance: str
    payment_count: int
    is_settled: bool

    @classmethod
    def from_domain(
        cls,
        summary: PaymentSummary,
        currency: CurrencyPreference = DEFAULT_PREFERENCE,
    ) -> PaymentSummaryViewModel:
        return cls(
            period_label=summary.period.label,
            total_paid=_money(summary.total_paid, currency),
            outstanding_balance=_money(summary.outstanding_balance, currency),
            payment_count=summary.payment_count,
            is_settled=summary.is_settled,
        )


@dataclass(frozen=True, slots=True)
class UtilityReadingViewModel:
    id: str
    utility_type: str
    utility_label: str
    reading_date: str
    current_reading: str
    previous_reading: str
    consumption: str
    unit_cost: str
    total_cost: str

    @classmethod
    def from_domain(
        cls,
        reading: UtilityReading,
        currency: CurrencyPreference = DEFAULT_PREFERENCE,
    ) -> UtilityReadingViewModel:
        return cls(
            id=str(reading.id),
            utility_type=reading.utility_type.value,
            utility_label=UTILITY_LABELS.get(reading.utility_type.value, "—"),
            reading_date=_readable_date(reading.reading_date),
            # Meter readings and consumption are physical quantities, not money.
            current_reading=_quantity(reading.current_reading.amount),
            previous_reading=_quantity(reading.previous_reading.amount),
            consumption=_quantity(reading.consumption.amount),
            # The tariff is money, and keeps its four-decimal precision because
            # rounding it to cents would misstate the rate per unit.
            unit_cost=_rate(reading.unit_cost.amount, currency),
            total_cost=_money(reading.total_cost, currency),
        )


@dataclass(frozen=True, slots=True)
class PaymentRecordViewModel:
    id: str
    payment_date: str
    amount: str
    status: str
    status_label: str
    status_classes: str
    notes: str

    @classmethod
    def from_domain(
        cls,
        record: PaymentRecord,
        currency: CurrencyPreference = DEFAULT_PREFERENCE,
    ) -> PaymentRecordViewModel:
        classes, label = PAYMENT_STATUS_STYLES.get(
            record.status.value, ("bg-gray-100 text-gray-800", record.status.value)
        )
        return cls(
            id=str(record.id),
            payment_date=_readable_date(record.payment_date),
            amount=_money(record.amount.amount, currency),
            status=record.status.value,
            status_label=label,
            status_classes=classes,
            # `notes` is nullable on the wire; the template must never print "None".
            notes=record.notes or "",
        )


@dataclass(frozen=True, slots=True)
class DocumentViewModel:
    id: str
    document_type: str
    document_label: str
    file_url: str
    file_name: str
    description: str
    uploaded_at: str

    @classmethod
    def from_domain(cls, document: Document) -> DocumentViewModel:
        file_url = document.file_url
        return cls(
            id=str(document.id),
            document_type=document.document_type.value,
            document_label=DOCUMENT_TYPE_LABELS.get(
                document.document_type.value, "Other"
            ),
            file_url=file_url,
            # The Nextcloud path ends in the file name; showing the whole URL as
            # link text would break the layout and tell the user nothing.
            file_name=file_url.rstrip("/").rsplit("/", 1)[-1] or file_url,
            description=document.description or "",
            uploaded_at=document.uploaded_at.isoformat(timespec="minutes"),
        )


@dataclass(frozen=True, slots=True)
class UtilityBillViewModel:
    utility_type: str
    utility_label: str
    period_label: str
    total_consumption: str
    total_cost: str
    reading_count: int
    average_unit_cost: str

    @classmethod
    def from_domain(
        cls,
        bill: UtilityBill,
        currency: CurrencyPreference = DEFAULT_PREFERENCE,
    ) -> UtilityBillViewModel:
        return cls(
            utility_type=bill.utility_type.value,
            utility_label=UTILITY_LABELS.get(bill.utility_type.value, "—"),
            period_label=bill.period.label,
            total_consumption=_quantity(bill.total_consumption.amount),
            total_cost=_money(bill.total_cost, currency),
            reading_count=bill.reading_count,
            # A blended per-unit rate; same four-decimal rule as a raw tariff.
            average_unit_cost=_rate(bill.average_unit_cost, currency),
        )


__all__ = [
    "DOCUMENT_TYPE_LABELS",
    "PAYMENT_STATUS_STYLES",
    "UTILITY_LABELS",
    "AddressViewModel",
    "ApartmentViewModel",
    "DocumentViewModel",
    "HouseViewModel",
    "PaymentRecordViewModel",
    "PaymentStatus",
    "PaymentSummaryViewModel",
    "UtilityBillViewModel",
    "UtilityReadingViewModel",
    "UtilityType",
]
