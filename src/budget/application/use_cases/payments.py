"""Credit-card payment use case."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from src.budget.domain.entities import CreditCardPayment
from src.budget.domain.exceptions import InvalidPaymentAmountError
from src.budget.domain.ports import CreditCardPaymentPort


@dataclass
class PayCreditCardUseCase:
    """Settle a credit card from one of the caller's funding accounts.

    The view only moves form fields around; every rule about what a payable
    amount looks like lives here, so the BFF cannot drift from the backend's
    ``DecimalField(min_value=0.01, decimal_places=2)`` contract.
    """

    payment_repository: CreditCardPaymentPort

    async def execute(
        self,
        access_token: str,
        account_id: str,
        funding_account_id: str,
        amount: str,
    ) -> CreditCardPayment:
        """Validate the command and post the payment to the backend."""
        normalized = self._normalize_amount(amount)
        return await self.payment_repository.pay_credit_card(
            access_token, account_id, funding_account_id, normalized
        )

    @staticmethod
    def _normalize_amount(raw: str) -> str:
        """Turn a form value into the two-decimal string DRF expects."""
        try:
            amount = Decimal((raw or "").strip())
        except (InvalidOperation, ValueError):
            raise InvalidPaymentAmountError("Enter a valid payment amount.") from None

        if not amount.is_finite() or amount <= 0:
            raise InvalidPaymentAmountError("Payment amount must be greater than zero.")
        if amount.as_tuple().exponent < -2:
            raise InvalidPaymentAmountError(
                "Payment amount cannot have more than 2 decimal places."
            )
        return f"{amount:.2f}"
