"""Flask application factory.

This is the composition root: it is the single place where the outer layers
(Flask, httpx, DRF) are wired to the inner layers (use cases, domain). Nothing
below this module knows that Flask or httpx exist.
"""

import os
from datetime import date
from pathlib import Path

import httpx
from dotenv import load_dotenv
from flask import Flask, Response, g, redirect, render_template, request, url_for
from jinja2 import FileSystemLoader
from werkzeug.exceptions import ServiceUnavailable

from src.budget.application.use_cases import (
    CreateTransactionUseCase,
    DeleteTransactionUseCase,
    FetchTransactionsUseCase,
    GetDashboardOverviewUseCase,
    GetTransactionUseCase,
    UpdateTransactionUseCase,
)
from src.budget.infrastructure.repositories import (
    DRFDashboardRepository,
    DRFTransactionRepository,
)
from src.budget.interfaces.blueprints import dashboard_bp, transactions_bp
from src.identity.application.use_cases import (
    AuthenticateUserUseCase,
    LogoutUserUseCase,
    RefreshTokenUseCase,
)
from src.identity.infrastructure.repository import DRFAuthRepository
from src.identity.interfaces.blueprint import auth_bp
from src.interfaces.web.security import init_security
from src.profile.application.use_cases import (
    GetProfileUseCase,
    UpdatePreferencesUseCase,
    UpdateProfileUseCase,
)
from src.profile.infrastructure.repository import DRFProfileRepository
from src.profile.interfaces.blueprint import profile_bp
from src.rentals.application.wiring import build_rentals_use_cases
from src.rentals.infrastructure.drf_client import DrfRentalsClient
from src.rentals.infrastructure.nextcloud_webdav import (
    MAX_UPLOAD_BYTES,
)
from src.rentals.infrastructure.nextcloud_webdav import (
    build_client as build_nextcloud_client,
)
from src.rentals.interfaces.web.routes import rentals_bp
from src.rentals.interfaces.web.upload_proxy import build_upload_proxy_blueprint
from src.shared.auth.csrf import CSRFTokenManager
from src.shared.auth.jwt_cookie_manager import CookieConfig, JWTCookieManager
from src.shared.http.auto_refresh import AutoRefreshingRepository
from src.shared.http.drf_client import DRFAPIClient, ServiceUnavailableError
from src.shared.infrastructure.clock import SystemClock
from src.users.application.use_cases import (
    GetAdminStatsUseCase,
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
    app = Flask(__name__, template_folder="templates")
    # Every bounded context keeps its Jinja2 templates inside its own package, so
    # each directory is registered on the search path. `profile` and `users` are
    # the exceptions: their templates are still under the flat `templates/` tree
    # and resolve through the first entry.
    #
    # Resolved from this file rather than from CWD, so the paths hold no matter
    # where the process was started from. `parents[2]` is `src/`.
    src_root = Path(__file__).resolve().parents[2]
    context_templates = [
        src_root / context / "interfaces" / subdir
        for context, subdir in (
            ("budget", "templates"),
            ("identity", "templates"),
            ("rentals", "web/templates"),
        )
    ]
    app.jinja_loader = FileSystemLoader(
        [Path(app.root_path) / "templates", *context_templates]
    )

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
    rentals_client = DrfRentalsClient(api_client)

    # Nextcloud upload path served on THIS app's origin by the reverse proxy.
    #
    # There are deliberately no Nextcloud credentials in the application. The
    # browser PUTs to a same-origin path and the proxy in front of Nextcloud
    # injects the `Authorization` header from its own environment. That keeps
    # the App Password out of the DOM -- where it was readable in the page source
    # by every signed-in user -- removes CORS and its preflight entirely, and
    # still routes no file bytes through Flask (AGENTS.md, rule 6).
    #
    # Read into app config rather than `os.environ` inside the template, so the
    # value stays injectable in tests and a missing setting renders a disabled
    # control rather than an undefined JavaScript identifier.
    app.config["NEXTCLOUD_UPLOAD_PATH"] = os.getenv(
        "NEXTCLOUD_UPLOAD_PATH", "/nextcloud-dav/rentals"
    )

    # LOCAL DEV ONLY. When set, documents are POSTed to a Flask route which
    # forwards them to Nextcloud, instead of the browser PUTing straight to the
    # reverse-proxy path. That breaks AGENTS.md rule 6 (files through the BFF),
    # so it is off by default and confined to a single opt-in module --
    # see `src/rentals/interfaces/web/upload_proxy.py` for why it exists and
    # what it does not fix. Production must leave this false.
    app.config["NEXTCLOUD_UPLOAD_VIA_BFF"] = (
        os.getenv("NEXTCLOUD_UPLOAD_VIA_BFF", "false").lower() in TRUTHY_VALUES
    )

    # A hard ceiling on the request body, so a large upload is refused by Flask
    # with a 413 instead of being buffered into the worker. Only relevant when
    # the fallback above is on; the proxy path never carries a body through here.
    if app.config["NEXTCLOUD_UPLOAD_VIA_BFF"]:
        app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    # Constructed here, the one place allowed to build concrete adapters
    # (AGENTS.md, constraint 3). `None` when the settings are incomplete, which
    # the view reports as "not configured" instead of raising.
    nextcloud_client = build_nextcloud_client(
        base_url=os.getenv("NEXTCLOUD_BASE_URL", ""),
        dav_path=os.getenv("NEXTCLOUD_DAV_PATH", ""),
        basic_auth=os.getenv("NEXTCLOUD_BASIC_AUTH", ""),
    )
    app.nextcloud_webdav_client = nextcloud_client

    # The shared clock is exposed on the app so views read "today" from one
    # injectable source instead of calling `date.today()` directly.
    app.clock = SystemClock()

    # Exposed so tests can rebuild a bounded context's use cases against a
    # substitute adapter or a frozen clock without reaching into this factory.
    app.extensions["rentals_api_client"] = rentals_client

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
    # Rentals goes through the same proxy. Its adapter methods take the access
    # token as their first positional argument precisely so this works: the proxy
    # identifies the token by position and substitutes the refreshed one there.
    safe_rentals_client = refreshing(rentals_client)

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
    # Same auto-refreshing proxy as the other admin routes, so an expired token
    # refreshes instead of logging the admin out mid-audit.
    app.get_admin_stats_use_case = GetAdminStatsUseCase(repository=safe_user_repo)

    # Rentals use cases, built in their own composition root so no view ever
    # assembles one.
    build_rentals_use_cases(safe_rentals_client, clock=app.clock).attach_to(app)

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
        #
        # The display name is here for the nav's avatar dropdown. It falls back
        # to "User" rather than rendering an empty label, so the initials below
        # are always two-or-one characters and the avatar never collapses.
        name = (session.get("user_name") or "").strip() or "User"
        # First letter of each of the first two words: "Ana Souza" -> "AS".
        # Falls back to "U" for a name that is somehow all whitespace.
        initials = (
            "".join(part[0].upper() for part in name.split()[:2] if part) or "U"
        ).upper()

        return {
            "current_user_is_staff": session.get("is_staff", False),
            "current_user_id": session.get("user_id"),
            "current_user_name": name,
            "current_user_initials": initials,
            # Always None today. `avatar_url` belongs to the profile context and
            # identity cannot import profile (AGENTS.md, constraint 1), so the
            # dropdown renders initials. The template already branches on this,
            # so wiring an avatar later is a one-line change here -- see
            # AGENTS.md on why it is not done now.
            "current_user_avatar": None,
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
    app.register_blueprint(rentals_bp)

    # LOCAL DEV ONLY -- the rule-6 upload fallback, registered as its own
    # blueprint and only when its flag is set. Removing this block (and the
    # config/client setup above) removes the exception entirely; nothing else in
    # the app knows the route exists.
    if app.config["NEXTCLOUD_UPLOAD_VIA_BFF"]:
        app.register_blueprint(build_upload_proxy_blueprint())

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
