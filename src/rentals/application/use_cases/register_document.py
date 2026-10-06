"""Register the reference to a document already uploaded to Nextcloud."""

from dataclasses import dataclass

from src.rentals.application.ports import RentalsApiClient
from src.rentals.domain.entities import Document
from src.rentals.domain.exceptions import InvalidRentalsInputError
from src.rentals.domain.value_objects import (
    ApartmentId,
    DocumentType,
    validate_document_url,
)


@dataclass(frozen=True, slots=True)
class RegisterDocumentUseCase:
    """Record a Nextcloud file reference against an apartment.

    The BFF never sees file bytes. The frontend PUTs the file straight to the
    Nextcloud public WebDAV endpoint and passes the resulting absolute URL here,
    which is then registered in the DRF backend. That flow keeps large uploads
    off the Flask process entirely (see AGENTS.md, "Direct Uploads").

    `file_url` is validated as an absolute URL before the request is made: the
    backend stores it in a ``models.URLField(max_length=500)`` and re-checks it
    with ``serializers.URLField``, so a bare path such as ``lease.pdf`` would
    otherwise surface as an opaque 400 after a pointless round trip.
    """

    rentals_client: RentalsApiClient

    async def execute(
        self,
        access_token: str,
        *,
        apartment_id: ApartmentId,
        document_type: DocumentType,
        file_url: str,
        description: str | None,
    ) -> Document:
        if not isinstance(apartment_id, ApartmentId):
            raise InvalidRentalsInputError("apartment_id must be an ApartmentId.")
        if not isinstance(document_type, DocumentType):
            # A bare string is rejected here rather than coerced: the document
            # type arrives from a <select>, so a wrong value means the template
            # and the domain enum have drifted apart.
            raise InvalidRentalsInputError("document_type must be a DocumentType.")

        normalized_url = validate_document_url(file_url)
        normalized_description = (description or "").strip() or None

        return await self.rentals_client.register_document(
            access_token,
            apartment_id=apartment_id,
            document_type=document_type,
            file_url=normalized_url,
            description=normalized_description,
        )
