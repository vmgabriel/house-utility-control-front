"""Analytics data and funding-account creation for the budget context.

Both capabilities live on the dashboard now. There is no standalone analytics
page and no standalone accounts page: the charts hydrate from ``analytics_data``
and the dashboard's "Manage Accounts" modal posts to ``create_account``.
"""

from datetime import date, timedelta

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    request,
    url_for,
)

from src.budget.application.use_cases.accounts import CreateFundingAccountUseCase
from src.budget.application.use_cases.analytics import GetTransactionAnalyticsUseCase
from src.budget.domain.entities import FundingAccount
from src.budget.domain.exceptions import DomainException
from src.budget.domain.value_objects import (
    AccountType,
    AnalyticsGroupBy,
    AnalyticsPeriod,
)
from src.budget.interfaces.viewmodels import AnalyticsViewModel
from src.shared.auth.csrf import CSRFTokenManager
from src.shared.auth.jwt_cookie_manager import JWTCookieManager
from src.shared.http.drf_client import ServiceUnavailableError

budget_bp = Blueprint("budget", __name__, url_prefix="/budget")

#: Default look-back per period, in days, and the reasons are different for
#: each. A daily chart of a year is 365 unreadable bars, so it gets a month; a
#: weekly chart gets a quarter; a monthly chart gets a full year. All three are
#: comfortably inside the backend's 366-day ceiling.
_DAILY_DAYS = 30
_WEEKLY_DAYS = 84
_MONTHLY_MONTHS = 12

#: The backend rejects a range whose endpoints are further apart than this
#: (``MAX_RANGE_DAYS`` in the transactions application layer).
_MAX_RANGE_DAYS = 366


def _csrf_ok() -> bool:
    """Validate the CSRF token posted with a state-changing request."""
    csrf_manager: CSRFTokenManager = current_app.csrf_manager
    return csrf_manager.validate_token(request.form.get("csrf_token", ""))


def _parse_period(raw: str | None) -> AnalyticsPeriod:
    try:
        return AnalyticsPeriod(raw or "")
    except ValueError:
        return AnalyticsPeriod.MONTHLY


def _parse_group_by(raw: str | None) -> AnalyticsGroupBy:
    try:
        return AnalyticsGroupBy(raw or "")
    except ValueError:
        return AnalyticsGroupBy.TYPE


def _default_range(period: AnalyticsPeriod) -> tuple[date, date]:
    """The default (start, end) window for a period, ending today."""
    today = date.today()
    if period is AnalyticsPeriod.MONTHLY:
        # Twelve calendar months *including* the current one. Counting the
        # current month as well as twelve full months back produced thirteen
        # buckets and, from the 3rd of any month, a window longer than the
        # backend's 366-day ceiling -- a 400 that the dashboard reported as
        # "Could not load analytics" on almost every day of the month.
        month_index = (today.year * 12 + today.month - 1) - (_MONTHLY_MONTHS - 1)
        year, zero_based_month = divmod(month_index, 12)
        return date(year, zero_based_month + 1, 1), today
    days = _DAILY_DAYS if period is AnalyticsPeriod.DAILY else _WEEKLY_DAYS
    return today - timedelta(days=days - 1), today


def _requested_range(period: AnalyticsPeriod) -> tuple[date, date]:
    """Resolve the window from the query string, falling back per period.

    An unparseable or inverted pair falls back to the period's default rather
    than erroring, so a hand-edited URL still renders a chart. A range longer
    than the backend allows is clamped for the same reason: a stale bookmark
    should show a chart, not an error banner.
    """
    default_start, default_end = _default_range(period)
    start = default_start
    end = default_end
    start_raw = request.args.get("start_date")
    end_raw = request.args.get("end_date")
    if start_raw:
        try:
            start = date.fromisoformat(start_raw)
        except ValueError:
            start = default_start
    if end_raw:
        try:
            end = date.fromisoformat(end_raw)
        except ValueError:
            end = default_end
    if end < start:
        return default_start, default_end
    if (end - start).days > _MAX_RANGE_DAYS:
        return default_start, default_end
    return start, end


@budget_bp.route("/analytics/data/")
async def analytics_data():
    """Return one chart-ready analytics payload as JSON."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)
    if not access_token:
        # The browser's fetch cannot follow a redirect to an HTML login page
        # usefully, so answer with the status the script already understands.
        return jsonify({"detail": "Authentication required."}), 401

    period = _parse_period(request.args.get("period"))
    group_by = _parse_group_by(request.args.get("group_by"))
    start_date, end_date = _requested_range(period)

    use_case: GetTransactionAnalyticsUseCase = (
        current_app.get_transaction_analytics_use_case
    )
    try:
        analytics_result = await use_case.execute(
            access_token, start_date, end_date, group_by, period
        )
    except ServiceUnavailableError:
        raise
    except DomainException:
        return jsonify({"detail": "Analytics could not be loaded."}), 502
    except Exception:
        return jsonify({"detail": "Analytics could not be loaded."}), 500

    return jsonify(AnalyticsViewModel.from_domain(analytics_result).to_dict())


@budget_bp.route("/accounts/create", methods=["POST"])
async def create_account():
    """Create a funding account from the dashboard's modal form."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)
    if not access_token:
        return redirect(url_for("auth.login"))

    if not _csrf_ok():
        flash("Invalid or expired CSRF token.", "error")
        return redirect(url_for("dashboard.index"))

    name = request.form.get("name", "").strip()
    account_type_raw = request.form.get("account_type", "")
    try:
        account_type = AccountType(account_type_raw)
    except ValueError:
        flash("Choose a valid account type.", "error")
        return redirect(url_for("dashboard.index"))

    account = FundingAccount(id=None, name=name, account_type=account_type)
    use_case: CreateFundingAccountUseCase = current_app.create_funding_account_use_case
    try:
        await use_case.execute(access_token, account)
    except ServiceUnavailableError:
        raise
    except DomainException as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard.index"))
    except Exception:
        flash("Failed to create funding account.", "error")
        return redirect(url_for("dashboard.index"))

    flash("Funding account created successfully!", "success")
    return redirect(url_for("dashboard.index"))
