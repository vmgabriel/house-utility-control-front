"""Rentals Flask blueprint: houses, apartments, and their sub-resources.

Views here are deliberately thin: read a value from the form, hand it to a use
case, and render ViewModels. No business rule lives in this module, and the only
arithmetic happens in the domain or in Alpine.js for a live preview.

Every interactive element lives inside the single top-level ``x-data`` scope of
its template (AGENTS.md, "Alpine.js Scope Strictness"). A button placed outside
that scope would silently do nothing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from src.infrastructure.auth.csrf import CSRFTokenManager
from src.infrastructure.auth.jwt_cookie_manager import JWTCookieManager
from src.rentals.domain.exceptions import RentalsDomainError
from src.rentals.domain.value_objects import (
    ApartmentId,
    DocumentType,
    HouseId,
    Period,
    UtilityType,
)
from src.rentals.interfaces.viewmodels import (
    DOCUMENT_TYPE_LABELS,
    UTILITY_LABELS,
    ApartmentViewModel,
    DocumentViewModel,
    HouseViewModel,
    PaymentRecordViewModel,
    PaymentSummaryViewModel,
    UtilityReadingViewModel,
)
from src.shared.utils.currency import CurrencyPreference

rentals_bp = Blueprint("rentals", __name__, url_prefix="/rentals")


def _access_token() -> str | None:
    """Read the JWT from its HttpOnly cookie."""
    cookie_manager: JWTCookieManager = current_app.cookie_manager
    return cookie_manager.get_access_token(request)


def _require_csrf() -> bool:
    """Validate the hidden form field against the manager's token."""
    csrf_manager: CSRFTokenManager = current_app.csrf_manager
    return csrf_manager.validate_token(request.form.get("csrf_token", ""))


def _parse_decimal(raw: str | None, label: str) -> Decimal:
    """Parse a decimal from a form field, flashing a message on failure.

    `Decimal` construction is the only parsing that happens in the web layer;
    the domain re-validates the value through its value objects regardless.
    """
    text = (raw or "").strip()
    if not text:
        raise RentalsDomainError(f"{label} is required.")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise RentalsDomainError(f"{label} must be a number.") from exc


def _parse_date(raw: str | None, label: str) -> date:
    """Parse an ISO date from a form field."""
    text = (raw or "").strip()
    if not text:
        raise RentalsDomainError(f"{label} is required.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise RentalsDomainError(f"{label} must be a valid date.") from exc


def _parse_enum(enum_cls, raw: str | None, label: str):
    """Parse an enum-valued form field.

    A `<select>` is client-controlled, so an unexpected member has to become a
    `RentalsDomainError`. Constructing the enum directly would raise a bare
    `ValueError`, which the views' `except RentalsDomainError` would not catch
    -- turning a tampered query string into a 500 instead of a flash message.
    """
    text = (raw or "").strip()
    try:
        return enum_cls(text)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise RentalsDomainError(f"{label} must be one of: {allowed}.") from exc


def _parse_uuid(raw: str | None, label: str) -> UUID:
    """Parse a UUID from a form field."""
    try:
        return UUID((raw or "").strip())
    except (ValueError, AttributeError, TypeError) as exc:
        raise RentalsDomainError(f"{label} is not a valid identifier.") from exc


def _current_period() -> Period:
    """The month shown by default on the apartment hub.

    Goes through the injected `Clock` rather than `date.today()` directly, so a
    test with a frozen clock gets a deterministic period in both the summary
    query and the `max` attribute on the date inputs.
    """
    today = current_app.clock.today()
    return Period(year=today.year, month=today.month)


@dataclass(frozen=True, slots=True)
class NextcloudUploadConfig:
    """Everything the browser needs to PUT a file into the Nextcloud drop folder.

    Holds no account credentials by design. Nextcloud's public-share WebDAV
    endpoint authenticates with the share token as the username and an *empty*
    password, so the only secret reaching the DOM is that token -- which is
    scoped to one upload folder and revocable, unlike an account password.
    """

    webdav_base_url: str
    share_token: str

    #: Human-readable names, used to tell an operator exactly what is missing.
    ENV_VARS = ("NEXTCLOUD_WEBDAV_BASE_URL", "NEXTCLOUD_SHARE_TOKEN")

    @property
    def missing(self) -> tuple[str, ...]:
        """Which environment variables are absent or blank."""
        return tuple(
            name
            for name, value in (
                ("NEXTCLOUD_WEBDAV_BASE_URL", self.webdav_base_url),
                ("NEXTCLOUD_SHARE_TOKEN", self.share_token),
            )
            if not value.strip()
        )

    @property
    def configured(self) -> bool:
        return not self.missing

    @property
    def unconfigured_message(self) -> str:
        """The operator-facing message naming the exact missing variables."""
        return (
            "Nextcloud upload is not configured. Please check "
            f"{' and '.join(self.missing)} in .env"
        )

    def to_js_config(self) -> dict[str, object]:
        """The serialisable payload handed to Alpine.

        Carries `configured` so the template can disable the file input and show
        the reason, instead of failing on the first PUT.
        """
        return {
            "webdavBaseUrl": self.webdav_base_url.rstrip("/"),
            "shareToken": self.share_token,
            "configured": self.configured,
            "missing": list(self.missing),
        }


def _nextcloud_upload_config() -> NextcloudUploadConfig:
    """Read the drop-folder settings from app config.

    App config rather than ``os.environ`` inside the template, so the values are
    injectable in tests and a missing setting renders a disabled control rather
    than an undefined JavaScript identifier.
    """
    config = current_app.config
    return NextcloudUploadConfig(
        webdav_base_url=str(config.get("NEXTCLOUD_WEBDAV_BASE_URL", "")),
        share_token=str(config.get("NEXTCLOUD_SHARE_TOKEN", "")),
    )


async def _currency_preference(access_token: str) -> CurrencyPreference:
    """Read the user's currency and locale from the profile context.

    Reached through `current_app` rather than an import: bounded contexts never
    import each other (AGENTS.md, constraint 1), and this module deliberately
    has no `from src.profile...` anywhere. Only two attributes are read, and
    `CurrencyPreference.from_profile` reads them dynamically, so rentals has no
    compile-time dependency on the profile entity either.

    Any failure degrades to the default currency rather than taking the page
    down. A user whose profile is missing, unreachable, or carries a blank
    `currency` should still see their rent and balance -- in a fallback currency,
    not in none at all. The rentals pages are otherwise fully functional without
    this call, so failing here would be a self-inflicted outage.
    """
    try:
        profile = await current_app.get_profile_use_case.execute(access_token)
    except Exception as error:  # noqa: BLE001 - deliberately broad, see docstring
        current_app.logger.warning(
            "Falling back to the default currency; profile unavailable: %s",
            type(error).__name__,
        )
        return CurrencyPreference()
    return CurrencyPreference.from_profile(profile)


@rentals_bp.route("/")
async def index():
    """List the caller's houses."""
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    houses, currency = await asyncio.gather(
        current_app.get_houses_use_case.execute(access_token),
        _currency_preference(access_token),
    )
    return render_template(
        "rentals/houses.html",
        houses=[HouseViewModel.from_domain(house) for house in houses],
        document_types=DOCUMENT_TYPE_LABELS,
        utility_types=UTILITY_LABELS,
        currency=currency,
    )


@rentals_bp.route("/houses", methods=["POST"])
async def create_house():
    """Register a new property.

    The owner is not a form field: the backend derives it from the JWT
    (`_authenticated_user_id`), so accepting one here would invite a value the
    server ignores.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("rentals.index"))

    try:
        house = await current_app.create_house_use_case.execute(
            access_token,
            name=request.form.get("name", ""),
            street=request.form.get("street", ""),
            city=request.form.get("city", ""),
            state=request.form.get("state", ""),
            country=request.form.get("country", ""),
        )
        flash(f"Property created: {house.name}.", "success")
    except RentalsDomainError as exc:
        flash(str(exc), "error")
    return redirect(url_for("rentals.index"))


@rentals_bp.route("/houses/<uuid:house_id>")
async def house_detail(house_id: UUID):
    """One house and its apartments."""
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    houses, currency = await asyncio.gather(
        current_app.get_houses_use_case.execute(access_token),
        _currency_preference(access_token),
    )
    target = HouseId(house_id)
    house = next((item for item in houses if item.id == target), None)
    if house is None:
        flash("House not found.", "error")
        return redirect(url_for("rentals.index"))

    apartments = await current_app.get_apartments_use_case.execute(access_token, target)
    return render_template(
        "rentals/house_detail.html",
        house=HouseViewModel.from_domain(house),
        apartments=[
            ApartmentViewModel.from_domain(apartment, currency)
            for apartment in apartments
        ],
        currency=currency,
    )


@rentals_bp.route("/apartments/<uuid:apartment_id>", methods=["GET", "POST"])
async def apartment_detail(apartment_id: UUID):
    """The apartment hub, and the target of its three POST forms.

    The hub shows the payment summary for the selected month plus three tabs
    (documents, utilities, payments). All five reads are independent, so they run
    concurrently; a single slow endpoint no longer serialises behind the others.
    """
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))

    identifier = ApartmentId(apartment_id)
    if request.method == "POST":
        return await _handle_apartment_post(access_token, identifier)

    period = _current_period()

    # Six independent reads; run them concurrently so one slow endpoint does not
    # serialise behind the others. `return_exceptions=True` keeps a failure in
    # the optional collections from taking down the whole hub: an apartment with
    # no readings yet can legitimately 404 on some of these.
    results = await asyncio.gather(
        current_app.get_apartment_use_case.execute(access_token, identifier),
        current_app.get_payment_summary_use_case.execute(
            access_token, identifier, period
        ),
        current_app.get_utility_readings_use_case.execute(access_token, identifier),
        current_app.get_payment_records_use_case.execute(access_token, identifier),
        current_app.get_documents_use_case.execute(access_token, identifier),
        _currency_preference(access_token),
        return_exceptions=True,
    )
    apartment, summary, readings, payments, documents, currency = results

    # The apartment itself is the one read that must succeed: without it there is
    # no page to render, so its error propagates to the 404/503 handling.
    if isinstance(apartment, BaseException):
        raise apartment
    # The rest degrade to an empty section rather than a 500.
    summary = None if isinstance(summary, BaseException) else summary
    readings = [] if isinstance(readings, BaseException) else readings
    payments = [] if isinstance(payments, BaseException) else payments
    documents = [] if isinstance(documents, BaseException) else documents
    # `_currency_preference` already swallows its own failures, so it only
    # arrives here as an exception if something entirely unexpected happened.
    if isinstance(currency, BaseException):
        currency = CurrencyPreference()

    return render_template(
        "rentals/apartment_detail.html",
        apartment=ApartmentViewModel.from_domain(apartment, currency),
        summary=(
            None
            if summary is None
            else PaymentSummaryViewModel.from_domain(summary, currency)
        ),
        readings=[
            UtilityReadingViewModel.from_domain(item, currency) for item in readings
        ],
        payments=[
            PaymentRecordViewModel.from_domain(item, currency) for item in payments
        ],
        documents=[DocumentViewModel.from_domain(item) for item in documents],
        period=period,
        document_types=DOCUMENT_TYPE_LABELS,
        utility_types=UTILITY_LABELS,
        nextcloud=_nextcloud_upload_config().to_js_config(),
        currency=currency,
    )


async def _handle_apartment_post(access_token: str, identifier: ApartmentId):
    """Dispatch the hub's three POST actions.

    CSRF is validated first for every action: it is the one check that must not
    depend on which form was submitted.
    """
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("rentals.apartment_detail", apartment_id=identifier))

    action = request.form.get("action", "")
    try:
        if action == "record_reading":
            message = await _record_reading(access_token, identifier)
        elif action == "record_payment":
            message = await _record_payment(access_token, identifier)
        elif action == "register_document":
            message = await _register_document(access_token, identifier)
        else:
            flash("Unknown action.", "error")
            return redirect(
                url_for("rentals.apartment_detail", apartment_id=identifier)
            )
        flash(message, "success")
    except RentalsDomainError as exc:
        # Domain validation: the message is written for end users.
        flash(str(exc), "error")
    return redirect(url_for("rentals.apartment_detail", apartment_id=identifier))


async def _record_reading(access_token: str, identifier: ApartmentId) -> str:
    """Record a meter reading from the Alpine-previewed form."""
    utility_type = _parse_enum(UtilityType, request.form.get("utility_type"), "Utility")
    reading = await current_app.record_utility_reading_use_case.execute(
        access_token,
        apartment_id=identifier,
        utility_type=utility_type,
        reading_date=_parse_date(request.form.get("reading_date"), "Reading date"),
        current=_parse_decimal(request.form.get("current_reading"), "Current reading"),
        previous=_parse_decimal(
            request.form.get("previous_reading"), "Previous reading"
        ),
        unit_cost=_parse_decimal(request.form.get("unit_cost"), "Unit cost"),
    )
    label = UTILITY_LABELS.get(utility_type.value, utility_type.value)
    currency = await _currency_preference(access_token)
    return (
        f"{label} reading recorded: {reading.consumption} units, "
        f"{currency.format(reading.total_cost)}."
    )


async def _record_payment(access_token: str, identifier: ApartmentId) -> str:
    """Record a rent payment from the hub's payment form."""
    record = await current_app.record_payment_use_case.execute(
        access_token,
        apartment_id=identifier,
        payment_date=_parse_date(request.form.get("payment_date"), "Payment date"),
        amount=_parse_decimal(request.form.get("amount"), "Amount"),
        notes=request.form.get("notes"),
    )
    currency = await _currency_preference(access_token)
    return (
        f"Payment of {currency.format(record.amount.amount)} "
        f"recorded ({record.status.value})."
    )


async def _register_document(access_token: str, identifier: ApartmentId) -> str:
    """Register a document reference uploaded straight to Nextcloud.

    No file bytes reach this function. The browser PUTs the file to the drop
    folder first and submits only the resulting URL, which the domain validates
    as an absolute URL before the backend is called.

    The server-side configuration check is a real guard, not belt-and-braces:
    the template disables the file input when the drop folder is unconfigured,
    but that is a client-side courtesy. A crafted POST can skip it, and it must
    not be able to register a `file_url` the UI never actually uploaded to.
    """
    upload_config = _nextcloud_upload_config()
    if not upload_config.configured:
        raise RentalsDomainError(upload_config.unconfigured_message)

    document = await current_app.register_document_use_case.execute(
        access_token,
        apartment_id=identifier,
        document_type=_parse_enum(
            DocumentType, request.form.get("document_type"), "Document type"
        ),
        file_url=request.form.get("file_url", ""),
        description=request.form.get("description"),
    )
    return f"Document registered: {document.file_url.rsplit('/', 1)[-1]}."


@rentals_bp.route("/houses/<uuid:house_id>/apartments", methods=["POST"])
async def create_apartment(house_id: UUID):
    """Create an apartment inside a house."""
    access_token = _access_token()
    if not access_token:
        return redirect(url_for("auth.login"))
    if not _require_csrf():
        flash("Invalid CSRF token.", "error")
        return redirect(url_for("rentals.house_detail", house_id=house_id))

    try:
        floor_raw = request.form.get("floor", "").strip()
        floor = int(floor_raw) if floor_raw else 1
        await current_app.create_apartment_use_case.execute(
            access_token,
            house_id=HouseId(house_id),
            number=request.form.get("number", ""),
            floor=floor,
            monthly_rent=_parse_decimal(
                request.form.get("monthly_rent"), "Monthly rent"
            ),
        )
        flash("Apartment created.", "success")
    except RentalsDomainError as exc:
        flash(str(exc), "error")
    except ValueError:
        flash("Floor must be a whole number.", "error")
    return redirect(url_for("rentals.house_detail", house_id=house_id))
