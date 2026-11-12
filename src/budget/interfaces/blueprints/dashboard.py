"""Dashboard blueprint."""

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from src.budget.application.use_cases.accounts import GetFundingAccountsUseCase
from src.budget.application.use_cases.dashboard import GetDashboardOverviewUseCase
from src.budget.application.use_cases.payments import PayCreditCardUseCase
from src.budget.domain.entities import DashboardOverview
from src.budget.domain.exceptions import DomainException
from src.budget.domain.value_objects import AccountType
from src.budget.interfaces.viewmodels import (
    DashboardOverviewViewModel,
    FundingAccountViewModel,
)
from src.shared.auth.csrf import CSRFTokenManager
from src.shared.auth.jwt_cookie_manager import JWTCookieManager
from src.shared.http.drf_client import ServiceUnavailableError

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

EMPTY_OVERVIEW = DashboardOverview(today=None, this_week=None, this_month=None)


def _csrf_ok() -> bool:
    """Validate the CSRF token posted with a state-changing request."""
    csrf_manager: CSRFTokenManager = current_app.csrf_manager
    return csrf_manager.validate_token(request.form.get("csrf_token", ""))


def _dashboard_redirect(account_id: str | None):
    """Return to the dashboard, keeping the selected account when there is one."""
    if account_id:
        return redirect(url_for("dashboard.index", account_id=account_id))
    return redirect(url_for("dashboard.index"))


@dashboard_bp.route("/")
async def index():
    """Dashboard index page: summaries, analytics, and account management."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)

    if not access_token:
        return redirect(url_for("auth.login"))

    selected_account_id = (request.args.get("account_id") or "").strip()

    overview_use_case: GetDashboardOverviewUseCase = current_app.overview_use_case

    try:
        overview = await overview_use_case.execute(access_token)
        viewmodel = DashboardOverviewViewModel.from_domain(overview)
    except ServiceUnavailableError:
        # Backend unreachable, so re-raise for the 503 page. Falling through to
        # the empty dashboard below would show "$0.00" across all three cards,
        # which reads as "you spent nothing" rather than "we could not ask".
        raise
    except Exception:
        # Graceful degradation: render an empty dashboard rather than a 500.
        # Authentication failures are handled upstream by the auto-refreshing
        # repository, so anything landing here is a genuine backend problem --
        # a reachable backend that answered with an error. The user can act on
        # that, so an empty dashboard plus a blank slate is a fair report.
        viewmodel = DashboardOverviewViewModel.from_domain(EMPTY_OVERVIEW)

    # The "Manage Accounts" modal lists these server-side so the first paint is
    # complete and the modal needs no fetch of its own. Best-effort on purpose:
    # an accounts failure must not take the dashboard down with it. The modal
    # says so inline instead of showing an empty list that reads as "you have
    # no accounts".
    accounts_use_case: GetFundingAccountsUseCase = (
        current_app.get_funding_accounts_use_case
    )
    accounts_unavailable = False
    try:
        accounts = await accounts_use_case.execute(access_token)
    except Exception:
        accounts = []
        accounts_unavailable = True

    account_viewmodels = [
        FundingAccountViewModel.from_domain(account)
        for account in accounts
        if account.is_active
    ]

    # A card can only be settled from a debit or savings account, so the payment
    # modal offers exactly those. Cards themselves and cash stay out of the
    # funding dropdown.
    credit_card_accounts = [
        account
        for account in account_viewmodels
        if account.account_type == AccountType.CREDIT_CARD.value
    ]
    payment_accounts = [
        account
        for account in account_viewmodels
        if account.account_type in (AccountType.DEBIT.value, AccountType.SAVINGS.value)
    ]

    # Preselect the card the URL asked for; otherwise the first one, so the
    # modal opens on a usable choice even when "All accounts" is selected.
    default_credit_card_id = ""
    if selected_account_id:
        selected_card = next(
            (card for card in credit_card_accounts if card.id == selected_account_id),
            None,
        )
        if selected_card:
            default_credit_card_id = selected_card.id
    if not default_credit_card_id and credit_card_accounts:
        default_credit_card_id = credit_card_accounts[0].id

    return render_template(
        "dashboard/index.html",
        overview=viewmodel,
        accounts=account_viewmodels,
        account_types=AccountType,
        accounts_unavailable=accounts_unavailable,
        analytics_endpoint=url_for("budget.analytics_data"),
        selected_account_id=selected_account_id,
        credit_card_accounts=credit_card_accounts,
        payment_accounts=payment_accounts,
        default_credit_card_id=default_credit_card_id,
    )


@dashboard_bp.route("/pay-card", methods=["POST"])
async def pay_card():
    """Forward a credit-card payment form to the DRF backend.

    Deliberately thin: the amount rules live in ``PayCreditCardUseCase`` and
    the ledger movement lives in the backend. This view only validates the CSRF
    token, reads the form, and reports the outcome.
    """
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)
    if not access_token:
        return redirect(url_for("auth.login"))

    if not _csrf_ok():
        flash("Invalid or expired CSRF token.", "error")
        return _dashboard_redirect(request.form.get("account_id"))

    account_id = (request.form.get("account_id") or "").strip()
    funding_account_id = (request.form.get("funding_account_id") or "").strip()
    amount = request.form.get("amount", "")

    if not account_id or not funding_account_id:
        flash("Choose a credit card and a funding account.", "error")
        return _dashboard_redirect(account_id)

    use_case: PayCreditCardUseCase = current_app.pay_credit_card_use_case
    try:
        await use_case.execute(access_token, account_id, funding_account_id, amount)
    except ServiceUnavailableError:
        raise
    except DomainException as exc:
        flash(str(exc), "error")
        return _dashboard_redirect(account_id)
    except Exception:
        flash("Failed to pay credit card.", "error")
        return _dashboard_redirect(account_id)

    flash("Credit card payment completed!", "success")
    return _dashboard_redirect(account_id)
