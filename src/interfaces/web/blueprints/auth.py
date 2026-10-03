"""Authentication blueprint."""

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from src.application.use_cases.auth import (
    AuthenticateUserUseCase,
    LogoutUserUseCase,
)
from src.domain.exceptions import AuthenticationError, DomainException
from src.infrastructure.api.drf_client import ServiceUnavailableError
from src.infrastructure.auth.csrf import CSRFTokenManager
from src.infrastructure.auth.jwt_cookie_manager import JWTCookieManager

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _csrf_ok() -> bool:
    """Validate the CSRF token posted with a state-changing request."""
    csrf_manager: CSRFTokenManager = current_app.csrf_manager
    return csrf_manager.validate_token(request.form.get("csrf_token", ""))


@auth_bp.route("/login", methods=["GET", "POST"])
async def login() -> Response:
    """Login page and handler."""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not _csrf_ok():
            flash("Invalid or expired CSRF token. Please try again.", "error")
            return redirect(url_for("auth.login"))

        auth_use_case: AuthenticateUserUseCase = current_app.auth_use_case
        try:
            access_token, refresh_token, user = await auth_use_case.execute(
                email, password
            )
        except AuthenticationError:
            flash("Invalid email or password. Please try again.", "error")
            return redirect(url_for("auth.login"))
        except ServiceUnavailableError:
            # The backend never answered, so there is no verdict to report on the
            # form. Re-raised for the branded 503 page, which says "try again in
            # a moment" far better than a vague inline error can.
            raise
        except DomainException:
            flash("Authentication service unavailable. Please try again.", "error")
            return redirect(url_for("auth.login"))

        # Set secure cookies
        cookie_manager: JWTCookieManager = current_app.cookie_manager
        response = redirect(url_for("dashboard.index"))
        cookie_manager.set_tokens(response, access_token, refresh_token)

        # Persist the staff flag and user id so the nav, staff-only routes, and
        # the admin list's self-ban guard can read them without re-fetching
        # /users/me/ on every request.
        session["is_staff"] = user.is_staff
        session["user_id"] = user.id

        flash(f"Welcome back, {user.name}!", "success")
        return response

    # GET request - render login form
    return render_template("auth/login.html")


@auth_bp.route("/logout", methods=["POST"])
async def logout() -> Response:
    """Logout handler."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    logout_use_case: LogoutUserUseCase = current_app.logout_use_case

    if not _csrf_ok():
        flash("Invalid or expired CSRF token.", "error")
        return redirect(url_for("dashboard.index"))

    access_token = cookie_manager.get_access_token(request)
    if access_token:
        try:
            await logout_use_case.execute(access_token)
        except DomainException:
            # A failed remote logout must not trap the user in a stale session;
            # the local cookies are cleared either way.
            #
            # This includes ServiceUnavailableError, and that is intentional --
            # it is the one flow where a 503 would be the *worse* outcome.
            # Logging out is a purely local action: refusing to drop the session
            # because the backend is unreachable would leave the user
            # authenticated against a service that cannot check anything anyway.
            pass

    response = redirect(url_for("auth.login"))
    cookie_manager.clear_tokens(response)
    session.pop("is_staff", None)
    flash("You have been logged out.", "info")
    return response


@auth_bp.route("/register", methods=["GET"])
async def register() -> str:
    """Register page (placeholder for now)."""
    return render_template("auth/register.html")
