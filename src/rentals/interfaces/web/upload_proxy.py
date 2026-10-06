"""LOCAL DEVELOPMENT ONLY -- Flask-side upload fallback for Nextcloud.

.. warning::

   **This route deliberately breaks AGENTS.md rule 6.** Documents are not
   supposed to pass through Flask. They are kept in their own module, and
   behind their own flag, purely so document upload can be exercised during
   local development. Turn ``NEXTCLOUD_UPLOAD_VIA_BFF`` off and drop the single
   ``register_upload_proxy()`` call in ``app.py`` before deploying anywhere real.

Why it exists
-------------
The production design is that the browser ``PUT``s the file to a same-origin
path on this app and the reverse proxy streams it straight to Nextcloud,
injecting the credential from its own environment. That path is correct and
stays the default.

During local development the proxy route is often the least reliable part of
the stack: it needs Caddy running, a live Nextcloud, and a credential injected
from a separate process environment. When that is what is blocking you, this
route removes one moving part -- Flask accepts the upload and forwards it with
``httpx``.

What this does and does not fix
-------------------------------
It is worth being precise, because the usual justification for this pattern is
wrong. **Routing the upload through Flask avoids no CORS problem that the proxy
route did not already avoid.** Both are same-origin from the browser's point of
view -- ``localhost:5001`` either way -- so neither triggers a preflight, and
neither needs a credential in the browser. That is exactly why the production
design works, and why the three cross-origin attempts recorded in AGENTS.md
failed. Adding a Flask hop cannot rescue a cross-origin design, because this is
not one.

What the route buys is narrower and still real:

* the upload path stops depending on Caddy being up and correctly configured;
* the same flow works under a bare ``flask run``, with no proxy at all.

What it costs is the reason rule 6 exists -- every byte crosses the Flask
process, so an upload occupies a worker for its duration, and the BFF must be
healthy for uploads to work. The request body is therefore capped and the
upstream call is streamed rather than buffered.

The credential stays server-side. That is the property that made the abandoned
cross-origin designs unacceptable, and this route preserves it: the browser
sends a file and nothing else.
"""

from __future__ import annotations

from uuid import UUID

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from src.rentals.domain.exceptions import NextcloudUploadError
from src.shared.auth.csrf import CSRF_HEADER

#: The form field the browser uses to send the file.
FILE_FIELD = "file"

#: Endpoint prefix of the generated blueprint. The routes module builds the
#: fallback URL with `url_for(f"{UPLOAD_PROXY_BLUEPRINT}.upload_proxy", ...)`, so
#: the name lives in one place rather than being written out on both sides.
UPLOAD_PROXY_BLUEPRINT = "rentals_upload_proxy"


async def upload_proxy(apartment_id: UUID):
    """Accept a document upload and forward it to Nextcloud.

    Answers with JSON rather than a redirect or a flash, because the caller is
    ``fetch()`` in the browser rather than a form submission.

    ``apartment_id`` is validated as a UUID by the URL rule but deliberately
    does not select a storage location: the Nextcloud folder is shared, exactly
    as it is on the proxy path. Authorisation happens where it always did, when
    the document reference is registered through the authenticated
    ``register_document`` use case. Writing bytes into a shared folder is not a
    disclosure -- the file is not linked to anything until that step, which
    checks the caller may access the apartment.
    """
    if not current_app.cookie_manager.get_access_token(request):
        # 401 as JSON: this endpoint is reached by fetch(), never by a browser
        # navigation, so a redirect would deliver HTML where JSON is expected.
        return jsonify({"ok": False, "error": "Authentication required."}), 401

    if not current_app.csrf_manager.validate_token(request.headers.get(CSRF_HEADER)):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Invalid or missing CSRF token. Reload the page.",
                }
            ),
            403,
        )

    client = getattr(current_app, "nextcloud_webdav_client", None)
    if client is None:
        # Flag off, or the credential is missing. Report it the way the template
        # already renders an unconfigured control, rather than as a 500.
        return (
            jsonify(
                {
                    "ok": False,
                    "error": (
                        "Nextcloud upload is not configured. Please check "
                        "NEXTCLOUD_BASE_URL, NEXTCLOUD_DAV_PATH and "
                        "NEXTCLOUD_BASIC_AUTH in .env"
                    ),
                }
            ),
            503,
        )

    # Resolved before the upload, not after: this value is part of the stored
    # `file_url`, so a missing one must abort rather than leave an orphaned
    # object in Nextcloud that nothing will ever reference.
    public_path = str(current_app.config.get("NEXTCLOUD_UPLOAD_PATH", "")).strip()
    if not public_path:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "NEXTCLOUD_UPLOAD_PATH is not set, so the stored "
                    "URL cannot be built.",
                }
            ),
            503,
        )

    uploaded = request.files.get(FILE_FIELD)
    if uploaded is None or not uploaded.filename:
        return (
            jsonify({"ok": False, "error": f"No file was sent in '{FILE_FIELD}'."}),
            400,
        )

    # `secure_filename` strips directory components, so a crafted name such as
    # `../../../etc/passwd` cannot escape the target folder. It also mangles
    # non-ASCII names (accented characters become underscores), which is a real
    # cost of the fallback. It is not a correctness problem, because the
    # sanitised name is what gets stored in Nextcloud *and* what is returned as
    # `fileUrl` for the caller to submit as `file_url` -- the two always agree.
    filename = secure_filename(uploaded.filename)
    if not filename:
        return (
            jsonify(
                {"ok": False, "error": "The filename contained no usable characters."}
            ),
            400,
        )

    try:
        await client.upload(
            filename,
            uploaded.stream,
            content_type=uploaded.mimetype or "application/octet-stream",
        )
    except NextcloudUploadError as exc:
        return jsonify({"ok": False, "error": _operator_message(exc)}), 502

    # The URL handed back is the same-origin path on this app, NOT the Nextcloud
    # URL. That is what gets stored as the document's `file_url`, and it is
    # deliberately identical to what the proxy path returns, so a document
    # uploaded through this fallback is indistinguishable from one uploaded the
    # intended way -- only the party that moved the bytes differs.
    file_url = f"{request.host_url.rstrip('/')}/{public_path.strip('/')}/{filename}"
    return jsonify({"ok": True, "fileUrl": file_url, "filename": filename})


def _operator_message(exc: NextcloudUploadError) -> str:
    """Turn a failure into something an operator can act on.

    A 401/403 is overwhelmingly an expired or revoked App Password, and the
    generic "refused" wording sends people looking at the wrong layer.
    """
    if exc.status in (401, 403):
        return (
            f"Nextcloud refused the upload (HTTP {exc.status}). The App Password "
            "in NEXTCLOUD_BASIC_AUTH is likely expired or revoked."
        )
    return str(exc)


def build_upload_proxy_blueprint() -> Blueprint:
    """Return a fresh blueprint carrying the fallback route.

    A **new blueprint per call**, registered alongside `rentals_bp` rather than
    by adding a rule to it. That is deliberate: a Flask blueprint is mutable and
    module-level, so bolting a rule onto `rentals_bp` from inside `create_app()`
    makes the URL map depend on global state -- and fails outright the second
    time `create_app()` runs in one process, because the blueprint has already
    been registered. Building it fresh keeps `create_app()` repeatable, which
    the test suite depends on.

    The `url_prefix` carries the `/rentals` segment because the endpoint name is
    distinct from the main rentals blueprint; Flask only allows one blueprint per
    name per app.
    """
    blueprint = Blueprint(UPLOAD_PROXY_BLUEPRINT, __name__, url_prefix="/rentals")
    blueprint.add_url_rule(
        "/apartments/<uuid:apartment_id>/upload-proxy",
        endpoint="upload_proxy",
        view_func=upload_proxy,
        methods=["POST"],
    )
    return blueprint


__all__ = [
    "FILE_FIELD",
    "UPLOAD_PROXY_BLUEPRINT",
    "build_upload_proxy_blueprint",
    "upload_proxy",
]
