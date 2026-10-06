"""Record a utility meter reading for an apartment."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.rentals.application.ports import RentalsApiClient
from src.rentals.domain.entities import UtilityReading
from src.rentals.domain.exceptions import (
    InvalidReadingError,
    InvalidRentalsInputError,
)
from src.rentals.domain.value_objects import (
    ApartmentId,
    Reading,
    UnitCost,
    UtilityType,
)
from src.shared.domain.ports.clock import Clock


@dataclass(frozen=True, slots=True)
class RecordUtilityReadingUseCase:
    """Validate a meter reading, then record it through the port.

    Two invariants are checked here, ahead of the request, so an impossible
    reading never reaches the network:

    ``current >= previous``
        The backend enforces the same rule with a ``CheckConstraint``
        (``utility_reading_current_gte_previous``), so failing here turns a 400
        into a domain error the view can flash.

    ``reading_date`` is not in the future
        Mirrors the rule the transactions context already applies
        (``InvalidTransactionDateError``) via the injected `Clock`, which also
        keeps the use case testable with a frozen clock instead of ``date.today``.

    `ApartmentNotFoundError` and `UtilityReadingNotFoundError` from the port
    propagate untouched: a missing apartment is a real backend answer, not a
    validation failure.
    """

    rentals_client: RentalsApiClient
    clock: Clock

    async def execute(
        self,
        access_token: str,
        *,
        apartment_id: ApartmentId,
        utility_type: UtilityType,
        reading_date: date,
        current: Decimal,
        previous: Decimal,
        unit_cost: Decimal,
    ) -> UtilityReading:
        if not isinstance(apartment_id, ApartmentId):
            raise InvalidRentalsInputError("apartment_id must be an ApartmentId.")
        if not isinstance(utility_type, UtilityType):
            raise InvalidRentalsInputError("utility_type must be a UtilityType.")
        if type(reading_date) is not date:
            raise InvalidRentalsInputError("reading_date must be a date.")
        if reading_date > self.clock.today():
            raise InvalidRentalsInputError("reading_date cannot be in the future.")

        # The VOs reject negatives, over-precision, and out-of-range values with
        # domain errors that are already safe to show a user.
        current_reading = Reading(current)
        previous_reading = Reading(previous)
        tariff = UnitCost(unit_cost)

        if current_reading.amount < previous_reading.amount:
            raise InvalidReadingError(
                "Current reading cannot be lower than the previous reading."
            )

        return await self.rentals_client.record_utility_reading(
            access_token,
            apartment_id=apartment_id,
            utility_type=utility_type,
            reading_date=reading_date,
            current=current_reading.amount,
            previous=previous_reading.amount,
            unit_cost=tariff.amount,
        )
