"""List the houses owned by the current user."""

from dataclasses import dataclass

from src.rentals.application.ports import RentalsApiClient
from src.rentals.domain.entities import House


@dataclass(frozen=True, slots=True)
class GetHousesUseCase:
    """Fetch every house owned by the authenticated user.

    A thin orchestrator: the port owns the HTTP call and the mapping, and the
    backend derives the owner from the JWT, so there is no identifier to
    validate here.
    """

    rentals_client: RentalsApiClient

    async def execute(self, access_token: str) -> list[House]:
        return await self.rentals_client.get_houses(access_token)
