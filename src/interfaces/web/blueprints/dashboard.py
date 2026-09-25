"""Dashboard blueprint."""

from flask import Blueprint, current_app, redirect, render_template, request, url_for

from src.application.use_cases.dashboard import GetDashboardOverviewUseCase
from src.domain.entities import DashboardOverview
from src.infrastructure.auth.jwt_cookie_manager import JWTCookieManager
from src.interfaces.web.viewmodels import DashboardOverviewViewModel

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

EMPTY_OVERVIEW = DashboardOverview(today=None, this_week=None, this_month=None)


@dashboard_bp.route("/")
async def index():
    """Dashboard index page."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)

    if not access_token:
        return redirect(url_for("auth.login"))

    overview_use_case: GetDashboardOverviewUseCase = current_app.overview_use_case

    try:
        overview = await overview_use_case.execute(access_token)
        viewmodel = DashboardOverviewViewModel.from_domain(overview)
    except Exception:
        # Graceful degradation: render an empty dashboard rather than a 500.
        # Authentication failures are handled upstream by the auto-refreshing
        # repository, so anything landing here is a genuine backend problem.
        viewmodel = DashboardOverviewViewModel.from_domain(EMPTY_OVERVIEW)

    return render_template("dashboard/index.html", overview=viewmodel)
