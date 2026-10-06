"""httpx adapter implementing the rentals `RentalsApiClient` port.

The adapter does three things and nothing else:

1. Delegates the round trip to the shared :class:`DRFAPIClient`, so the
   base URL, JSON headers, Bearer token, transport-error handling, and the
   ``ServiceUnavailableError`` contract are identical to every other context.
   No second ``httpx.AsyncClient`` is created here.
2. Hands the parsed body to the mappers in
   :mod:`src.rentals.application.mappers`, which take plain ``dict`` objects --
   that is what keeps the mapping logic free of httpx while still living in one
   place.
3. Translates transport-level statuses into domain errors, so a view can catch
   ``ApartmentNotFoundError`` and render a 404 without knowing that DRF speaks
   in 404s.

Status handling differs by intent, and the difference matters:

* A ``404`` on a *list* endpoint is not a missing resource -- it means the
  nested apartment does not belong to the caller, which the backend signals
  with ``HouseNotFound``/``ApartmentNotFound`` mapped to 404. Same exception,
  same rendering.
* A ``400`` is a ``ValidationError`` from DRF. It is *not* converted into a
  domain error, because the use cases already rejected the equivalent input
  locally; if one still reaches DRF it means a rule exists only server-side,
  and the field-level detail is worth surfacing verbatim rather than flattening
  into a generic message.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.infrastructure.api.drf_client import (
    DRFAPIClient,
    NotFoundError,
    UnauthorizedError,
)
from src.rentals.application.mappers import (
    map_apartment,
    map_apartments_list,
    map_document,
    map_documents_list,
    map_house,
    map_houses_list,
    map_payment_record,
    map_payment_records_list,
    map_payment_summary,
    map_utility_bill,
    map_utility_reading,
    map_utility_readings_list,
)
from src.rentals.domain.entities import (
    Apartment,
    Document,
    House,
    PaymentRecord,
    UtilityReading,
)
from src.rentals.domain.exceptions import (
    ApartmentNotFoundError,
    HouseNotFoundError,
)
from src.rentals.domain.value_objects import (
    ApartmentId,
    ApartmentNumber,
    DocumentType,
    HouseId,
    MonthlyRent,
    PaymentSummary,
    Period,
    UtilityBill,
    UtilityType,
)


class DrfRentalsClient:
    """Rentals adapter over the shared DRF API client.

    Every method takes ``access_token`` first and positionally: the
    ``AutoRefreshingRepository`` proxy that wraps this object identifies the
    token that way, and substitutes a refreshed one on ``args[0]`` after a 401.
    """

    def __init__(self, api_client: DRFAPIClient) -> None:
        self.api_client = api_client

    # ------------------------------------------------------------------ #
    # Houses
    # ------------------------------------------------------------------ #
    async def get_houses(self, access_token: str) -> list[House]:
        body = await self.api_client.list_rentals_houses(access_token)
        return map_houses_list(body)

    async def create_house(
        self,
        access_token: str,
        *,
        name: str,
        street: str,
        city: str,
        state: str,
        country: str,
    ) -> House:
        payload = {
            "name": name,
            "street": street,
            "city": city,
            "state": state,
            "country": country,
        }
        body = await self.api_client.create_rentals_house(access_token, payload)
        return map_house(body)

    # ------------------------------------------------------------------ #
    # Apartments
    # ------------------------------------------------------------------ #
    async def get_apartments(
        self, access_token: str, house_id: HouseId
    ) -> list[Apartment]:
        body = await self.api_client.list_rentals_apartments(
            access_token, str(house_id)
        )
        return map_apartments_list(body)

    async def create_apartment(
        self,
        access_token: str,
        *,
        house_id: HouseId,
        number: ApartmentNumber,
        floor: int,
        monthly_rent: MonthlyRent,
    ) -> Apartment:
        payload = {
            "house_id": str(house_id),
            "number": str(number),
            "floor": floor,
            # Sent as a string to match DRF's decimal wire format exactly;
            # floats would be at the mercy of binary rounding.
            "monthly_rent": str(monthly_rent.amount),
        }
        body = await self.api_client.create_rentals_apartment(access_token, payload)
        return map_apartment(body)

    async def get_apartment(
        self, access_token: str, apartment_id: ApartmentId
    ) -> Apartment:
        try:
            body = await self.api_client.get_rentals_apartment(
                access_token, str(apartment_id)
            )
        except NotFoundError as exc:
            raise ApartmentNotFoundError(
                "Apartment not found or not accessible."
            ) from exc
        return map_apartment(body)

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #
    async def record_utility_reading(
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
        # `consumption` and `total_cost` are deliberately absent: the backend
        # derives both, and RecordUtilityReadingSerializer has no field for them.
        payload = {
            "utility_type": utility_type.value,
            "reading_date": reading_date.isoformat(),
            "current_reading": str(current),
            "previous_reading": str(previous),
            "unit_cost": str(unit_cost),
        }
        try:
            body = await self.api_client.create_rentals_utility_reading(
                access_token, str(apartment_id), payload
            )
        except NotFoundError as exc:
            raise ApartmentNotFoundError(
                "Apartment not found or not accessible."
            ) from exc
        return map_utility_reading(body)

    async def get_utility_readings(
        self, access_token: str, apartment_id: ApartmentId
    ) -> list[UtilityReading]:
        try:
            body = await self.api_client.list_rentals_utility_readings(
                access_token, str(apartment_id)
            )
        except NotFoundError as exc:
            raise ApartmentNotFoundError(
                "Apartment not found or not accessible."
            ) from exc
        return map_utility_readings_list(body)

    async def get_utility_bill(
        self,
        access_token: str,
        apartment_id: ApartmentId,
        utility_type: UtilityType,
        period: Period,
    ) -> UtilityBill:
        try:
            body = await self.api_client.get_rentals_utility_bill(
                access_token,
                str(apartment_id),
                utility_type.value,
                period.year,
                period.month,
            )
        except NotFoundError as exc:
            raise ApartmentNotFoundError(
                "Apartment not found or not accessible."
            ) from exc
        return map_utility_bill(body)

    # ------------------------------------------------------------------ #
    # Payments
    # ------------------------------------------------------------------ #
    async def get_payment_summary(
        self,
        access_token: str,
        apartment_id: ApartmentId,
        period: Period,
    ) -> PaymentSummary:
        try:
            body = await self.api_client.get_rentals_payment_summary(
                access_token, str(apartment_id), period.year, period.month
            )
        except NotFoundError as exc:
            raise ApartmentNotFoundError(
                "Apartment not found or not accessible."
            ) from exc
        return map_payment_summary(body)

    async def get_payment_records(
        self, access_token: str, apartment_id: ApartmentId
    ) -> list[PaymentRecord]:
        try:
            body = await self.api_client.list_rentals_payment_records(
                access_token, str(apartment_id)
            )
        except NotFoundError as exc:
            raise ApartmentNotFoundError(
                "Apartment not found or not accessible."
            ) from exc
        return map_payment_records_list(body)

    async def record_payment(
        self,
        access_token: str,
        *,
        apartment_id: ApartmentId,
        payment_date: date,
        amount: Decimal,
        notes: str | None,
    ) -> PaymentRecord:
        payload: dict[str, object] = {
            "payment_date": payment_date.isoformat(),
            "amount": str(amount),
        }
        if notes is not None:
            # Omitted rather than sent as null: RecordPaymentSerializer declares
            # `notes` with required=False and allow_blank, and a null would be
            # rejected as a validation error.
            payload["notes"] = notes
        try:
            body = await self.api_client.create_rentals_payment(
                access_token, str(apartment_id), payload
            )
        except NotFoundError as exc:
            raise ApartmentNotFoundError(
                "Apartment not found or not accessible."
            ) from exc
        return map_payment_record(body)

    # ------------------------------------------------------------------ #
    # Documents
    # ------------------------------------------------------------------ #
    async def get_documents(
        self, access_token: str, apartment_id: ApartmentId
    ) -> list[Document]:
        try:
            body = await self.api_client.list_rentals_documents(
                access_token, str(apartment_id)
            )
        except NotFoundError as exc:
            raise ApartmentNotFoundError(
                "Apartment not found or not accessible."
            ) from exc
        return map_documents_list(body)

    async def register_document(
        self,
        access_token: str,
        *,
        apartment_id: ApartmentId,
        document_type: DocumentType,
        file_url: str,
        description: str | None,
    ) -> Document:
        payload: dict[str, object] = {
            "document_type": document_type.value,
            "file_url": file_url,
        }
        if description is not None:
            # Same reason as `notes` above: the serializer wants a blank string
            # for "no description", not a null.
            payload["description"] = description
        try:
            body = await self.api_client.create_rentals_document(
                access_token, str(apartment_id), payload
            )
        except NotFoundError as exc:
            raise ApartmentNotFoundError(
                "Apartment not found or not accessible."
            ) from exc
        return map_document(body)


__all__ = ["DrfRentalsClient", "HouseNotFoundError", "UnauthorizedError"]
