"""Fetch an apartment's rent payment summary for a calendar month."""

from dataclasses import dataclass

from src.rentals.application.ports import RentalsApiClient
from src.rentals.domain.exceptions import InvalidRentalsInputError
from src.rentals.domain.value_objects import ApartmentId, PaymentSummary, Period


@dataclass(frozen=True, slots=True)
class GetPaymentSummaryUseCase:
    """Fetch the payment aggregation for one apartment and period.

    The endpoint requires `year` and `month` query parameters; the caller passes
    a `Period` so the month range is validated once, in `Period.__post_init__`,
    rather than being re-checked here.
    """

    rentals_client: RentalsApiClient

    async def execute(
        self,
        access_token: str,
        apartment_id: ApartmentId,
        period: Period,
    ) -> PaymentSummary:
        if not isinstance(apartment_id, ApartmentId):
            raise InvalidRentalsInputError("apartment_id must be an ApartmentId.")
        if not isinstance(period, Period):
            raise InvalidRentalsInputError("period must be a Period.")
        return await self.rentals_client.get_payment_summary(
            access_token, apartment_id, period
        )
