"""Dashboard blueprint."""

from flask import Blueprint, current_app, redirect, render_template, request, url_for

from src.budget.application.use_cases.accounts import GetFundingAccountsUseCase
from src.budget.application.use_cases.dashboard import GetDashboardOverviewUseCase
from src.budget.domain.entities import DashboardOverview
from src.budget.domain.value_objects import AccountType
from src.budget.interfaces.viewmodels import (
    DashboardOverviewViewModel,
    FundingAccountViewModel,
)
from src.shared.auth.jwt_cookie_manager import JWTCookieManager
from src.shared.http.drf_client import ServiceUnavailableError

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

EMPTY_OVERVIEW = DashboardOverview(today=None, this_week=None, this_month=None)


@dashboard_bp.route("/")
async def index():
    """Dashboard index page: summaries, analytics, and account management."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)

    if not access_token:
        return redirect(url_for("auth.login"))

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

    return render_template(
        "dashboard/index.html",
        overview=viewmodel,
        accounts=account_viewmodels,
        account_types=AccountType,
        accounts_unavailable=accounts_unavailable,
        analytics_endpoint=url_for("budget.analytics_data"),
    )
