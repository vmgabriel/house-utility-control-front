"""Credit-card payment repository implementation."""

from src.budget.domain.entities import CreditCardPayment
from src.budget.domain.ports import CreditCardPaymentPort
from src.budget.infrastructure.mappers import (
    credit_card_payment_to_drf_payload,
    map_credit_card_payment_response,
)
from src.budget.infrastructure.schemas import DRFCreditCardPaymentResponse
from src.shared.http.drf_client import DRFAPIClient


class DRFCreditCardPaymentRepository:
    """DRF-backed implementation of CreditCardPaymentPort."""

    def __init__(self, api_client: DRFAPIClient):
        self.api_client = api_client

    async def pay_credit_card(
        self,
        access_token: str,
        account_id: str,
        funding_account_id: str,
        amount: str,
    ) -> CreditCardPayment:
        payload = credit_card_payment_to_drf_payload(funding_account_id, amount)
        data = await self.api_client.pay_credit_card(access_token, account_id, payload)
        parsed = DRFCreditCardPaymentResponse.model_validate(data)
        return map_credit_card_payment_response(parsed)


# Structural check: the adapter must satisfy the port it claims to implement.
_: type[CreditCardPaymentPort] = DRFCreditCardPaymentRepository
