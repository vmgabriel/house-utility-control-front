"""Exceptions raised by the rentals domain.

Every failure the rentals rules can detect derives from
:class:`RentalsDomainError`, so views can catch one type and render a friendly
flash message (see AGENTS.md, "Error Handling in Views") while still letting
unexpected errors surface as bugs.

The hierarchy mirrors the backend's `apps.rentals.domain.exceptions` so a
translation layer can map DRF error codes onto these classes without a second
lookup table.
"""


class RentalsDomainError(Exception):
    """Base class for every rentals rule violation."""


class HouseNotFoundError(RentalsDomainError):
    """Raised when a house does not exist for the requesting owner."""


class ApartmentNotFoundError(RentalsDomainError):
    """Raised when an apartment does not exist within the request scope."""


class DocumentNotFoundError(RentalsDomainError):
    """Raised when a document does not exist within the request scope."""


class UtilityReadingNotFoundError(RentalsDomainError):
    """Raised when a utility reading does not exist within the request scope."""


class PaymentRecordNotFoundError(RentalsDomainError):
    """Raised when a payment record does not exist within the request scope."""


class InvalidReadingError(RentalsDomainError):
    """Raised when a meter reading breaks a measurement invariant."""


class InvalidPaymentError(RentalsDomainError):
    """Raised when a payment amount breaks a financial invariant."""


class InsufficientPaymentError(RentalsDomainError):
    """Raised when a payment cannot settle an outstanding balance."""


class InvalidAddressError(RentalsDomainError):
    """Raised when a property address is malformed or incomplete."""


class InvalidDocumentError(RentalsDomainError):
    """Raised when a document type or stored file URL is unacceptable."""


class InvalidRentalsInputError(RentalsDomainError):
    """Raised when user-supplied input fails validation before any I/O."""


class NextcloudUploadError(RentalsDomainError):
    """Raised when storing a document in Nextcloud did not succeed.

    Carries the upstream ``status`` when Nextcloud actually answered, so a view
    can tell an expired credential (401/403) apart from any other rejection
    without knowing how Nextcloud reports failure. ``status`` is ``None`` when
    the request never completed -- a DNS or TCP failure, or a timeout -- which
    is a different operator problem with a different fix.

    This exists for the local-development upload fallback only
    (``interfaces/web/upload_proxy.py``). The production path never uploads
    from Flask, so nothing else raises it.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
