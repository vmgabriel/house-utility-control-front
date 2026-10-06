"""Create a rental property."""

from dataclasses import dataclass

from src.rentals.application.ports import RentalsApiClient
from src.rentals.domain.entities import House
from src.rentals.domain.exceptions import InvalidRentalsInputError
from src.rentals.domain.value_objects import Address


@dataclass(frozen=True, slots=True)
class CreateHouseUseCase:
    """Validate the address, then register the house through the port.

    Validation happens here before the request so a blank or over-long form
    field becomes a flash message instead of a round trip that returns a DRF
    `ValidationError`. Constructing the `Address` value object is what enforces
    the per-field limits the backend also enforces in
    `CreateHouseSerializer`: name/street <= 200, city/state/country <= 100,
    none blank.

    The backend derives the owner from the JWT, so no owner is passed.
    """

    rentals_client: RentalsApiClient

    async def execute(
        self,
        access_token: str,
        *,
        name: str,
        street: str,
        city: str,
        state: str,
        country: str,
    ) -> House:
        for label, value in (
            ("name", name),
            ("street", street),
            ("city", city),
            ("state", state),
            ("country", country),
        ):
            if not (value or "").strip():
                raise InvalidRentalsInputError(f"{label} is required.")

        # The value object rejects blanks and over-long fields, raising
        # InvalidAddressError -- already a RentalsDomainError, so it renders.
        address = Address(street=street, city=city, state=state, country=country)

        return await self.rentals_client.create_house(
            access_token,
            name=" ".join(name.split()),
            street=address.street,
            city=address.city,
            state=address.state,
            country=address.country,
        )
