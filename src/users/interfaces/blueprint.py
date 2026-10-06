"""User Management Flask Blueprint."""

from functools import wraps

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from src.shared.auth.csrf import CSRFTokenManager
from src.shared.auth.jwt_cookie_manager import JWTCookieManager
from src.shared.http.drf_client import ServiceUnavailableError
from src.users.domain.entities import UserPlan
from src.users.interfaces.viewmodels import AdminUserViewModel

users_bp = Blueprint("users", __name__, url_prefix="/users")


def requires_staff(f):
    """Decorator to ensure only staff users can access the route."""

    @wraps(f)
    async def decorated_function(*args, **kwargs):
        # The login flow persists `is_staff` in the signed session cookie.
        if not session.get("is_staff", False):
            flash("Access denied. Staff privileges required.", "error")
            return redirect(url_for("dashboard.index"))
        return await f(*args, **kwargs)

    return decorated_function


@users_bp.route("/register", methods=["GET", "POST"])
async def register():
    csrf_manager: CSRFTokenManager = current_app.csrf_manager

    if request.method == "POST":
        if not csrf_manager.validate_token(request.form.get("csrf_token", "")):
            flash("Invalid or expired CSRF token. Please try again.", "error")
            return redirect(url_for("users.register"))

        email = request.form.get("email", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("users.register"))

        register_uc = current_app.register_user_use_case
        try:
            await register_uc.execute(email, full_name, password)
            flash("Account created successfully! Please log in.", "success")
            return redirect(url_for("auth.login"))
        except ServiceUnavailableError:
            raise
        except Exception as e:
            flash(f"Registration failed: {str(e)}", "error")

    return render_template("users/register.html")


@users_bp.route("/admin", methods=["GET"])
@requires_staff
async def admin_dashboard():
    """Admin landing page: headline counts plus a way into user management."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)
    stats_uc = current_app.get_admin_stats_use_case

    try:
        stats = await stats_uc.execute(access_token)
    except ServiceUnavailableError:
        # No backend, so no counts. Re-raised for the branded 503 page rather
        # than a page of dashes that reads like "zero users".
        raise
    except Exception:
        stats = None
        flash("Could not load admin statistics.", "error")

    return render_template("users/admin_dashboard.html", stats=stats)


# Under `/admin/` rather than `/admin` so it does not shadow the dashboard
# above. Flask matches the static segment before `<user_id>`, so this stays
# unambiguous alongside the `/admin/<user_id>/...` POST routes below.
@users_bp.route("/admin/list", methods=["GET"])
@requires_staff
async def admin_list():
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)
    list_uc = current_app.list_users_use_case

    if not access_token:
        return redirect(url_for("auth.login"))

    page = int(request.args.get("page", 1))
    try:
        count, users = await list_uc.execute(access_token, page=page, page_size=20)
        viewmodels = [AdminUserViewModel.from_domain(u) for u in users]
    except ServiceUnavailableError:
        raise
    except Exception:
        count = 0
        viewmodels = []
        flash("Failed to load users.", "error")

    return render_template(
        "users/admin_list.html", users=viewmodels, page=page, count=count
    )


@users_bp.route("/admin/<user_id>/plan", methods=["POST"])
@requires_staff
async def admin_update_plan(user_id: str):
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)
    csrf_manager: CSRFTokenManager = current_app.csrf_manager

    if not csrf_manager.validate_token(request.form.get("csrf_token", "")):
        flash("Invalid CSRF token", "error")
        return redirect(url_for("users.admin_list"))

    new_plan = request.form.get("plan", "free")
    update_plan_uc = current_app.update_user_plan_use_case

    try:
        await update_plan_uc.execute(access_token, user_id, UserPlan(new_plan))
        flash("User plan updated successfully.", "success")
    except ServiceUnavailableError:
        raise
    except Exception as e:
        flash(f"Failed to update plan: {str(e)}", "error")

    return redirect(url_for("users.admin_list"))


@users_bp.route("/admin/<user_id>/toggle", methods=["POST"])
@requires_staff
async def admin_toggle_active(user_id: str):
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)
    csrf_manager: CSRFTokenManager = current_app.csrf_manager

    if not csrf_manager.validate_token(request.form.get("csrf_token", "")):
        flash("Invalid CSRF token", "error")
        return redirect(url_for("users.admin_list"))

    is_active = request.form.get("is_active") == "true"
    ban_reason = request.form.get("ban_reason", "").strip()
    toggle_uc = current_app.toggle_user_active_use_case

    try:
        await toggle_uc.execute(
            access_token, user_id, is_active, ban_reason=ban_reason or None
        )
        status = "activated" if is_active else "banned"
        flash(f"User {status} successfully.", "success")
    except ServiceUnavailableError:
        raise
    except Exception as e:
        flash(f"Failed to update status: {str(e)}", "error")

    return redirect(url_for("users.admin_list"))
