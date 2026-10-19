"""Integration tests for the funding-account repository."""

import json

import httpx
import pytest
import respx

from src.budget.domain.entities import FundingAccount
from src.budget.domain.value_objects import AccountType
from src.budget.infrastructure.repositories.funding_account_repository import (
    DRFFundingAccountRepository,
)
from src.shared.http.drf_client import DRFAPIClient, DRFAPIClientError

ACCOUNTS_URL = "http://testserver/api/v1/transactions/accounts/"


@pytest.fixture
def account_repository():
    api_client = DRFAPIClient(base_url="http://testserver/api/v1")
    return DRFFundingAccountRepository(api_client)


class TestListAccounts:
    @pytest.mark.asyncio
    @respx.mock
    async def test_maps_the_list_envelope_to_domain_entities(self, account_repository):
        respx.get(ACCOUNTS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "count": 2,
                    "results": [
                        {
                            "id": "acc-1",
                            "name": "Visa Rewards",
                            "account_type": "credit_card",
                            "is_active": True,
                        },
                        {
                            "id": "acc-2",
                            "name": "Wallet",
                            "account_type": "cash",
                            "is_active": True,
                        },
                    ],
                },
            )
        )

        accounts = await account_repository.list_accounts("access-token")

        assert len(accounts) == 2
        assert all(isinstance(account, FundingAccount) for account in accounts)
        assert [account.name for account in accounts] == ["Visa Rewards", "Wallet"]
        assert accounts[0].account_type is AccountType.CREDIT_CARD
        assert accounts[1].account_type is AccountType.CASH

    @pytest.mark.asyncio
    @respx.mock
    async def test_an_empty_envelope_yields_an_empty_list(self, account_repository):
        respx.get(ACCOUNTS_URL).mock(
            return_value=httpx.Response(200, json={"count": 0, "results": []})
        )

        assert await account_repository.list_accounts("access-token") == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_the_bearer_token(self, account_repository):
        route = respx.get(ACCOUNTS_URL).mock(
            return_value=httpx.Response(200, json={"count": 0, "results": []})
        )

        await account_repository.list_accounts("access-token")

        assert route.calls[0].request.headers["Authorization"] == "Bearer access-token"


class TestCreateAccount:
    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_the_create_payload_and_maps_the_result(
        self, account_repository
    ):
        route = respx.post(ACCOUNTS_URL).mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "acc-9",
                    "name": "Amex",
                    "account_type": "debit",
                    "is_active": True,
                },
            )
        )

        created = await account_repository.create_account(
            "access-token",
            FundingAccount(id=None, name="Amex", account_type=AccountType.DEBIT),
        )

        body = json.loads(route.calls[0].request.content)
        # Matches CreateAccountSerializer: the id and is_active are server-owned.
        assert body == {"name": "Amex", "account_type": "debit"}
        assert created.id == "acc-9"
        assert created.account_type is AccountType.DEBIT
        assert created.is_active is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_rejected_create_raises(self, account_repository):
        respx.post(ACCOUNTS_URL).mock(
            return_value=httpx.Response(
                400, json={"name": ["This field may not be blank."]}
            )
        )

        with pytest.raises(DRFAPIClientError):
            await account_repository.create_account(
                "access-token",
                FundingAccount(id=None, name="Amex", account_type=AccountType.DEBIT),
            )
