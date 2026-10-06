"""Create an apartment inside a house."""

from dataclasses import dataclass
from decimal import Decimal

from src.rentals.application.ports import RentalsApiClient
from src.rentals.domain.entities import Apartment
from src.rentals.domain.exceptions import InvalidRentalsInputError
from src.rentals.domain.value_objects import ApartmentNumber, HouseId, MonthlyRent


@dataclass(frozen=True, slots=True)
class CreateApartmentUseCase:
    """Validate apartment input, then register it through the port.

    Validation happens here rather than only in the value objects so the caller
    receives the same `InvalidRentalsInputError` for a blank form field as for a
    bad type, and so nothing reaches the network before the request is known to be
    well-formed. `HouseNotFoundError` and `ApartmentNotFoundError` from the port
    propagate untouched, since a missing house is a real answer from the backend
    rather than a validation failure.
    """

    rentals_client: RentalsApiClient

    async def execute(
        self,
        access_token: str,
        *,
        house_id: HouseId,
        number: str,
        floor: int,
        monthly_rent: Decimal,
    ) -> Apartment:
        if not isinstance(house_id, HouseId):
            raise InvalidRentalsInputError("house_id must be a HouseId.")
        if isinstance(floor, bool) or not isinstance(floor, int):
            raise InvalidRentalsInputError("floor must be an integer.")
        if floor < 0:
            raise InvalidRentalsInputError("floor cannot be negative.")
        if monthly_rent <= 0:
            raise InvalidRentalsInputError("monthly_rent must be greater than zero.")
        # Constructing the VOs here is what validates the shape and length
        # limits; any domain error they raise is already the right type to show.
        apartment_number = ApartmentNumber(number)
        rent = MonthlyRent(monthly_rent)

        return await self.rentals_client.create_apartment(
            access_token,
            house_id=house_id,
            number=apartment_number,
            floor=floor,
            monthly_rent=rent,
        )
