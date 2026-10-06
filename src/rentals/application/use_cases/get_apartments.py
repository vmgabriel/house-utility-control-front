"""List the apartments belonging to one house."""

from dataclasses import dataclass

from src.rentals.application.ports import RentalsApiClient
from src.rentals.domain.entities import Apartment
from src.rentals.domain.exceptions import InvalidRentalsInputError
from src.rentals.domain.value_objects import HouseId


@dataclass(frozen=True, slots=True)
class GetApartmentsUseCase:
    """Fetch the apartments of `house_id`.

    The DRF endpoint requires `house_id` as a query parameter and returns a bare
    JSON array; both concerns belong to the adapter.
    """

    rentals_client: RentalsApiClient

    async def execute(self, access_token: str, house_id: HouseId) -> list[Apartment]:
        if not isinstance(house_id, HouseId):
            raise InvalidRentalsInputError("house_id must be a HouseId.")
        return await self.rentals_client.get_apartments(access_token, house_id)
