"""Flask application factory.

This is the composition root: it is the single place where the outer layers
(Flask, httpx, DRF) are wired to the inner layers (use cases, domain). Nothing
below this module knows that Flask or httpx exist.
"""

import os
from datetime import date

import httpx
from dotenv import load_dotenv
from flask import Flask, Response, g, redirect, render_template, request, url_for
from werkzeug.exceptions import ServiceUnavailable

from src.application.use_cases.auth import (
    AuthenticateUserUseCase,
    LogoutUserUseCase,
    RefreshTokenUseCase,
)
from src.application.use_cases.dashboard import GetDashboardOverviewUseCase
from src.application.use_cases.transactions import (
    CreateTransactionUseCase,
    DeleteTransactionUseCase,
    FetchTransactionsUseCase,
    GetTransactionUseCase,
    UpdateTransactionUseCase,
)
from src.infrastructure.api.drf_client import DRFAPIClient, ServiceUnavailableError
from src.infrastructure.auth.csrf import CSRFTokenManager
from src.infrastructure.auth.jwt_cookie_manager import CookieConfig, JWTCookieManager
from src.infrastructure.repositories.auth_repository import DRFAuthRepository
from src.infrastructure.repositories.auto_refresh import AutoRefreshingRepository
from src.infrastructure.repositories.dashboard_repository import DRFDashboardRepository
from src.infrastructure.repositories.transaction_repository import (
    DRFTransactionRepository,
)
from src.interfaces.web.blueprints.auth import auth_bp
from src.interfaces.web.blueprints.dashboard import dashboard_bp
from src.interfaces.web.blueprints.transactions import transactions_bp
from src.interfaces.web.security import init_security
from src.profile.application.use_cases import (
    GetProfileUseCase,
    UpdatePreferencesUseCase,
    UpdateProfileUseCase,
)
from src.profile.infrastructure.repository import DRFProfileRepository
from src.profile.interfaces.blueprint import profile_bp
from src.users.application.use_cases import (
    ListUsersUseCase,
    RegisterUserUseCase,
    ToggleUserActiveUseCase,
    UpdateUserPlanUseCase,
)
from src.users.infrastructure.repository import DRFUserRepository
from src.users.interfaces.blueprint import users_bp

load_dotenv()

DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1"
DEFAULT_SECRET_KEY = "dev-secret-key"
TRUTHY_VALUES = ("true", "1", "yes", "on")


def create_app() -> Flask:
    app = Flask(__name__)

    # Load configuration
    app.config.from_prefixed_env()
    app.config["DRF_API_BASE_URL"] = os.getenv("DRF_API_BASE_URL", DEFAULT_API_BASE_URL)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", DEFAULT_SECRET_KEY)

    debug = os.getenv("FLASK_DEBUG", "false").lower() in TRUTHY_VALUES
    app.debug = debug

    # Initialize infrastructure
    api_client = DRFAPIClient(base_url=app.config["DRF_API_BASE_URL"])
    auth_repo = DRFAuthRepository(api_client)
    transaction_repo = DRFTransactionRepository(api_client)
    dashboard_repo = DRFDashboardRepository(api_client)
    profile_repo = DRFProfileRepository(api_client)
    user_repo = DRFUserRepository(api_client)

    # Initialize security managers
    cookie_config = CookieConfig(
        # Secure cookies require HTTPS, which local development lacks.
        secure=not debug,
        samesite="Lax",
        httponly=True,
    )
    app.cookie_manager = JWTCookieManager(cookie_config)
    app.csrf_manager = CSRFTokenManager(app.config["SECRET_KEY"])

    # Initialize use cases. Protected repositories are wrapped so a 401 from DRF
    # triggers one token refresh and a single retry. The refreshed pair is
    # staged on `g` and written to the response cookies in `after_request`,
    # because the response object does not exist yet while a view is running.
    def refreshing(inner):
        async def refresh() -> tuple[str, str]:
            refresh_token = app.cookie_manager.get_refresh_token(request)
            return await auth_repo.refresh_token(refresh_token or "")

        def on_refreshed(access_token: str, refresh_token: str) -> None:
            g.refreshed_tokens = (access_token, refresh_token)

        return AutoRefreshingRepository(
            inner=inner, refresh=refresh, on_refreshed=on_refreshed
        )

    safe_transaction_repo = refreshing(transaction_repo)
    safe_dashboard_repo = refreshing(dashboard_repo)
    safe_profile_repo = refreshing(profile_repo)
    safe_user_repo = refreshing(user_repo)

    app.auth_use_case = AuthenticateUserUseCase(auth_repository=auth_repo)
    app.logout_use_case = LogoutUserUseCase(auth_repository=auth_repo)
    app.refresh_token_use_case = RefreshTokenUseCase(auth_repository=auth_repo)
    app.fetch_transactions_use_case = FetchTransactionsUseCase(
        transaction_repository=safe_transaction_repo
    )
    app.get_transaction_use_case = GetTransactionUseCase(
        transaction_repository=safe_transaction_repo
    )
    app.create_transaction_use_case = CreateTransactionUseCase(
        transaction_repository=safe_transaction_repo,
        current_date=date.today(),
    )
    app.update_transaction_use_case = UpdateTransactionUseCase(
        transaction_repository=safe_transaction_repo,
        current_date=date.today(),
    )
    app.delete_transaction_use_case = DeleteTransactionUseCase(
        transaction_repository=safe_transaction_repo
    )
    app.overview_use_case = GetDashboardOverviewUseCase(
        dashboard_repository=safe_dashboard_repo
    )
    app.get_profile_use_case = GetProfileUseCase(repository=safe_profile_repo)
    app.update_profile_use_case = UpdateProfileUseCase(repository=safe_profile_repo)
    app.update_preferences_use_case = UpdatePreferencesUseCase(
        repository=safe_profile_repo
    )
    app.register_user_use_case = RegisterUserUseCase(repository=user_repo)
    app.list_users_use_case = ListUsersUseCase(repository=safe_user_repo)
    app.update_user_plan_use_case = UpdateUserPlanUseCase(repository=safe_user_repo)
    app.toggle_user_active_use_case = ToggleUserActiveUseCase(repository=safe_user_repo)

    # Registered first on purpose. Flask runs `after_request` hooks in reverse
    # registration order, so this one executes last -- after the cookie hook
    # below has finished rebuilding the response, which is what guarantees no
    # response can slip out without the headers.
    init_security(app)

    # Every template gets a CSRF token and today's date, so no form can forget
    # the hidden field and the date picker always has a sane default.
    @app.context_processor
    def inject_template_globals() -> dict:
        csrf_manager: CSRFTokenManager = app.csrf_manager
        token = csrf_manager.generate_token()
        g.csrf_token = token
        return {"csrf_token": token, "today": date.today().isoformat()}

    @app.context_processor
    def inject_user_context() -> dict:
        from flask import session

        # `current_user_id` lets the admin list disable the ban action on the
        # signed-in admin's own row. Like `is_staff`, it comes from the session
        # written at login so every template can read it without a DRF call.
        return {
            "current_user_is_staff": session.get("is_staff", False),
            "current_user_id": session.get("user_id"),
        }

    @app.after_request
    def apply_security_cookies(response: Response) -> Response:
        # The CSRF cookie and the rendered form field carry the same token, so a
        # JS echo of the cookie still validates against a freshly issued token.
        token = getattr(g, "csrf_token", None)
        if token:
            app.csrf_manager.set_csrf_cookie(response, token)

        # Persist tokens rotated mid-request by the auto-refreshing repositories.
        refreshed = getattr(g, "refreshed_tokens", None)
        if refreshed:
            app.cookie_manager.set_tokens(response, *refreshed)
        return response

    # ------------------------------------------------------------------
    # Graceful degradation when the DRF backend is unreachable
    # ------------------------------------------------------------------
    # `ServiceUnavailableError` is the one the DRF client raises for transport
    # failures. The `httpx` handlers are a belt-and-braces fallback for a call
    # that escapes the adapter -- through a future code path, say -- and must
    # never be left to surface as a raw 500.
    #
    # There is intentionally no generic 500 handler. A 500 means a bug in *this*
    # app, and a branded page would hide the traceback that is the only useful
    # output of one. Only external failures get dressed up.

    def _render_service_unavailable():
        """Render the branded 503 page. Returns a (body, status) tuple."""
        return render_template("errors/503.html"), 503

    @app.errorhandler(503)
    def service_unavailable(error: ServiceUnavailable):
        # Reached when the app itself aborts with `abort(503)` or a proxy in
        # front of it forwards a 503, rather than by the DRF call failing.
        return _render_service_unavailable()

    @app.errorhandler(ServiceUnavailableError)
    @app.errorhandler(httpx.TimeoutException)
    @app.errorhandler(httpx.ConnectError)
    def handle_backend_unreachable(error: Exception):
        # `error` is deliberately not rendered. It can carry the backend URL and
        # the failure's own message, and this page is shown to end users; the
        # detail belongs in the server log, not on screen.
        app.logger.warning("DRF API unreachable, serving 503: %s", error)
        return _render_service_unavailable()

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(users_bp)

    # Root route
    @app.route("/")
    def index():
        return redirect(url_for("dashboard.index"))

    @app.route("/healthz")
    def healthz():
        """Liveness probe that never touches the backend."""
        return {"status": "ok", "debug": app.debug}

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=app.debug, port=5000)
