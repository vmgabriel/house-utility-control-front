"""Record a rent payment for an apartment."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.rentals.application.ports import RentalsApiClient
from src.rentals.domain.entities import PaymentRecord
from src.rentals.domain.exceptions import InvalidRentalsInputError
from src.rentals.domain.value_objects import ApartmentId, PaymentAmount
from src.shared.domain.ports.clock import Clock


@dataclass(frozen=True, slots=True)
class RecordPaymentUseCase:
    """Validate a payment, then record it through the port.

    The status is never sent. ``RecordPaymentSerializer`` accepts only
    ``payment_date``, ``amount``, and ``notes``; the backend derives ``PAID`` vs
    ``PARTIAL`` server-side by comparing the amount against the apartment's
    monthly rent, which the client does not have. Sending a status would be
    rejected as an unknown field.

    `payment_date` may not be in the future, matching the rule the transactions
    context applies to `date`. That check needs "today", so it goes through the
    injected `Clock` and stays testable.
    """

    rentals_client: RentalsApiClient
    clock: Clock

    async def execute(
        self,
        access_token: str,
        *,
        apartment_id: ApartmentId,
        payment_date: date,
        amount: Decimal,
        notes: str | None,
    ) -> PaymentRecord:
        if not isinstance(apartment_id, ApartmentId):
            raise InvalidRentalsInputError("apartment_id must be an ApartmentId.")
        if type(payment_date) is not date:
            raise InvalidRentalsInputError("payment_date must be a date.")
        if payment_date > self.clock.today():
            raise InvalidRentalsInputError("payment_date cannot be in the future.")

        # Rejects zero, negatives, and more than two decimal places.
        validated_amount = PaymentAmount(amount)
        normalized_notes = notes.strip() or None if notes else None
        if normalized_notes is not None and len(normalized_notes) > 2000:
            raise InvalidRentalsInputError("Notes cannot exceed 2000 characters.")

        return await self.rentals_client.record_payment(
            access_token,
            apartment_id=apartment_id,
            payment_date=payment_date,
            amount=validated_amount.amount,
            notes=normalized_notes,
        )
