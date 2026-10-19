"""Funding-account use cases."""

from dataclasses import dataclass

from src.budget.domain.entities import FundingAccount
from src.budget.domain.exceptions import InvalidAccountNameError
from src.budget.domain.ports import FundingAccountRepositoryPort


@dataclass
class GetFundingAccountsUseCase:
    """Use case for listing the caller's active funding accounts."""

    funding_account_repository: FundingAccountRepositoryPort

    async def execute(self, access_token: str) -> list[FundingAccount]:
        """List the caller's active funding accounts."""
        return await self.funding_account_repository.list_accounts(access_token)


@dataclass
class CreateFundingAccountUseCase:
    """Use case for creating a funding account."""

    funding_account_repository: FundingAccountRepositoryPort

    async def execute(
        self, access_token: str, account: FundingAccount
    ) -> FundingAccount:
        """Create a funding account after validating its name.

        A blank name is rejected here rather than sent to DRF and bounced back:
        the message is immediate, and the form does not depend on the backend's
        field-error format.
        """
        if not account.name.strip():
            raise InvalidAccountNameError("Account name cannot be blank")
        return await self.funding_account_repository.create_account(
            access_token, account
        )
