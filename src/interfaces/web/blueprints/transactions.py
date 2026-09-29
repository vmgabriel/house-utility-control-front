"""Transactions blueprint."""

from datetime import date
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from src.application.use_cases.transactions import (
    CreateTransactionUseCase,
    DeleteTransactionUseCase,
    FetchTransactionsUseCase,
)
from src.domain.entities import DEFAULT_CATEGORY, Transaction
from src.domain.exceptions import DomainException
from src.domain.value_objects import Money, TransactionId, TransactionType, UserId
from src.infrastructure.api.drf_client import ServiceUnavailableError
from src.infrastructure.auth.csrf import CSRFTokenManager
from src.infrastructure.auth.jwt_cookie_manager import JWTCookieManager
from src.interfaces.web.viewmodels import TransactionViewModel

transactions_bp = Blueprint("transactions", __name__, url_prefix="/transactions")

PAGE_SIZE = 20

#: Offered as datalist suggestions in the create form. The API accepts any
#: non-blank string up to 100 characters, so this is a convenience, not a limit.
CATEGORY_SUGGESTIONS = ("General", "Food", "Transport", "Salary", "Housing", "Other")


def _csrf_ok() -> bool:
    """Validate the CSRF token posted with a state-changing request."""
    csrf_manager: CSRFTokenManager = current_app.csrf_manager
    return csrf_manager.validate_token(request.form.get("csrf_token", ""))


def _requested_page() -> int:
    """Read the ``page`` query argument, falling back to 1 when unusable."""
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        return 1
    return page if page >= 1 else 1


@transactions_bp.route("/")
async def index():
    """List transactions."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)

    if not access_token:
        return redirect(url_for("auth.login"))

    fetch_use_case: FetchTransactionsUseCase = current_app.fetch_transactions_use_case
    page = _requested_page()

    try:
        transactions = await fetch_use_case.execute(
            access_token, page=page, page_size=PAGE_SIZE
        )
    except ServiceUnavailableError:
        # Backend unreachable: re-raise for the 503 page. An empty list would
        # render as "No transactions yet", which is a lie -- the user has
        # transactions, we just cannot see them right now.
        raise
    except DomainException:
        transactions = []
        flash("Failed to load transactions.", "error")
    except Exception:
        transactions = []
        flash("Transactions could not be loaded right now.", "error")

    viewmodels = [TransactionViewModel.from_domain(tx) for tx in transactions]
    return render_template(
        "transactions/index.html",
        transactions=viewmodels,
        page=page,
        category_suggestions=CATEGORY_SUGGESTIONS,
    )


@transactions_bp.route("/create", methods=["POST"])
async def create():
    """Create a new transaction."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)

    if not access_token:
        return redirect(url_for("auth.login"))

    if not _csrf_ok():
        flash("Invalid or expired CSRF token.", "error")
        return redirect(url_for("transactions.index"))

    tx_type = request.form.get("type", "expense")
    amount_str = request.form.get("amount", "0.00")
    description = request.form.get("description", "").strip()
    # The backend requires a non-blank category, so fall back to the domain
    # default rather than letting the API reject the request with a 400.
    category = request.form.get("category", "").strip() or DEFAULT_CATEGORY
    date_str = request.form.get("date") or date.today().isoformat()

    try:
        transaction = Transaction(
            id=None,
            # The backend derives ownership from the access token.
            user_id=UserId(""),
            type=TransactionType(tx_type),
            amount=Money(Decimal(amount_str)),
            description=description,
            date=date.fromisoformat(date_str),
            category=category,
        )
    except (InvalidOperation, ValueError) as exc:
        # Invalid Decimal, unknown transaction type, malformed date, or a
        # negative amount rejected by the Money value object.
        flash(f"Invalid transaction data: {exc}", "error")
        return redirect(url_for("transactions.index"))

    create_use_case: CreateTransactionUseCase = current_app.create_transaction_use_case
    try:
        await create_use_case.execute(access_token, transaction)
    except ServiceUnavailableError:
        # The write may or may not have landed upstream. Re-raise for the 503
        # page: a flash on the list page would imply the submission finished.
        raise
    except DomainException as exc:
        # Domain invariants (amount must be positive, no future dates) and DRF
        # validation errors surface their own message, which is safe to show.
        # This must return: falling through would also announce success, so a
        # rejected transaction would be reported as created.
        flash(str(exc), "error")
        return redirect(url_for("transactions.index"))
    except Exception:
        flash("Failed to create transaction.", "error")
        return redirect(url_for("transactions.index"))

    flash("Transaction created successfully!", "success")
    return redirect(url_for("transactions.index"))


@transactions_bp.route("/<transaction_id>/delete", methods=["POST"])
async def delete(transaction_id: str):
    """Delete a transaction."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)

    if not access_token:
        return redirect(url_for("auth.login"))

    if not _csrf_ok():
        flash("Invalid or expired CSRF token.", "error")
        return redirect(url_for("transactions.index"))

    delete_use_case: DeleteTransactionUseCase = current_app.delete_transaction_use_case
    try:
        await delete_use_case.execute(access_token, TransactionId(transaction_id))
    except ServiceUnavailableError:
        # The delete may or may not have landed upstream, so "Transaction
        # deleted successfully!" would be a guess. Re-raise for the 503 page.
        raise
    except Exception:
        flash("Failed to delete transaction.", "error")
    else:
        flash("Transaction deleted successfully!", "success")

    return redirect(url_for("transactions.index"))
