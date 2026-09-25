"""Flask application factory.

This is the composition root: it is the single place where the outer layers
(Flask, httpx, DRF) are wired to the inner layers (use cases, domain). Nothing
below this module knows that Flask or httpx exist.
"""

import os
from datetime import date

from dotenv import load_dotenv
from flask import Flask, Response, g, redirect, request, url_for

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
from src.infrastructure.api.drf_client import DRFAPIClient
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

    # Every template gets a CSRF token and today's date, so no form can forget
    # the hidden field and the date picker always has a sane default.
    @app.context_processor
    def inject_template_globals() -> dict:
        csrf_manager: CSRFTokenManager = app.csrf_manager
        token = csrf_manager.generate_token()
        g.csrf_token = token
        return {"csrf_token": token, "today": date.today().isoformat()}

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

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(transactions_bp)

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
