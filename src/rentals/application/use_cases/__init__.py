"""One file per rentals use case, so each orchestration reads on its own."""

from src.rentals.application.use_cases.create_apartment import CreateApartmentUseCase
from src.rentals.application.use_cases.create_house import CreateHouseUseCase
from src.rentals.application.use_cases.get_apartments import GetApartmentsUseCase
from src.rentals.application.use_cases.get_houses import GetHousesUseCase
from src.rentals.application.use_cases.get_payment_summary import (
    GetPaymentSummaryUseCase,
)
from src.rentals.application.use_cases.read_collections import (
    GetApartmentUseCase,
    GetDocumentsUseCase,
    GetPaymentRecordsUseCase,
    GetUtilityReadingsUseCase,
)
from src.rentals.application.use_cases.record_payment import RecordPaymentUseCase
from src.rentals.application.use_cases.record_utility_reading import (
    RecordUtilityReadingUseCase,
)
from src.rentals.application.use_cases.register_document import RegisterDocumentUseCase

__all__ = [
    "CreateApartmentUseCase",
    "CreateHouseUseCase",
    "GetApartmentUseCase",
    "GetApartmentsUseCase",
    "GetDocumentsUseCase",
    "GetHousesUseCase",
    "GetPaymentRecordsUseCase",
    "GetPaymentSummaryUseCase",
    "GetUtilityReadingsUseCase",
    "RecordPaymentUseCase",
    "RecordUtilityReadingUseCase",
    "RegisterDocumentUseCase",
]
