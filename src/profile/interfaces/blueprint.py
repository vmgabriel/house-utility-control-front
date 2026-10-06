"""Profile Flask Blueprint."""

from dataclasses import replace

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from src.profile.interfaces.viewmodels import ProfileViewModel
from src.shared.auth.csrf import CSRFTokenManager
from src.shared.auth.jwt_cookie_manager import JWTCookieManager
from src.shared.http.drf_client import ServiceUnavailableError

profile_bp = Blueprint("profile", __name__, url_prefix="/profile")


@profile_bp.route("/", methods=["GET", "POST"])
async def index():
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)

    if not access_token:
        return redirect(url_for("auth.login"))

    get_uc = current_app.get_profile_use_case
    update_uc = current_app.update_profile_use_case
    csrf_manager: CSRFTokenManager = current_app.csrf_manager

    if request.method == "POST":
        csrf_token = request.form.get("csrf_token", "")
        if not csrf_manager.validate_token(csrf_token):
            flash("Invalid CSRF token", "error")
            return redirect(url_for("profile.index"))

        try:
            current_profile = await get_uc.execute(access_token)
            merged = replace(
                current_profile,
                first_name=request.form.get("first_name", "").strip()
                or current_profile.first_name,
                last_name=request.form.get("last_name", "").strip()
                or current_profile.last_name,
                timezone=request.form.get("timezone", "").strip()
                or current_profile.timezone,
                avatar_url=(
                    request.form.get("avatar_url", "").strip()
                    if "avatar_url" in request.form
                    else current_profile.avatar_url
                ),
                bio=(
                    request.form.get("bio", "").strip()
                    if "bio" in request.form
                    else current_profile.bio
                ),
            )
            await update_uc.execute(access_token, merged)
            flash("Profile updated successfully!", "success")
        except ServiceUnavailableError:
            raise
        except Exception as e:
            flash(f"Failed to update: {str(e)}", "error")

        return redirect(url_for("profile.index"))

    # GET
    try:
        profile = await get_uc.execute(access_token)
        vm = ProfileViewModel.from_domain(profile)
    except ServiceUnavailableError:
        raise
    except Exception:
        flash("Could not load profile", "error")
        vm = None

    return render_template("profile/index.html", profile=vm)


@profile_bp.route("/preferences", methods=["POST"])
async def preferences():
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    access_token = cookie_manager.get_access_token(request)

    if not access_token:
        return redirect(url_for("auth.login"))

    update_pref_uc = current_app.update_preferences_use_case
    csrf_manager: CSRFTokenManager = current_app.csrf_manager

    csrf_token = request.form.get("csrf_token", "")
    if not csrf_manager.validate_token(csrf_token):
        flash("Invalid CSRF token", "error")
        return redirect(url_for("profile.index"))

    try:
        await update_pref_uc.execute(
            access_token,
            language=request.form.get("language", "es"),
            currency=request.form.get("currency", "USD"),
            date_format=request.form.get("date_format", "YYYY-MM-DD"),
        )
        flash("Preferences updated successfully!", "success")
    except ServiceUnavailableError:
        raise
    except Exception as e:
        flash(f"Failed to update: {str(e)}", "error")

    return redirect(url_for("profile.index"))
