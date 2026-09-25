"""Transaction repository implementation."""

from src.domain.entities import Transaction
from src.domain.ports import TransactionRepositoryPort
from src.domain.value_objects import TransactionId
from src.infrastructure.api.drf_client import DRFAPIClient, NotFoundError
from src.infrastructure.api.mappers import (
    map_transaction_response,
    transaction_to_drf_payload,
)
from src.infrastructure.api.schemas import (
    DRFPaginatedTransactionsResponse,
    DRFTransactionResponse,
)


class DRFTransactionRepository:
    """DRF-backed implementation of TransactionRepositoryPort."""

    def __init__(self, api_client: DRFAPIClient):
        self.api_client = api_client

    async def list_transactions(
        self, access_token: str, page: int = 1, page_size: int = 20
    ) -> list[Transaction]:
        data = await self.api_client.list_transactions(access_token, page, page_size)
        paginated = DRFPaginatedTransactionsResponse.model_validate(data)
        return [map_transaction_response(tx) for tx in paginated.results]

    async def get_transaction(
        self, access_token: str, transaction_id: TransactionId
    ) -> Transaction | None:
        try:
            data = await self.api_client.get_transaction(
                access_token, str(transaction_id)
            )
        except NotFoundError:
            # The port models "absent" as None; auth and transport failures must
            # still surface instead of masquerading as a missing transaction.
            return None
        tx_data = DRFTransactionResponse.model_validate(data)
        return map_transaction_response(tx_data)

    async def create_transaction(
        self, access_token: str, transaction: Transaction
    ) -> Transaction:
        payload = transaction_to_drf_payload(transaction)
        data = await self.api_client.create_transaction(access_token, payload)
        tx_data = DRFTransactionResponse.model_validate(data)
        return map_transaction_response(tx_data)

    async def update_transaction(
        self, access_token: str, transaction: Transaction
    ) -> Transaction:
        payload = transaction_to_drf_payload(transaction)
        data = await self.api_client.update_transaction(
            access_token, str(transaction.id), payload
        )
        tx_data = DRFTransactionResponse.model_validate(data)
        return map_transaction_response(tx_data)

    async def delete_transaction(
        self, access_token: str, transaction_id: TransactionId
    ) -> None:
        await self.api_client.delete_transaction(access_token, str(transaction_id))


# Structural check: the adapter must satisfy the port it claims to implement.
_: type[TransactionRepositoryPort] = DRFTransactionRepository
