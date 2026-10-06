"""Translate raw DRF rentals JSON into domain entities.

These mappers take **already-parsed JSON** -- plain ``dict`` and ``list[dict]``
-- never an ``httpx.Response``. Keeping the transport out of the signature is
what lets the application layer stay free of httpx (AGENTS.md), and it is why
the same functions are usable directly in unit tests with hand-built payloads.

Four contract details drive the implementation. All were confirmed against
``apps.rentals.infrastructure.persistence.models`` and
``apps.rentals.interfaces.serializers`` in the backend repository:

1. **Decimals arrive as strings.** DRF's ``COERCE_DECIMAL_TO_STRING`` is on by
   default, so ``monthly_rent`` is ``"2500.00"``, not ``2500.0``. Numeric fields
   are parsed through the value objects rather than trusted as floats, which
   would lose precision on tenant rents and, worse, silently accept
   ``0.1 + 0.2 != 0.3`` style drift into a bill total.

2. **List endpoints return bare arrays.** ``HouseListView`` and friends return
   ``Response(Serializer(many=True).data)`` directly. DRF's
   ``DEFAULT_PAGINATION_CLASS`` does not apply to these plain ``APIView``
   classes, so there is no ``count``/``results`` envelope to unwrap -- unlike
   ``/transactions/``.

3. **Nullable fields stay nullable.** ``Document.description`` and
   ``PaymentRecord.notes`` are ``blank=True, null=True`` in the model and
   ``allow_null=True`` in the serializer, so they map to ``None`` rather than
   being defaulted to ``""``.

4. **Stored totals are mapped, not recomputed.** ``consumption``,
   ``total_cost``, and the aggregates are mapped verbatim. The backend is the
   authority for a persisted row, and re-deriving here would risk disagreeing
   with a reading written under a different tariff. Fresh *intent* is where the
   domain derives values -- see ``UtilityReading.create``.

See also ``src/contract/overrides.py`` for the documented divergences between
the OpenAPI schema and the live API.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.rentals.domain.entities import (
    Apartment,
    Document,
    House,
    PaymentRecord,
    UtilityReading,
)
from src.rentals.domain.exceptions import InvalidRentalsInputError
from src.rentals.domain.value_objects import (
    UNIT_COST_STEP,
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
    PaymentSummary,
    Period,
    Reading,
    UnitCost,
    UserId,
    UtilityBill,
    UtilityReadingId,
    UtilityType,
    _to_decimal,
)

#: Wire keys whose values may legitimately be ``null`` and must map to ``None``
#: instead of being defaulted to a non-null placeholder. Asserted against the
#: models (``blank=True, null=True``) and the serializers (``allow_null=True``).
NULLABLE_TEXT_FIELDS = frozenset({"description", "notes"})

#: Entity label used in error messages, keyed by the ``*DetailsSerializer``.
_ENTITY_LABELS = {
    "HouseDetailsSerializer": "House",
    "ApartmentDetailsSerializer": "Apartment",
    "DocumentDetailsSerializer": "Document",
    "UtilityReadingDetailsSerializer": "UtilityReading",
    "PaymentRecordDetailsSerializer": "PaymentRecord",
    "PaymentSummaryDetailsSerializer": "PaymentSummary",
    "UtilityBillDetailsSerializer": "UtilityBill",
}


# --------------------------------------------------------------------------- #
# Field readers
#
# Each reader distinguishes three failure modes the DRF contract allows:
# absent key, explicit null, and wrong type. Collapsing them into one would make
# a backend regression indistinguishable from bad client input.
# --------------------------------------------------------------------------- #
def _require(payload: dict[str, Any], key: str, entity: str) -> Any:
    """Fetch a required key, distinguishing absent from explicitly null."""
    if key not in payload:
        raise InvalidRentalsInputError(
            f"{entity} payload is missing required key '{key}'."
        )
    return payload[key]


def _uuid_value(payload: dict[str, Any], key: str, entity: str) -> UUID:
    """Parse a required UUID wire field.

    The DRF serializers emit UUIDs as canonical hyphenated strings (their
    ``source`` is ``id.value``), so parsing happens here; the identity value
    objects then only have to validate the type.
    """
    raw = _require(payload, key, entity)
    if raw is None:
        raise InvalidRentalsInputError(f"{entity} '{key}' must not be null.")
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidRentalsInputError(
            f"{entity} '{key}' is not a valid UUID: {raw!r}."
        ) from exc


def _datetime_field(payload: dict[str, Any], key: str, entity: str) -> datetime:
    """Parse a required timezone-aware ISO-8601 timestamp.

    DRF emits a trailing ``Z`` for UTC. ``datetime.fromisoformat`` accepts ``Z``
    from Python 3.11 onwards and this project requires 3.11+, but the
    substitution is kept so a backend that emits ``+0000`` without a colon, or
    a lowercase ``z``, still parses.
    """
    raw = _require(payload, key, entity)
    if raw is None:
        raise InvalidRentalsInputError(f"{entity} '{key}' must not be null.")
    text = str(raw)
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidRentalsInputError(
            f"{entity} '{key}' is not a valid timestamp: {raw!r}."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidRentalsInputError(
            f"{entity} '{key}' must be timezone-aware, got {raw!r}."
        )
    return parsed


def _date_field(payload: dict[str, Any], key: str, entity: str) -> date:
    """Parse a required ISO-8601 date (``YYYY-MM-DD``)."""
    raw = _require(payload, key, entity)
    if raw is None:
        raise InvalidRentalsInputError(f"{entity} '{key}' must not be null.")
    text = str(raw)
    # Checked before `fromisoformat`, which would otherwise raise first and
    # report the less useful "not a valid date" for what is really a datetime
    # sent to a date field.
    if "T" in text:
        raise InvalidRentalsInputError(
            f"{entity} '{key}' must be a date, not a datetime: {raw!r}."
        )
    try:
        return date.fromisoformat(text)
    except (ValueError, TypeError) as exc:
        raise InvalidRentalsInputError(
            f"{entity} '{key}' is not a valid date: {raw!r}."
        ) from exc


def _text_field(payload: dict[str, Any], key: str, entity: str) -> str:
    """Read a required non-null string."""
    raw = _require(payload, key, entity)
    if raw is None:
        raise InvalidRentalsInputError(f"{entity} '{key}' must not be null.")
    return str(raw)


def _nullable_text_field(payload: dict[str, Any], key: str, entity: str) -> str | None:
    """Read an optional string, mapping both ``null`` and absence to ``None``.

    Covers the ``blank=True, null=True`` columns (``Document.description``,
    ``PaymentRecord.notes``). A blank string is additionally collapsed to
    ``None`` by the value objects, so an empty ``description`` never round-trips
    as ``""`` into a template.
    """
    raw = payload.get(key)
    return None if raw is None else str(raw)


def _int_field(payload: dict[str, Any], key: str, entity: str) -> int:
    """Read a required integer, rejecting a JSON ``true``/``false``.

    ``bool`` is an ``int`` subclass, so without the guard a boolean would
    silently become 0 or 1 instead of reporting a contract change.
    """
    raw = _require(payload, key, entity)
    if raw is None:
        raise InvalidRentalsInputError(f"{entity} '{key}' must not be null.")
    if isinstance(raw, bool):
        raise InvalidRentalsInputError(
            f"{entity} '{key}' must be an integer, got a boolean."
        )
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidRentalsInputError(
            f"{entity} '{key}' is not a valid integer: {raw!r}."
        ) from exc


def _decimal_field(payload: dict[str, Any], key: str, entity: str) -> Decimal:
    """Read a required decimal wire field as a ``Decimal``.

    Accepts DRF's string form and an already-numeric value; delegates the
    coercion (including the ``bool`` rejection and the finiteness check) to the
    shared ``_to_decimal`` helper. The per-field scale and sign rules are then
    enforced by the value object the caller constructs.
    """
    raw = _require(payload, key, entity)
    if raw is None:
        raise InvalidRentalsInputError(f"{entity} '{key}' must not be null.")
    return _to_decimal(raw, f"{entity} {key}", InvalidRentalsInputError)


def _enum_field(payload: dict[str, Any], key: str, enum_cls: Any, entity: str) -> Any:
    """Read a required enum-valued string.

    An unknown member is a backend change, so it is reported as invalid rentals
    input rather than coerced or defaulted -- defaulting would let a renamed
    enum member render as the wrong label in the UI with no signal.
    """
    raw = _text_field(payload, key, entity)
    try:
        return enum_cls(raw)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise InvalidRentalsInputError(
            f"{entity} '{key}' value {raw!r} is not one of: {allowed}."
        ) from exc


def _as_payload_list(payload: Any, entity: str) -> list[dict[str, Any]]:
    """Coerce a list endpoint's payload into a list of dicts.

    A dict is rejected with an actionable message rather than treated as a
    single item, because the realistic cause is DRF pagination being switched on
    globally -- which would otherwise yield an empty page of apartments with no
    error anywhere.
    """
    if payload is None:
        return []
    if isinstance(payload, dict):
        raise InvalidRentalsInputError(
            f"{entity} list must be a JSON array, but the payload is an object "
            f"with keys {sorted(payload)[:5]}. If the backend started "
            f"paginating, unwrap the results envelope here."
        )
    if not isinstance(payload, list):
        raise InvalidRentalsInputError(
            f"{entity} list must be a JSON array, got {type(payload).__name__}."
        )
    items: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise InvalidRentalsInputError(
                f"{entity} list item {index} must be an object, got "
                f"{type(item).__name__}."
            )
        items.append(item)
    return items


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #
def map_house(payload: dict[str, Any]) -> House:
    """Map one ``HouseDetailsSerializer`` payload to a `House`.

    The serializer flattens the address into ``street``/``city``/``state``/
    ``country`` rather than nesting an object, so `Address` is rebuilt here.
    """
    entity = _ENTITY_LABELS["HouseDetailsSerializer"]
    return House(
        id=HouseId(_uuid_value(payload, "id", entity)),
        owner_id=UserId(_uuid_value(payload, "owner_id", entity)),
        name=_text_field(payload, "name", entity),
        address=Address(
            street=_text_field(payload, "street", entity),
            city=_text_field(payload, "city", entity),
            state=_text_field(payload, "state", entity),
            country=_text_field(payload, "country", entity),
        ),
        created_at=_datetime_field(payload, "created_at", entity),
        updated_at=_datetime_field(payload, "updated_at", entity),
    )


def map_houses_list(payload: Any) -> list[House]:
    """Map the bare JSON array returned by ``GET /rentals/houses/``."""
    return [map_house(item) for item in _as_payload_list(payload, "House")]


def map_apartment(payload: dict[str, Any]) -> Apartment:
    """Map one ``ApartmentDetailsSerializer`` payload to an `Apartment`."""
    entity = _ENTITY_LABELS["ApartmentDetailsSerializer"]
    return Apartment(
        id=ApartmentId(_uuid_value(payload, "id", entity)),
        house_id=HouseId(_uuid_value(payload, "house_id", entity)),
        number=ApartmentNumber(_text_field(payload, "number", entity)),
        floor=_int_field(payload, "floor", entity),
        monthly_rent=MonthlyRent(_decimal_field(payload, "monthly_rent", entity)),
        created_at=_datetime_field(payload, "created_at", entity),
        updated_at=_datetime_field(payload, "updated_at", entity),
    )


def map_apartments_list(payload: Any) -> list[Apartment]:
    """Map the bare JSON array returned by ``GET /rentals/apartments/``."""
    return [map_apartment(item) for item in _as_payload_list(payload, "Apartment")]


def map_document(payload: dict[str, Any]) -> Document:
    """Map one ``DocumentDetailsSerializer`` payload to a `Document`.

    `description` is nullable. `file_url` is re-validated as an absolute URL by
    the entity, so a row that predates the URLField constraint cannot reach a
    template with a bare path.
    """
    entity = _ENTITY_LABELS["DocumentDetailsSerializer"]
    return Document(
        id=DocumentId(_uuid_value(payload, "id", entity)),
        apartment_id=ApartmentId(_uuid_value(payload, "apartment_id", entity)),
        document_type=_enum_field(payload, "document_type", DocumentType, entity),
        file_url=_text_field(payload, "file_url", entity),
        description=_nullable_text_field(payload, "description", entity),
        uploaded_at=_datetime_field(payload, "uploaded_at", entity),
    )


def map_documents_list(payload: Any) -> list[Document]:
    """Map the bare JSON array from the apartment documents endpoint."""
    return [map_document(item) for item in _as_payload_list(payload, "Document")]


def map_utility_reading(payload: dict[str, Any]) -> UtilityReading:
    """Map one ``UtilityReadingDetailsSerializer`` payload.

    `consumption` and `total_cost` are mapped verbatim rather than recomputed:
    the backend is the authority for a persisted row. The entity still re-checks
    that `consumption` is a valid `Reading` and that `current >= previous`, so a
    corrupt row surfaces here instead of reaching the UI.
    """
    entity = _ENTITY_LABELS["UtilityReadingDetailsSerializer"]
    return UtilityReading(
        id=UtilityReadingId(_uuid_value(payload, "id", entity)),
        apartment_id=ApartmentId(_uuid_value(payload, "apartment_id", entity)),
        utility_type=_enum_field(payload, "utility_type", UtilityType, entity),
        reading_date=_date_field(payload, "reading_date", entity),
        current_reading=Reading(_decimal_field(payload, "current_reading", entity)),
        previous_reading=Reading(_decimal_field(payload, "previous_reading", entity)),
        consumption=Reading(_decimal_field(payload, "consumption", entity)),
        unit_cost=UnitCost(_decimal_field(payload, "unit_cost", entity)),
        # A plain Decimal, not a `Reading`: this is a money total computed by
        # the backend, not a meter quantity bound by the 2-decimal scale.
        total_cost=_decimal_field(payload, "total_cost", entity),
        created_at=_datetime_field(payload, "created_at", entity),
    )


def map_utility_readings_list(payload: Any) -> list[UtilityReading]:
    """Map the bare JSON array from the apartment utilities endpoint."""
    return [
        map_utility_reading(item)
        for item in _as_payload_list(payload, "UtilityReading")
    ]


def map_payment_record(payload: dict[str, Any]) -> PaymentRecord:
    """Map one ``PaymentRecordDetailsSerializer`` payload.

    `notes` is nullable. `status` is mapped verbatim because the backend may
    report ``PENDING`` or ``OVERDUE``, neither of which can be derived from the
    amount alone without the apartment's monthly rent.
    """
    entity = _ENTITY_LABELS["PaymentRecordDetailsSerializer"]
    return PaymentRecord(
        id=PaymentRecordId(_uuid_value(payload, "id", entity)),
        apartment_id=ApartmentId(_uuid_value(payload, "apartment_id", entity)),
        payment_date=_date_field(payload, "payment_date", entity),
        amount=PaymentAmount(_decimal_field(payload, "amount", entity)),
        status=_enum_field(payload, "status", PaymentStatus, entity),
        notes=_nullable_text_field(payload, "notes", entity),
        created_at=_datetime_field(payload, "created_at", entity),
    )


def map_payment_records_list(payload: Any) -> list[PaymentRecord]:
    """Map the bare JSON array from the apartment payments endpoint."""
    return [
        map_payment_record(item) for item in _as_payload_list(payload, "PaymentRecord")
    ]


def map_payment_summary(payload: dict[str, Any]) -> PaymentSummary:
    """Map one ``PaymentSummaryDetailsSerializer`` payload.

    The endpoint requires `year` and `month` query parameters and echoes both in
    the body, so the period is rebuilt from the response rather than trusted
    from the caller's request.
    """
    entity = _ENTITY_LABELS["PaymentSummaryDetailsSerializer"]
    return PaymentSummary(
        apartment_id=ApartmentId(_uuid_value(payload, "apartment_id", entity)),
        period=Period(
            year=_int_field(payload, "year", entity),
            month=_int_field(payload, "month", entity),
        ),
        total_paid=_decimal_field(payload, "total_paid", entity),
        outstanding_balance=_decimal_field(payload, "outstanding_balance", entity),
        payment_count=_int_field(payload, "payment_count", entity),
    )


def map_utility_bill(payload: dict[str, Any]) -> UtilityBill:
    """Map one ``UtilityBillDetailsSerializer`` payload."""
    entity = _ENTITY_LABELS["UtilityBillDetailsSerializer"]
    return UtilityBill(
        apartment_id=ApartmentId(_uuid_value(payload, "apartment_id", entity)),
        utility_type=_enum_field(payload, "utility_type", UtilityType, entity),
        period=Period(
            year=_int_field(payload, "year", entity),
            month=_int_field(payload, "month", entity),
        ),
        total_consumption=Reading(_decimal_field(payload, "total_consumption", entity)),
        total_cost=_decimal_field(payload, "total_cost", entity),
        reading_count=_int_field(payload, "reading_count", entity),
    )


def map_period(payload: dict[str, Any], entity: str = "Period") -> Period:
    """Build a `Period` from a flat ``year``/``month`` pair.

    Exposed because the summary and bill endpoints both return the pair at the
    top level, and a view that already has the fields should not need to build
    the `Period` itself just to re-read it.
    """
    return Period(
        year=_int_field(payload, "year", entity),
        month=_int_field(payload, "month", entity),
    )


__all__ = [
    "NULLABLE_TEXT_FIELDS",
    "UNIT_COST_STEP",
    "map_apartment",
    "map_apartments_list",
    "map_document",
    "map_documents_list",
    "map_house",
    "map_houses_list",
    "map_payment_record",
    "map_payment_records_list",
    "map_payment_summary",
    "map_period",
    "map_utility_bill",
    "map_utility_reading",
    "map_utility_readings_list",
]
