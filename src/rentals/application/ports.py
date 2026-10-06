"""Application-layer ports for the rentals context.

A port describes what the rentals use cases *need* from the outside world, in
terms of domain objects only. The concrete adapter (an httpx client speaking
the DRF rentals API) lives in ``src/rentals/infrastructure`` and is injected in
the composition root, so the use cases stay framework-free per AGENTS.md.

Two conventions hold across every method:

**The access token is the first positional argument.** This is a hard
requirement, not a style choice. ``AutoRefreshingRepository`` -- the existing
proxy that refreshes an expired JWT and retries once on ``401`` -- identifies
the token by position and substitutes the new one into ``args[0]``. A port that
put a ``HouseId`` or ``UserId`` first would have a refreshed JWT silently
written into that slot. Every rentals adapter is wrapped in that proxy, so
``DRF_API_BASE_URL``, JWT handling, and refresh all come for free.

**Ownership is never passed.** The backend derives the caller from the JWT
(``_authenticated_user_id(request)`` reads ``request.user.pk``) on every rentals
endpoint, and no rentals view accepts an owner or user query parameter. Sending
one would be ignored, so it is not part of the contract here either.
"""

from datetime import date
from decimal import Decimal
from typing import Protocol

from src.rentals.domain.entities import (
    Apartment,
    Document,
    House,
    PaymentRecord,
    UtilityReading,
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


class RentalsApiClient(Protocol):
    """Outbound port for the DRF rentals API."""

    async def get_houses(self, access_token: str) -> list[House]:
        """List every house owned by the authenticated user.

        Returns a bare JSON array, not a paginated envelope: the rentals views
        are plain ``APIView`` classes, so DRF's default pagination does not
        apply to them.
        """
        ...

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
        """Create a house owned by the authenticated user.

        Accepts the flattened address that ``CreateHouseSerializer`` expects.
        """
        ...

    async def get_apartments(
        self, access_token: str, house_id: HouseId
    ) -> list[Apartment]:
        """List the apartments of one house.

        Requires ``house_id`` as a query parameter and returns a bare array.
        """
        ...

    async def create_apartment(
        self,
        access_token: str,
        *,
        house_id: HouseId,
        number: ApartmentNumber,
        floor: int,
        monthly_rent: MonthlyRent,
    ) -> Apartment:
        """Create an apartment in `house_id`.

        Takes value objects rather than raw scalars: the use case has already
        validated them, so the adapter forwards them straight to
        ``CreateApartmentSerializer``.
        """
        ...

    async def get_apartment(
        self, access_token: str, apartment_id: ApartmentId
    ) -> Apartment:
        """Fetch one apartment."""
        ...

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
        """Record a meter reading.

        `consumption` and `total_cost` are computed server-side from these four
        inputs; the adapter must not send them.
        """
        ...

    async def get_utility_readings(
        self, access_token: str, apartment_id: ApartmentId
    ) -> list[UtilityReading]:
        """List an apartment's readings, newest first."""
        ...

    async def get_utility_bill(
        self,
        access_token: str,
        apartment_id: ApartmentId,
        utility_type: UtilityType,
        period: Period,
    ) -> UtilityBill:
        """Aggregate one utility's consumption and cost over `period`."""
        ...

    async def get_payment_summary(
        self,
        access_token: str,
        apartment_id: ApartmentId,
        period: Period,
    ) -> PaymentSummary:
        """Aggregate one apartment's payments over `period`."""
        ...

    async def get_payment_records(
        self, access_token: str, apartment_id: ApartmentId
    ) -> list[PaymentRecord]:
        """List an apartment's payments, newest first."""
        ...

    async def record_payment(
        self,
        access_token: str,
        *,
        apartment_id: ApartmentId,
        payment_date: date,
        amount: Decimal,
        notes: str | None,
    ) -> PaymentRecord:
        """Record a rent payment.

        ``RecordPaymentSerializer`` takes only these four fields: the status is
        derived server-side by comparing the amount against the apartment's
        monthly rent, so sending one would be rejected as an unknown field.
        """
        ...

    async def get_documents(
        self, access_token: str, apartment_id: ApartmentId
    ) -> list[Document]:
        """List an apartment's documents, newest first."""
        ...

    async def register_document(
        self,
        access_token: str,
        *,
        apartment_id: ApartmentId,
        document_type: DocumentType,
        file_url: str,
        description: str | None,
    ) -> Document:
        """Record the reference to a file already uploaded to Nextcloud.

        The BFF never receives file bytes: the frontend PUTs directly to the
        Nextcloud public WebDAV endpoint and passes the resulting absolute URL.
        `description` is optional, matching the nullable model column.
        """
        ...
