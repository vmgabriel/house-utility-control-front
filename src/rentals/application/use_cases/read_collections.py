"""Read the collections that make up the apartment hub."""

from dataclasses import dataclass

from src.rentals.application.ports import RentalsApiClient
from src.rentals.domain.entities import (
    Apartment,
    Document,
    PaymentRecord,
    UtilityReading,
)
from src.rentals.domain.exceptions import InvalidRentalsInputError
from src.rentals.domain.value_objects import ApartmentId


@dataclass(frozen=True, slots=True)
class GetApartmentUseCase:
    """Fetch one apartment for the hub header."""

    rentals_client: RentalsApiClient

    async def execute(self, access_token: str, apartment_id: ApartmentId) -> Apartment:
        if not isinstance(apartment_id, ApartmentId):
            raise InvalidRentalsInputError("apartment_id must be an ApartmentId.")
        return await self.rentals_client.get_apartment(access_token, apartment_id)


@dataclass(frozen=True, slots=True)
class GetUtilityReadingsUseCase:
    """List an apartment's readings, newest first."""

    rentals_client: RentalsApiClient

    async def execute(
        self, access_token: str, apartment_id: ApartmentId
    ) -> list[UtilityReading]:
        if not isinstance(apartment_id, ApartmentId):
            raise InvalidRentalsInputError("apartment_id must be an ApartmentId.")
        return await self.rentals_client.get_utility_readings(
            access_token, apartment_id
        )


@dataclass(frozen=True, slots=True)
class GetPaymentRecordsUseCase:
    """List an apartment's payments, newest first."""

    rentals_client: RentalsApiClient

    async def execute(
        self, access_token: str, apartment_id: ApartmentId
    ) -> list[PaymentRecord]:
        if not isinstance(apartment_id, ApartmentId):
            raise InvalidRentalsInputError("apartment_id must be an ApartmentId.")
        return await self.rentals_client.get_payment_records(access_token, apartment_id)


@dataclass(frozen=True, slots=True)
class GetDocumentsUseCase:
    """List an apartment's document references, newest first."""

    rentals_client: RentalsApiClient

    async def execute(
        self, access_token: str, apartment_id: ApartmentId
    ) -> list[Document]:
        if not isinstance(apartment_id, ApartmentId):
            raise InvalidRentalsInputError("apartment_id must be an ApartmentId.")
        return await self.rentals_client.get_documents(access_token, apartment_id)
