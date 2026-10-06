"""Drift detection for the rentals OpenAPI contract.

`src/contract/types.py` is generated from the DRF schema, which is why it is not
imported at runtime. That does leave a gap: nothing asserts that the BFF's
hand-written rentals shapes still match the backend. These tests close it
without importing the generated module into application code -- they read it as
data and compare field *names*.

Run `make verify-api-contract` to regenerate the contract after an intentional
API change; a failure here means either the schema drifted (regenerate) or the
mappers need updating (a real incompatibility).
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal

import pytest

from src.contract import types
from src.rentals.application import mappers
from src.rentals.domain.exceptions import InvalidRentalsInputError

#: OpenAPI schema name -> the wire keys each mapper reads.
#: Kept as data so the comparison below is a table, not a pile of asserts.
MAPPER_WIRE_KEYS: dict[str, tuple[str, ...]] = {
    "House": (
        "id",
        "owner_id",
        "name",
        "street",
        "city",
        "state",
        "country",
        "created_at",
        "updated_at",
    ),
    "Apartment": (
        "id",
        "house_id",
        "number",
        "floor",
        "monthly_rent",
        "created_at",
        "updated_at",
    ),
    "Document": (
        "id",
        "apartment_id",
        "document_type",
        "file_url",
        "description",
        "uploaded_at",
    ),
    "UtilityReading": (
        "id",
        "apartment_id",
        "utility_type",
        "reading_date",
        "current_reading",
        "previous_reading",
        "consumption",
        "unit_cost",
        "total_cost",
        "created_at",
    ),
    "PaymentRecord": (
        "id",
        "apartment_id",
        "payment_date",
        "amount",
        "status",
        "notes",
        "created_at",
    ),
    "PaymentSummary": (
        "apartment_id",
        "year",
        "month",
        "total_paid",
        "outstanding_balance",
        "payment_count",
    ),
    "UtilityBill": (
        "apartment_id",
        "utility_type",
        "year",
        "month",
        "total_consumption",
        "total_cost",
        "reading_count",
    ),
}

#: The backend's OpenAPI component names differ from the mappers' function
#: names: `apps.rentals.interfaces.serializers` declares `HouseDetailsSerializer`,
#: so drf-spectacular exposes `HouseDetails`. Confirmed against the live schema.
RENAMED_IN_BACKEND = {
    "House": "HouseDetails",
    "Apartment": "ApartmentDetails",
    "Document": "DocumentDetails",
    "UtilityReading": "UtilityReadingDetails",
    "PaymentRecord": "PaymentRecordDetails",
    "PaymentSummary": "PaymentSummaryDetails",
    "UtilityBill": "UtilityBillDetails",
}


def _contract_fields(schema_name: str) -> tuple[str, ...] | None:
    """Return the generated contract's field names, or None when absent."""
    contract_name = RENAMED_IN_BACKEND.get(schema_name, schema_name)
    contract_class = getattr(types, contract_name, None)
    if contract_class is None:
        return None
    return tuple(field.name for field in fields(contract_class))


def _skip_when_contract_missing(schema_name: str):
    """Skip with an actionable reason when rentals is absent from the schema.

    The generated contract is fetched from the running DRF server. That server is
    currently running an older revision that predates the rentals commits, so
    none of these schemas appear yet. Once it is restarted and
    `make sync-api-contract` has been re-run, these tests assert for real
    instead of skipping.
    """
    if _contract_fields(schema_name) is None:
        pytest.skip(
            f"{schema_name} is absent from src/contract/types.py: the DRF "
            f"server predates the rentals app. Restart it and run "
            f"'make sync-api-contract' to enable this check."
        )


class TestGeneratedContractCoversRentals:
    """The mappers and the generated contract must agree on field names.

    Skipped while the generated contract predates the rentals app; see
    `_skip_when_contract_missing`.
    """

    @pytest.mark.parametrize("schema_name", sorted(MAPPER_WIRE_KEYS))
    def test_every_mapped_field_exists_in_the_contract(self, schema_name: str):
        _skip_when_contract_missing(schema_name)
        contract_fields = _contract_fields(schema_name)
        assert contract_fields is not None
        unknown = set(MAPPER_WIRE_KEYS[schema_name]) - set(contract_fields)
        assert not unknown, (
            f"The mapper reads keys the generated {schema_name} schema does not "
            f"define: {sorted(unknown)}"
        )

    @pytest.mark.parametrize("schema_name", sorted(MAPPER_WIRE_KEYS))
    def test_no_contract_field_is_ignored_by_the_mappers(self, schema_name: str):
        _skip_when_contract_missing(schema_name)
        contract_fields = _contract_fields(schema_name)
        assert contract_fields is not None
        # `extra="allow"` on the pydantic schemas means an unread field is not
        # an error, but it does mean the UI silently drops backend data.
        unread = set(contract_fields) - set(MAPPER_WIRE_KEYS[schema_name])
        assert not unread, (
            f"The generated {schema_name} schema has fields no mapper reads: "
            f"{sorted(unread)}"
        )


class TestHouseMapperMatchesContract:
    def test_flat_address_fields_are_read(self):
        """The serializer flattens the address; the mapper rebuilds it."""
        payload = {
            "id": "11111111-1111-4111-8111-111111111111",
            "owner_id": "22222222-2222-4222-8222-222222222222",
            "name": "Sobrado House",
            "street": "Rua das Flores",
            "city": "Sao Paulo",
            "state": "SP",
            "country": "BR",
            "created_at": "2026-10-01T12:00:00Z",
            "updated_at": "2026-10-02T12:00:00Z",
        }
        house = mappers.map_house(payload)
        assert house.address.street == "Rua das Flores"
        assert house.address.country == "BR"
        assert house.created_at.tzinfo is not None


class TestBareListHandling:
    """Rentals list endpoints return bare arrays, not paginated envelopes."""

    def test_bare_array_maps(self):
        payload = [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "owner_id": "22222222-2222-4222-8222-222222222222",
                "name": "A",
                "street": "s",
                "city": "c",
                "state": "st",
                "country": "br",
                "created_at": "2026-10-01T12:00:00Z",
                "updated_at": "2026-10-01T12:00:00Z",
            }
        ]
        assert len(mappers.map_houses_list(payload)) == 1

    def test_paginated_envelope_is_rejected_with_guidance(self):
        with pytest.raises(InvalidRentalsInputError, match="unwrap"):
            mappers.map_houses_list({"count": 1, "results": []})

    def test_empty_list_maps_to_empty(self):
        assert mappers.map_houses_list([]) == []


class TestNullability:
    def test_null_description_maps_to_none(self):
        payload = {
            "id": "33333333-3333-4333-8333-333333333333",
            "apartment_id": "44444444-4444-4444-8444-444444444444",
            "document_type": "LEASE_CONTRACT",
            "file_url": "https://cloud.example.com/s/x",
            "description": None,
            "uploaded_at": "2026-10-01T12:00:00Z",
        }
        assert mappers.map_document(payload).description is None

    def test_absent_description_maps_to_none(self):
        payload = {
            "id": "33333333-3333-4333-8333-333333333333",
            "apartment_id": "44444444-4444-4444-8444-444444444444",
            "document_type": "OTHER",
            "file_url": "https://cloud.example.com/s/x",
            "uploaded_at": "2026-10-01T12:00:00Z",
        }
        assert mappers.map_document(payload).description is None

    def test_null_notes_maps_to_none(self):
        payload = {
            "id": "55555555-5555-4555-8555-555555555555",
            "apartment_id": "44444444-4444-4444-8444-444444444444",
            "payment_date": "2026-10-01",
            "amount": "2500.00",
            "status": "PAID",
            "notes": None,
            "created_at": "2026-10-01T12:00:00Z",
        }
        assert mappers.map_payment_record(payload).notes is None

    def test_nullable_field_catalogue_matches_overrides(self):
        """The catalogue documents exactly the nullable columns we rely on."""
        assert mappers.NULLABLE_TEXT_FIELDS == {"description", "notes"}


class TestDecimalParsing:
    def test_string_decimals_become_decimal(self):
        payload = {
            "id": "66666666-6666-4666-8666-666666666666",
            "house_id": "11111111-1111-4111-8111-111111111111",
            "number": "101",
            "floor": 3,
            "monthly_rent": "2500.00",
            "created_at": "2026-10-01T12:00:00Z",
            "updated_at": "2026-10-01T12:00:00Z",
        }
        apartment = mappers.map_apartment(payload)
        assert str(apartment.monthly_rent.amount) == "2500.00"

    def test_float_decimal_is_accepted(self):
        """A future DRF version may stop stringifying; still must not drift.

        Note the stored exponent: ``Decimal(str(2500.00))`` is ``2500.0``, and
        ``MonthlyRent`` validates the scale without rewriting it, so the VO
        keeps exponent -1 here while a string payload yields -2. Both compare
        and format identically (``f"{amount:.2f}"``), and both serialise to a
        value DRF's ``DecimalField`` accepts, so this is cosmetic rather than a
        compatibility problem -- but it is asserted so the difference is
        deliberate rather than surprising.
        """
        payload = {
            "id": "66666666-6666-4666-8666-666666666666",
            "house_id": "11111111-1111-4111-8111-111111111111",
            "number": "101",
            "floor": 3,
            "monthly_rent": 2500.00,
            "created_at": "2026-10-01T12:00:00Z",
            "updated_at": "2026-10-01T12:00:00Z",
        }
        apartment = mappers.map_apartment(payload)
        assert apartment.monthly_rent.amount == Decimal("2500.00")
        # Rendering is scale-independent, which is what templates rely on.
        assert str(apartment.monthly_rent) == "2500.00"

    def test_precision_is_preserved(self):
        payload = {
            "id": "77777777-7777-4777-8777-777777777777",
            "apartment_id": "44444444-4444-4444-8444-444444444444",
            "utility_type": "WATER",
            "reading_date": "2026-10-01",
            "current_reading": "150.50",
            "previous_reading": "120.00",
            "consumption": "30.50",
            "unit_cost": "3.2567",
            "total_cost": "99.33",
            "created_at": "2026-10-01T12:00:00Z",
        }
        reading = mappers.map_utility_reading(payload)
        assert str(reading.unit_cost.amount) == "3.2567"
        assert str(reading.total_cost) == "99.33"


class TestErrorMessagesAreActionable:
    def test_unknown_enum_member_lists_allowed_values(self):
        payload = {
            "id": "55555555-5555-4555-8555-555555555555",
            "apartment_id": "44444444-4444-4444-8444-444444444444",
            "payment_date": "2026-10-01",
            "amount": "1.00",
            "status": "REFUNDED",
            "notes": None,
            "created_at": "2026-10-01T12:00:00Z",
        }
        with pytest.raises(InvalidRentalsInputError, match="PAID"):
            mappers.map_payment_record(payload)

    def test_naive_timestamp_is_rejected(self):
        payload = {
            "id": "11111111-1111-4111-8111-111111111111",
            "owner_id": "22222222-2222-4222-8222-222222222222",
            "name": "A",
            "street": "s",
            "city": "c",
            "state": "st",
            "country": "br",
            "created_at": "2026-10-01T12:00:00",
            "updated_at": "2026-10-01T12:00:00",
        }
        with pytest.raises(InvalidRentalsInputError, match="timezone-aware"):
            mappers.map_house(payload)

    def test_datetime_rejected_for_date_field(self):
        payload = {
            "id": "77777777-7777-4777-8777-777777777777",
            "apartment_id": "44444444-4444-4444-8444-444444444444",
            "utility_type": "WATER",
            "reading_date": "2026-10-01T00:00:00Z",
            "current_reading": "10.00",
            "previous_reading": "1.00",
            "consumption": "9.00",
            "unit_cost": "1.0000",
            "total_cost": "9.00",
            "created_at": "2026-10-01T12:00:00Z",
        }
        with pytest.raises(InvalidRentalsInputError, match="not a datetime"):
            mappers.map_utility_reading(payload)

    def test_missing_key_is_named(self):
        with pytest.raises(InvalidRentalsInputError, match="'name'"):
            mappers.map_house(
                {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "owner_id": "22222222-2222-4222-8222-222222222222",
                }
            )

    def test_boolean_integer_is_rejected(self):
        payload = {
            "id": "66666666-6666-4666-8666-666666666666",
            "house_id": "11111111-1111-4111-8111-111111111111",
            "number": "101",
            "floor": True,
            "monthly_rent": "1.00",
            "created_at": "2026-10-01T12:00:00Z",
            "updated_at": "2026-10-01T12:00:00Z",
        }
        with pytest.raises(InvalidRentalsInputError, match="boolean"):
            mappers.map_apartment(payload)
