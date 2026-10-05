"""Rental entities and their invariants.

This module is pure Python: no Flask, no httpx, no pydantic. Entities are
mutable dataclasses with explicit behaviour methods, because the rentals rules
are stateful (`Apartment.add_document`, `PaymentRecord.mark_overdue`) and hiding
them behind a `with_` style API would only move the same rules into the
application layer.

Field sets mirror the DRF output serializers in
`apps.rentals.interfaces.serializers`:

* `HouseDetailsSerializer`     -> `street`/`city`/`state`/`country` flattened
* `ApartmentDetailsSerializer` -> `number`, `floor`, `monthly_rent`
* `DocumentDetailsSerializer`  -> `file_url` as an absolute URL
* `UtilityReadingDetailsSerializer`
* `PaymentRecordDetailsSerializer`

Timestamps are timezone-aware by contract. The backend writes them with
`auto_now_add`/`auto_now` and serialises them with DRF's `DateTimeField`, which
emits a trailing `Z` for UTC, so a naive datetime here would silently drop the
offset when the BFF re-serialises for a template.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from src.rentals.domain.exceptions import (
    ApartmentNotFoundError,
    DocumentNotFoundError,
    InvalidDocumentError,
    InvalidReadingError,
    PaymentRecordNotFoundError,
    UtilityReadingNotFoundError,
)
from src.rentals.domain.value_objects import (
    CENT,
    Address,
    ApartmentId,
    ApartmentNumber,
    DocumentId,
    DocumentType,
    HouseId,
    MonthlyRent,
    PaymentAmount,
    PaymentRecordId,
    PaymentStatus,
    Reading,
    UnitCost,
    UserId,
    UtilityReadingId,
    UtilityType,
    validate_document_url,
)

MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 2000
MAX_NOTES_LENGTH = 2000


def _require_aware(moment: datetime, label: str) -> None:
    """Reject naive datetimes.

    A naive datetime reaching a template renders without an offset, and any
    later comparison against an aware value raises `TypeError`, so it is
    rejected at construction instead.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{label} timestamps must be timezone-aware.")


def _normalize_optional_text(raw: str | None, limit: int, label: str) -> str | None:
    """Trim, collapse, and map blank/empty to `None`."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{label} must be a string or None.")
    normalized = " ".join(raw.split())
    if not normalized:
        return None
    if len(normalized) > limit:
        raise ValueError(f"{label} cannot exceed {limit} characters.")
    return normalized


@dataclass(eq=False, slots=True)
class House:
    """A rental property owned by a single user."""

    owner_id: UserId
    name: str
    address: Address
    created_at: datetime
    updated_at: datetime
    id: HouseId | None = None
    #: Ids of the apartments the backend reports for this house. The BFF never
    #: mutates this from a request payload; it is populated by the mapper and
    #: used to cross-check ownership links.
    apartment_ids: list[ApartmentId] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = self._clean_name(self.name)
        self.owner_id = self._require(UserId, self.owner_id, "House owner")
        self.address = self._require(Address, self.address, "House address")
        if self.id is not None:
            self.id = self._require(HouseId, self.id, "House id")
        _require_aware(self.created_at, "House")
        _require_aware(self.updated_at, "House")

    @classmethod
    def create(
        cls,
        *,
        owner_id: UserId,
        name: str,
        address: Address,
        now: datetime,
    ) -> House:
        """Create a house, stamping both timestamps from `now`."""
        _require_aware(now, "House")
        return cls(
            id=None,
            owner_id=owner_id,
            name=name,
            address=address,
            created_at=now,
            updated_at=now,
        )

    def rename(self, name: str, *, now: datetime) -> None:
        """Change the display name."""
        _require_aware(now, "House")
        self.name = self._clean_name(name)
        self.updated_at = now

    def change_address(self, address: Address, *, now: datetime) -> None:
        """Replace the postal address."""
        _require_aware(now, "House")
        self.address = self._require(Address, address, "House address")
        self.updated_at = now

    def link_apartment(self, apartment_id: ApartmentId) -> None:
        """Record that `apartment_id` belongs to this house; duplicates rejected."""
        apartment_id = self._require(ApartmentId, apartment_id, "Apartment")
        if apartment_id in self.apartment_ids:
            raise ValueError("Apartment is already registered on this house.")
        self.apartment_ids.append(apartment_id)

    def unlink_apartment(self, apartment_id: ApartmentId) -> None:
        """Remove an apartment link, raising when it was never present."""
        try:
            self.apartment_ids.remove(apartment_id)
        except ValueError as exc:
            raise ApartmentNotFoundError(
                "Apartment does not belong to this house."
            ) from exc

    @staticmethod
    def _clean_name(name: object) -> str:
        if not isinstance(name, str):
            raise ValueError("House name must be a string.")
        normalized = " ".join(name.split())
        if not normalized:
            raise ValueError("House name cannot be empty.")
        if len(normalized) > MAX_NAME_LENGTH:
            raise ValueError(f"House name cannot exceed {MAX_NAME_LENGTH} characters.")
        return normalized

    @staticmethod
    def _require(expected: type, value: object, label: str):
        if not isinstance(value, expected):
            raise TypeError(f"{label} must be a {expected.__name__}.")
        return value


@dataclass(eq=False, slots=True)
class Apartment:
    """One rentable unit inside a house."""

    house_id: HouseId
    number: ApartmentNumber
    floor: int
    monthly_rent: MonthlyRent
    created_at: datetime
    updated_at: datetime
    id: ApartmentId | None = None
    document_ids: list[DocumentId] = field(default_factory=list)
    utility_reading_ids: list[UtilityReadingId] = field(default_factory=list)
    payment_record_ids: list[PaymentRecordId] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.house_id = self._require(HouseId, self.house_id, "Apartment house")
        self.number = self._require(ApartmentNumber, self.number, "Apartment number")
        self.monthly_rent = self._require(
            MonthlyRent, self.monthly_rent, "Apartment rent"
        )
        self.floor = self._clean_floor(self.floor)
        if self.id is not None:
            self.id = self._require(ApartmentId, self.id, "Apartment id")
        _require_aware(self.created_at, "Apartment")
        _require_aware(self.updated_at, "Apartment")

    @classmethod
    def create(
        cls,
        *,
        house_id: HouseId,
        number: ApartmentNumber,
        floor: int,
        monthly_rent: MonthlyRent,
        now: datetime,
    ) -> Apartment:
        """Create an apartment, stamping both timestamps from `now`."""
        _require_aware(now, "Apartment")
        return cls(
            id=None,
            house_id=house_id,
            number=number,
            floor=floor,
            monthly_rent=monthly_rent,
            created_at=now,
            updated_at=now,
        )

    def update(
        self,
        *,
        number: ApartmentNumber,
        floor: int,
        monthly_rent: MonthlyRent,
        now: datetime,
    ) -> None:
        """Apply a complete validated replacement."""
        _require_aware(now, "Apartment")
        self.number = self._require(ApartmentNumber, number, "Apartment number")
        self.monthly_rent = self._require(MonthlyRent, monthly_rent, "Apartment rent")
        self.floor = self._clean_floor(floor)
        self.updated_at = now

    def link_document(self, document_id: DocumentId) -> None:
        """Record a document id; duplicates rejected."""
        document_id = self._require(DocumentId, document_id, "Document")
        if document_id in self.document_ids:
            raise ValueError("Document is already registered on this apartment.")
        self.document_ids.append(document_id)

    def link_utility_reading(self, reading_id: UtilityReadingId) -> None:
        """Record a utility reading id; duplicates rejected."""
        reading_id = self._require(UtilityReadingId, reading_id, "Reading")
        if reading_id in self.utility_reading_ids:
            raise ValueError("Reading is already registered on this apartment.")
        self.utility_reading_ids.append(reading_id)

    def link_payment_record(self, payment_record_id: PaymentRecordId) -> None:
        """Record a payment record id; duplicates rejected."""
        payment_record_id = self._require(PaymentRecordId, payment_record_id, "Payment")
        if payment_record_id in self.payment_record_ids:
            raise ValueError("Payment is already registered on this apartment.")
        self.payment_record_ids.append(payment_record_id)

    def unlink_document(self, document_id: DocumentId) -> None:
        """Remove a document link, raising when it was never present."""
        try:
            self.document_ids.remove(document_id)
        except ValueError as exc:
            raise DocumentNotFoundError(
                "Document does not belong to this apartment."
            ) from exc

    def unlink_utility_reading(self, reading_id: UtilityReadingId) -> None:
        """Remove a reading link, raising when it was never present."""
        try:
            self.utility_reading_ids.remove(reading_id)
        except ValueError as exc:
            raise UtilityReadingNotFoundError(
                "Reading does not belong to this apartment."
            ) from exc

    def unlink_payment_record(self, payment_record_id: PaymentRecordId) -> None:
        """Remove a payment link, raising when it was never present."""
        try:
            self.payment_record_ids.remove(payment_record_id)
        except ValueError as exc:
            raise PaymentRecordNotFoundError(
                "Payment does not belong to this apartment."
            ) from exc

    @staticmethod
    def _clean_floor(floor: object) -> int:
        # bool is a subclass of int; floor=True would otherwise become 1.
        if isinstance(floor, bool) or not isinstance(floor, int):
            raise TypeError("Apartment floor must be an integer.")
        if floor < 0:
            raise ValueError("Apartment floor cannot be negative.")
        return floor

    @staticmethod
    def _require(expected: type, value: object, label: str):
        if not isinstance(value, expected):
            raise TypeError(f"{label} must be a {expected.__name__}.")
        return value


@dataclass(eq=False, slots=True)
class Document:
    """A file attached to an apartment, already stored in Nextcloud.

    The BFF never sees the file bytes. The frontend PUTs directly to the
    Nextcloud public WebDAV endpoint and sends the resulting absolute URL here;
    this entity only records it. `file_url` is therefore validated as an
    absolute URL, matching the backend's `models.URLField(max_length=500)`.
    """

    apartment_id: ApartmentId
    document_type: DocumentType
    file_url: str
    uploaded_at: datetime
    description: str | None = None
    id: DocumentId | None = None

    def __post_init__(self) -> None:
        self.apartment_id = Apartment._require(
            ApartmentId, self.apartment_id, "Document apartment"
        )
        self.document_type = self._clean_document_type(self.document_type)
        self.file_url = validate_document_url(self.file_url)
        self.description = _normalize_optional_text(
            self.description, MAX_DESCRIPTION_LENGTH, "Document description"
        )
        if self.id is not None:
            self.id = Apartment._require(DocumentId, self.id, "Document id")
        _require_aware(self.uploaded_at, "Document")

    @classmethod
    def create(
        cls,
        *,
        apartment_id: ApartmentId,
        document_type: DocumentType,
        file_url: str,
        description: str | None,
        now: datetime,
    ) -> Document:
        """Register a document reference for an already-uploaded file."""
        _require_aware(now, "Document")
        return cls(
            id=None,
            apartment_id=apartment_id,
            document_type=document_type,
            file_url=file_url,
            description=description,
            uploaded_at=now,
        )

    @staticmethod
    def _clean_document_type(document_type: object) -> DocumentType:
        """Accept a `DocumentType` or its wire string.

        The wire string case keeps the domain constructible straight from a form
        post without importing any enum-parsing helper from infrastructure.
        """
        if isinstance(document_type, DocumentType):
            return document_type
        if isinstance(document_type, str):
            try:
                return DocumentType(document_type)
            except ValueError as exc:
                raise InvalidDocumentError(
                    f"'{document_type}' is not a valid document type."
                ) from exc
        raise InvalidDocumentError("Document type is invalid.")


@dataclass(eq=False, slots=True)
class UtilityReading:
    """One meter reading for one utility of one apartment.

    `consumption` and `total_cost` are never accepted as input: they are derived
    in :meth:`create` from the two readings and the tariff. The backend does the
    same arithmetic server-side (its `UtilityReadingModel.create`), so deriving
    them here keeps the two sides comparable and lets the UI preview a total
    before the POST resolves.
    """

    apartment_id: ApartmentId
    utility_type: UtilityType
    reading_date: date
    current_reading: Reading
    previous_reading: Reading
    consumption: Reading
    unit_cost: UnitCost
    total_cost: Decimal
    created_at: datetime
    id: UtilityReadingId | None = None

    def __post_init__(self) -> None:
        self.apartment_id = Apartment._require(
            ApartmentId, self.apartment_id, "Reading apartment"
        )
        self.utility_type = self._clean_utility_type(self.utility_type)
        self._require_exact_date(self.reading_date)
        for attribute, expected, label in (
            ("current_reading", Reading, "Current reading"),
            ("previous_reading", Reading, "Previous reading"),
            ("consumption", Reading, "Consumption"),
            ("unit_cost", UnitCost, "Unit cost"),
        ):
            value = getattr(self, attribute)
            if not isinstance(value, expected):
                raise TypeError(f"{label} must be a {expected.__name__}.")
        if self.current_reading.amount < self.previous_reading.amount:
            raise InvalidReadingError(
                "Current reading cannot be lower than the previous reading."
            )
        if self.id is not None:
            self.id = Apartment._require(UtilityReadingId, self.id, "Reading id")
        _require_aware(self.created_at, "Utility reading")

    @classmethod
    def create(
        cls,
        *,
        apartment_id: ApartmentId,
        utility_type: UtilityType,
        reading_date: date,
        current_reading: Reading,
        previous_reading: Reading,
        unit_cost: UnitCost,
        now: datetime,
    ) -> UtilityReading:
        """Create a reading, deriving consumption and total cost."""
        _require_aware(now, "Utility reading")
        cls._require_exact_date(reading_date)
        for value, expected, label in (
            (current_reading, Reading, "Current reading"),
            (previous_reading, Reading, "Previous reading"),
            (unit_cost, UnitCost, "Unit cost"),
        ):
            if not isinstance(value, expected):
                raise TypeError(f"{label} must be a {expected.__name__}.")
        if current_reading.amount < previous_reading.amount:
            raise InvalidReadingError(
                "Current reading cannot be lower than the previous reading."
            )
        consumption_amount = current_reading.amount - previous_reading.amount
        total_cost = (consumption_amount * unit_cost.amount).quantize(CENT)
        return cls(
            id=None,
            apartment_id=apartment_id,
            utility_type=utility_type,
            reading_date=reading_date,
            current_reading=current_reading,
            previous_reading=previous_reading,
            consumption=Reading(consumption_amount),
            unit_cost=unit_cost,
            total_cost=total_cost,
            created_at=now,
        )

    @staticmethod
    def _clean_utility_type(utility_type: object) -> UtilityType:
        if isinstance(utility_type, UtilityType):
            return utility_type
        if isinstance(utility_type, str):
            try:
                return UtilityType(utility_type)
            except ValueError as exc:
                raise ValueError(
                    f"'{utility_type}' is not a valid utility type."
                ) from exc
        raise ValueError("Utility type is invalid.")

    @staticmethod
    def _require_exact_date(value: object) -> None:
        # `isinstance(x, date)` is True for datetime too, and a datetime here
        # would lose its time component when formatted for a template.
        if type(value) is not date:
            raise ValueError("Reading date must be a date.")


@dataclass(eq=False, slots=True)
class PaymentRecord:
    """A monthly rent payment made for an apartment.

    `status` is derived from `amount` vs `monthly_rent` when not supplied,
    mirroring the backend's `PaymentRecord.calculate_status`.
    """

    apartment_id: ApartmentId
    payment_date: date
    amount: PaymentAmount
    status: PaymentStatus
    created_at: datetime
    notes: str | None = None
    id: PaymentRecordId | None = None

    def __post_init__(self) -> None:
        self.apartment_id = Apartment._require(
            ApartmentId, self.apartment_id, "Payment apartment"
        )
        self._require_exact_date(self.payment_date)
        if not isinstance(self.amount, PaymentAmount):
            raise TypeError("Payment amount must be a PaymentAmount.")
        self.status = self._clean_status(self.status)
        self.notes = _normalize_optional_text(
            self.notes, MAX_NOTES_LENGTH, "Payment notes"
        )
        if self.id is not None:
            self.id = Apartment._require(PaymentRecordId, self.id, "Payment id")
        _require_aware(self.created_at, "Payment")

    @classmethod
    def create(
        cls,
        *,
        apartment_id: ApartmentId,
        payment_date: date,
        amount: PaymentAmount,
        monthly_rent: MonthlyRent,
        notes: str | None,
        now: datetime,
        status: PaymentStatus | None = None,
    ) -> PaymentRecord:
        """Create a payment, deriving `status` when it is not supplied."""
        _require_aware(now, "Payment")
        cls._require_exact_date(payment_date)
        if not isinstance(amount, PaymentAmount):
            raise TypeError("Payment amount must be a PaymentAmount.")
        if not isinstance(monthly_rent, MonthlyRent):
            raise TypeError("Monthly rent must be a MonthlyRent.")
        resolved = (
            cls.calculate_status(amount=amount, monthly_rent=monthly_rent)
            if status is None
            else cls._clean_status(status)
        )
        return cls(
            id=None,
            apartment_id=apartment_id,
            payment_date=payment_date,
            amount=amount,
            status=resolved,
            notes=notes,
            created_at=now,
        )

    def refresh_status(self, *, monthly_rent: MonthlyRent) -> None:
        """Recompute the status from the current amount and the monthly rent."""
        if not isinstance(monthly_rent, MonthlyRent):
            raise TypeError("Monthly rent must be a MonthlyRent.")
        self.status = self.calculate_status(
            amount=self.amount, monthly_rent=monthly_rent
        )

    def mark_overdue(self) -> None:
        """Mark the payment overdue; a fully paid payment cannot lapse."""
        if self.status is PaymentStatus.PAID:
            raise ValueError("A paid payment cannot be marked as overdue.")
        self.status = PaymentStatus.OVERDUE

    @staticmethod
    def calculate_status(
        *, amount: PaymentAmount, monthly_rent: MonthlyRent
    ) -> PaymentStatus:
        """Derive the status by comparing the amount to the monthly rent."""
        if amount.amount >= monthly_rent.amount:
            return PaymentStatus.PAID
        return PaymentStatus.PARTIAL

    @staticmethod
    def _clean_status(status: object) -> PaymentStatus:
        if isinstance(status, PaymentStatus):
            return status
        if isinstance(status, str):
            try:
                return PaymentStatus(status)
            except ValueError as exc:
                raise ValueError(f"'{status}' is not a valid payment status.") from exc
        raise ValueError("Payment status is invalid.")

    @staticmethod
    def _require_exact_date(value: object) -> None:
        if type(value) is not date:
            raise ValueError("Payment date must be a date.")
