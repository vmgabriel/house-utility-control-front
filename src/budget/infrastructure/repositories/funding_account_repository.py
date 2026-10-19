"""Funding-account repository implementation."""

from src.budget.domain.entities import FundingAccount
from src.budget.domain.ports import FundingAccountRepositoryPort
from src.budget.infrastructure.mappers import (
    funding_account_to_drf_payload,
    map_funding_account_response,
)
from src.budget.infrastructure.schemas import (
    DRFFundingAccountListResponse,
    DRFFundingAccountResponse,
)
from src.shared.http.drf_client import DRFAPIClient


class DRFFundingAccountRepository:
    """DRF-backed implementation of FundingAccountRepositoryPort."""

    def __init__(self, api_client: DRFAPIClient):
        self.api_client = api_client

    async def list_accounts(self, access_token: str) -> list[FundingAccount]:
        data = await self.api_client.list_funding_accounts(access_token)
        parsed = DRFFundingAccountListResponse.model_validate(data)
        return [map_funding_account_response(account) for account in parsed.results]

    async def create_account(
        self, access_token: str, account: FundingAccount
    ) -> FundingAccount:
        payload = funding_account_to_drf_payload(account)
        data = await self.api_client.create_funding_account(access_token, payload)
        parsed = DRFFundingAccountResponse.model_validate(data)
        return map_funding_account_response(parsed)


# Structural check: the adapter must satisfy the port it claims to implement.
_: type[FundingAccountRepositoryPort] = DRFFundingAccountRepository
