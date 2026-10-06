"""Composition root for the rentals context.

The single place allowed to construct concrete adapters and inject them into
use cases (AGENTS.md, constraint 3). Views never build a use case; they read the
ready-made ones off `current_app`.

The auto-refresh wrapping lives in `src/interfaces/web/app.py` alongside the
equivalent wrapping for every other context, because `refreshing()` there closes
over the request-scoped cookie manager. This module's job is only to construct
the adapter, the clock, and the use cases that depend on them -- and to make it
impossible to wire a use case to a raw, unrefreshed client.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.rentals.application.use_cases import (
    CreateApartmentUseCase,
    CreateHouseUseCase,
    GetApartmentsUseCase,
    GetApartmentUseCase,
    GetDocumentsUseCase,
    GetHousesUseCase,
    GetPaymentRecordsUseCase,
    GetPaymentSummaryUseCase,
    GetUtilityReadingsUseCase,
    RecordPaymentUseCase,
    RecordUtilityReadingUseCase,
    RegisterDocumentUseCase,
)
from src.rentals.infrastructure.drf_client import DrfRentalsClient
from src.shared.domain.ports.clock import Clock
from src.shared.infrastructure.clock import SystemClock


@dataclass(frozen=True, slots=True)
class RentalsUseCases:
    """Every rentals use case, wired to one shared adapter and clock."""

    get_houses: GetHousesUseCase
    create_house: CreateHouseUseCase
    get_apartment: GetApartmentUseCase
    get_apartments: GetApartmentsUseCase
    create_apartment: CreateApartmentUseCase
    get_documents: GetDocumentsUseCase
    get_utility_readings: GetUtilityReadingsUseCase
    get_payment_summary: GetPaymentSummaryUseCase
    get_payment_records: GetPaymentRecordsUseCase
    record_utility_reading: RecordUtilityReadingUseCase
    record_payment: RecordPaymentUseCase
    register_document: RegisterDocumentUseCase

    #: Attribute name on the Flask app -> attribute name here. Views read
    #: `current_app.get_houses_use_case`, matching the existing contexts.
    APP_ATTRIBUTES = (
        ("get_houses_use_case", "get_houses"),
        ("create_house_use_case", "create_house"),
        ("get_apartment_use_case", "get_apartment"),
        ("get_apartments_use_case", "get_apartments"),
        ("create_apartment_use_case", "create_apartment"),
        ("get_documents_use_case", "get_documents"),
        ("get_utility_readings_use_case", "get_utility_readings"),
        ("get_payment_summary_use_case", "get_payment_summary"),
        ("get_payment_records_use_case", "get_payment_records"),
        ("record_utility_reading_use_case", "record_utility_reading"),
        ("record_payment_use_case", "record_payment"),
        ("register_document_use_case", "register_document"),
    )

    def attach_to(self, target: object) -> None:
        """Expose each use case as an attribute on `target` (the Flask app)."""
        for app_name, field_name in self.APP_ATTRIBUTES:
            setattr(target, app_name, getattr(self, field_name))


def build_rentals_use_cases(
    rentals_client: DrfRentalsClient,
    clock: Clock | None = None,
) -> RentalsUseCases:
    """Build the rentals use cases around an adapter and a clock.

    `clock` defaults to `SystemClock` so the composition root can stay terse,
    while tests can pass a frozen clock without patching anything.
    """
    resolved_clock: Clock = clock if clock is not None else SystemClock()
    return RentalsUseCases(
        get_houses=GetHousesUseCase(rentals_client=rentals_client),
        create_house=CreateHouseUseCase(rentals_client=rentals_client),
        get_apartment=GetApartmentUseCase(rentals_client=rentals_client),
        get_apartments=GetApartmentsUseCase(rentals_client=rentals_client),
        create_apartment=CreateApartmentUseCase(rentals_client=rentals_client),
        get_documents=GetDocumentsUseCase(rentals_client=rentals_client),
        get_utility_readings=GetUtilityReadingsUseCase(rentals_client=rentals_client),
        get_payment_summary=GetPaymentSummaryUseCase(rentals_client=rentals_client),
        get_payment_records=GetPaymentRecordsUseCase(rentals_client=rentals_client),
        record_utility_reading=RecordUtilityReadingUseCase(
            rentals_client=rentals_client, clock=resolved_clock
        ),
        record_payment=RecordPaymentUseCase(
            rentals_client=rentals_client, clock=resolved_clock
        ),
        register_document=RegisterDocumentUseCase(rentals_client=rentals_client),
    )


__all__ = [
    "RentalsUseCases",
    "build_rentals_use_cases",
]
