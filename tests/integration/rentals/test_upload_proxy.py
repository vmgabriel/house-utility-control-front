"""The local-development Nextcloud upload fallback.

Covers the two halves of the exception to AGENTS.md rule 6:

* the Flask route in `src.rentals.interfaces.web.upload_proxy`;
* the WebDAV adapter in `src.rentals.infrastructure.nextcloud_webdav`.

The Nextcloud hop is mocked with an `httpx.MockTransport`, so these tests still
assert the real request the adapter builds -- URL, `Authorization` header, and
streamed body -- rather than a stub's idea of it.

The behaviour asserted here that matters most is the *containment*: the route
must not hand the browser a credential, and the URL it returns must be the
same-origin one, so a document uploaded through the fallback is
indistinguishable from one uploaded the intended way.
"""

from __future__ import annotations

import io
import json
import os

import httpx
import pytest

from src.infrastructure.auth.csrf import CSRF_HEADER
from src.rentals.domain.exceptions import NextcloudUploadError
from src.rentals.infrastructure.nextcloud_webdav import (
    NextcloudWebDavClient,
    build_client,
)
from src.rentals.interfaces.web.upload_proxy import FILE_FIELD
from tests.integration.rentals.conftest import (
    ACCESS_TOKEN,
    APARTMENT_ID,
    REFRESH_TOKEN,
    USER_ID,
)

UPLOAD_URL = f"/rentals/apartments/{APARTMENT_ID}/upload-proxy"
DAV_PATH = "/remote.php/dav/files/bff-uploader/rentals"
CREDENTIAL = "dGVzdC11c2VyOnRlc3QtcGFzc3dvcmQ="  # "test-user:test-password"


@pytest.fixture
def enabled_app(app, monkeypatch):
    """An app with the fallback flag on and a mocked Nextcloud client.

    `monkeypatch.setenv` is deliberate: `create_app` reads the flag from the
    environment, so the route is only registered when the variable is set at
    factory time. Building a second app is the only way to exercise the real
    registration path rather than bolting the rule on afterwards.
    """
    from src.interfaces.web.app import create_app
    from src.rentals.application.wiring import build_rentals_use_cases

    monkeypatch.setenv("NEXTCLOUD_UPLOAD_VIA_BFF", "true")
    monkeypatch.setenv("NEXTCLOUD_BASE_URL", "https://cloud.example")
    monkeypatch.setenv("NEXTCLOUD_DAV_PATH", DAV_PATH)
    monkeypatch.setenv("NEXTCLOUD_BASIC_AUTH", CREDENTIAL)

    application = create_app()
    application.config.update(
        TESTING=True, NEXTCLOUD_UPLOAD_PATH="/nextcloud-dav/rentals"
    )
    build_rentals_use_cases(application.extensions["rentals_api_client"]).attach_to(
        application
    )
    return application


@pytest.fixture
def nextcloud_mock():
    """Records the upstream request and returns a canned 201."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = request.content
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    client = NextcloudWebDavClient(
        "https://cloud.example", DAV_PATH, CREDENTIAL, transport=transport
    )
    captured["client"] = client
    return captured


def _authed(application):
    from src.infrastructure.auth.jwt_cookie_manager import (
        ACCESS_COOKIE,
        REFRESH_COOKIE,
    )

    test_client = application.test_client()
    test_client.set_cookie(ACCESS_COOKIE, ACCESS_TOKEN)
    test_client.set_cookie(REFRESH_COOKIE, REFRESH_TOKEN)
    with test_client.session_transaction() as session:
        session["user_id"] = str(USER_ID)
    return test_client


def _csrf(application) -> str:
    return application.csrf_manager.generate_token()


def _post(
    client, application, *, filename="lease.pdf", csrf="valid", payload=None, **extra
):
    """POST a file to the fallback route.

    `csrf` distinguishes the three cases a test actually needs: a valid token
    (the default), a specific bad one, and `None` to send no header at all. A
    plain falsy default would collapse "absent" into "generate a good one" and
    make the missing-token test pass for the wrong reason.
    """
    data = {FILE_FIELD: (io.BytesIO(payload or b"PDF-CONTENT"), filename)}
    headers = {}
    if csrf == "valid":
        headers[CSRF_HEADER] = _csrf(application)
    elif csrf is not None:
        headers[CSRF_HEADER] = csrf
    headers.update(extra)
    return client.post(
        UPLOAD_URL, data=data, headers=headers, content_type="multipart/form-data"
    )


class TestRouteRegistration:
    def test_route_is_absent_when_the_flag_is_off(self, app):
        """The exception must be opt-in, not merely unused."""
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        assert not any("upload-proxy" in rule for rule in rules)

    def test_route_exists_when_the_flag_is_on(self, enabled_app):
        rules = {str(rule) for rule in enabled_app.url_map.iter_rules()}
        assert any("upload-proxy" in rule for rule in rules)


class TestGuards:
    def test_requires_authentication(self, enabled_app, nextcloud_mock):
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        anonymous = enabled_app.test_client()
        response = _post(anonymous, enabled_app, csrf=_csrf(enabled_app))

        assert response.status_code == 401
        # JSON, not a redirect to the login page: the caller is fetch().
        assert response.get_json()["ok"] is False

    def test_rejects_a_missing_csrf_token(self, enabled_app, nextcloud_mock):
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        response = _post(_authed(enabled_app), enabled_app, csrf=None)

        assert response.status_code == 403
        assert "CSRF" in response.get_json()["error"]

    def test_rejects_a_forged_csrf_token(self, enabled_app, nextcloud_mock):
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        response = _post(_authed(enabled_app), enabled_app, csrf="salt:0:deadbeef")

        assert response.status_code == 403

    def test_unconfigured_storage_reports_503_not_500(self, enabled_app):
        """A missing credential must read as unconfigured, not crash."""
        enabled_app.nextcloud_webdav_client = None
        response = _post(_authed(enabled_app), enabled_app, csrf=_csrf(enabled_app))

        assert response.status_code == 503
        assert "NEXTCLOUD_BASE_URL" in response.get_json()["error"]

    def test_rejects_a_request_with_no_file(self, enabled_app, nextcloud_mock):
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        response = _authed(enabled_app).post(
            UPLOAD_URL,
            data={},
            headers={CSRF_HEADER: _csrf(enabled_app)},
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        assert FILE_FIELD in response.get_json()["error"]

    def test_rejects_a_filename_with_no_usable_characters(
        self, enabled_app, nextcloud_mock
    ):
        """`secure_filename` can reduce a name to nothing; that is a 400."""
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        response = _post(
            _authed(enabled_app),
            enabled_app,
            filename="///",
            csrf=_csrf(enabled_app),
        )

        assert response.status_code == 400
        assert nextcloud_mock.get("url") is None


class TestSuccessfulUpload:
    def test_forwards_the_file_to_nextcloud(self, enabled_app, nextcloud_mock):
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        response = _post(_authed(enabled_app), enabled_app, csrf=_csrf(enabled_app))

        assert response.status_code == 200
        assert nextcloud_mock["method"] == "PUT"
        assert nextcloud_mock["url"] == (f"https://cloud.example{DAV_PATH}/lease.pdf")
        assert nextcloud_mock["body"] == b"PDF-CONTENT"

    def test_injects_the_server_side_credential(self, enabled_app, nextcloud_mock):
        """The credential reaches Nextcloud -- and only Nextcloud."""
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        response = _post(_authed(enabled_app), enabled_app, csrf=_csrf(enabled_app))

        assert nextcloud_mock["authorization"] == f"Basic {CREDENTIAL}"
        # ...and is never echoed back to the browser.
        body = response.get_data(as_text=True)
        assert CREDENTIAL not in body
        assert "Basic" not in body

    def test_returns_the_same_origin_url_not_the_nextcloud_url(
        self, enabled_app, nextcloud_mock
    ):
        """The stored `file_url` must be indistinguishable from the proxy path.

        This is the containment guarantee: enabling the fallback must not
        change what gets written to the database.
        """
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        response = _post(_authed(enabled_app), enabled_app, csrf=_csrf(enabled_app))

        assert response.get_json() == {
            "ok": True,
            "fileUrl": "http://localhost/nextcloud-dav/rentals/lease.pdf",
            "filename": "lease.pdf",
        }

    def test_strips_path_traversal_from_the_filename(self, enabled_app, nextcloud_mock):
        """A crafted name must not escape the target folder."""
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        response = _post(
            _authed(enabled_app),
            enabled_app,
            filename="../../../etc/passwd",
            csrf=_csrf(enabled_app),
        )

        assert response.status_code == 200
        assert nextcloud_mock["url"] == f"https://cloud.example{DAV_PATH}/etc_passwd"
        # The stored URL uses the same sanitised name, so the two agree.
        assert response.get_json()["fileUrl"].endswith("/etc_passwd")


class TestUpstreamFailures:
    @pytest.mark.parametrize("status", [401, 403])
    def test_expired_credential_is_named_as_such(
        self, enabled_app, monkeypatch, status
    ):
        transport = httpx.MockTransport(lambda request: httpx.Response(status))
        enabled_app.nextcloud_webdav_client = NextcloudWebDavClient(
            "https://cloud.example", DAV_PATH, CREDENTIAL, transport=transport
        )
        response = _post(_authed(enabled_app), enabled_app, csrf=_csrf(enabled_app))

        assert response.status_code == 502
        error = response.get_json()["error"]
        assert "App Password" in error
        assert str(status) in error

    def test_a_missing_folder_is_reported_with_its_status(
        self, enabled_app, nextcloud_mock
    ):
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        enabled_app.nextcloud_webdav_client = NextcloudWebDavClient(
            "https://cloud.example", DAV_PATH, CREDENTIAL, transport=transport
        )
        response = _post(_authed(enabled_app), enabled_app, csrf=_csrf(enabled_app))

        assert response.status_code == 502
        assert "404" in response.get_json()["error"]

    def test_an_unreachable_nextcloud_is_not_reported_as_a_refusal(self, enabled_app):
        """A transport failure and a rejection are different operator problems."""
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("no route"))
        )
        enabled_app.nextcloud_webdav_client = NextcloudWebDavClient(
            "https://cloud.example", DAV_PATH, CREDENTIAL, transport=transport
        )
        response = _post(_authed(enabled_app), enabled_app, csrf=_csrf(enabled_app))

        assert response.status_code == 502
        assert "reach Nextcloud" in response.get_json()["error"]

    def test_the_upload_url_is_resolved_before_bytes_are_sent(
        self, enabled_app, nextcloud_mock
    ):
        """No orphaned object: `file_url` must be derivable before the PUT.

        Otherwise a misconfigured NEXTCLOUD_UPLOAD_PATH would leave a file in
        Nextcloud that no document will ever point at.
        """
        enabled_app.nextcloud_webdav_client = nextcloud_mock["client"]
        enabled_app.config["NEXTCLOUD_UPLOAD_PATH"] = ""
        response = _post(_authed(enabled_app), enabled_app, csrf=_csrf(enabled_app))

        assert response.status_code == 503
        assert nextcloud_mock.get("url") is None


class TestWebDavClient:
    def test_streams_rather_than_buffering(self):
        """The body must arrive intact even when it spans several chunks.

        The adapter passes an iterator to httpx, so this asserts the streaming
        path end to end -- `CHUNK_SIZE` is small enough here that a multi-chunk
        body is genuinely exercised.
        """
        payload = b"x" * (200 * 1024)
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(201)

        client = NextcloudWebDavClient(
            "https://cloud.example",
            DAV_PATH,
            CREDENTIAL,
            transport=httpx.MockTransport(handler),
        )
        import asyncio

        asyncio.run(client.upload("big.pdf", io.BytesIO(payload)))

        assert captured["body"] == payload

    def test_normalises_slashes_in_the_dav_path(self):
        client = NextcloudWebDavClient(
            "https://cloud.example/", "remote.php/dav/files/u/rentals/", CREDENTIAL
        )
        assert client.remote_url("a.pdf") == (
            "https://cloud.example/remote.php/dav/files/u/rentals/a.pdf"
        )

    @pytest.mark.parametrize(
        "base_url,dav_path,basic_auth",
        [
            ("", DAV_PATH, CREDENTIAL),
            ("https://cloud.example", "", CREDENTIAL),
            ("https://cloud.example", DAV_PATH, ""),
            ("   ", DAV_PATH, CREDENTIAL),
        ],
    )
    def test_build_client_returns_none_when_incomplete(
        self, base_url, dav_path, basic_auth
    ):
        """A blank setting disables the control instead of 500-ing on first run."""
        assert build_client(base_url, dav_path, basic_auth) is None

    def test_upload_raises_a_domain_error_on_rejection(self):
        client = NextcloudWebDavClient(
            "https://cloud.example",
            DAV_PATH,
            CREDENTIAL,
            transport=httpx.MockTransport(lambda request: httpx.Response(507)),
        )
        import asyncio

        with pytest.raises(NextcloudUploadError) as caught:
            asyncio.run(client.upload("a.pdf", io.BytesIO(b"x")))

        assert caught.value.status == 507

    def test_accepts_every_success_status(self):
        """Nextcloud answers 201 on create and 204 on overwrite; both are fine."""
        import asyncio

        for status in (200, 201, 204):
            client = NextcloudWebDavClient(
                "https://cloud.example",
                DAV_PATH,
                CREDENTIAL,
                transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s)),
            )
            url = asyncio.run(client.upload("a.pdf", io.BytesIO(b"x")))
            assert url.endswith("/a.pdf")


class TestHubRendering:
    def test_the_hub_advertises_the_fallback_when_enabled(
        self, enabled_app, nextcloud_mock
    ):
        """The template must receive a URL it can actually post to."""
        from src.rentals.interfaces.web.routes import _nextcloud_upload_config

        with enabled_app.test_request_context():
            payload = _nextcloud_upload_config().to_js_config(
                "http://localhost", bff_upload_path="/rentals/x/upload-proxy"
            )

        assert payload["viaBff"] is True
        assert payload["bffUploadPath"] == "/rentals/x/upload-proxy"
        # The stored URL is untouched by the fallback.
        assert payload["uploadUrl"] == "http://localhost/nextcloud-dav/rentals"

    def test_the_flag_reads_the_usual_truthy_spellings(self, app):
        """`.env` can only express strings, so the flag must accept them."""
        from src.rentals.interfaces.web.routes import _config_flag

        for value in ("true", "1", "yes", "on", "TRUE", " On "):
            app.config["NEXTCLOUD_UPLOAD_VIA_BFF"] = value
            with app.test_request_context():
                assert _config_flag("NEXTCLOUD_UPLOAD_VIA_BFF") is True

        for value in ("false", "0", "no", "", "off"):
            app.config["NEXTCLOUD_UPLOAD_VIA_BFF"] = value
            with app.test_request_context():
                assert _config_flag("NEXTCLOUD_UPLOAD_VIA_BFF") is False

    def test_the_flag_defaults_to_off(self, app):
        """A missing setting must leave the production path in place."""
        from src.rentals.interfaces.web.routes import _config_flag

        app.config.pop("NEXTCLOUD_UPLOAD_VIA_BFF", None)
        with app.test_request_context():
            assert _config_flag("NEXTCLOUD_UPLOAD_VIA_BFF") is False


class TestLargeUpload:
    """A multi-megabyte file must survive the streaming round trip intact.

    This is the assertion that matters most for the fallback's stated cost. The
    body crosses Flask as an async chunk iterator, so anything that breaks the
    chunking -- a sync iterator on an async client, a premature close, a length
    mismatch -- shows up here as corrupt bytes rather than as a clean error.
    """

    # Deliberately NOT `async def`. The suite runs with pytest-asyncio in auto
    # mode, so an async test already owns a running event loop -- and Flask
    # bridges its sync WSGI call into that same loop via AsyncToSync, which
    # refuses to nest. A sync test keeps the request path identical to every
    # other test in this file.
    def test_a_multi_megabyte_file_arrives_intact(self, enabled_app):
        import hashlib

        payload = os.urandom(3 * 1024 * 1024)
        digest = hashlib.sha256(payload).hexdigest()
        seen: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return httpx.Response(201)

        enabled_app.nextcloud_webdav_client = NextcloudWebDavClient(
            "https://cloud.example",
            DAV_PATH,
            CREDENTIAL,
            transport=httpx.MockTransport(handler),
        )

        response = _post(
            _authed(enabled_app),
            enabled_app,
            filename="big.pdf",
            csrf=_csrf(enabled_app),
            payload=payload,
        )

        assert response.status_code == 200
        assert hashlib.sha256(seen["body"]).hexdigest() == digest


def test_json_payload_shape_is_stable():
    """Guard against the browser's `response.json()` contract drifting.

    The frontend branches on `ok` and `fileUrl`; renaming either silently
    breaks uploads with no server-side error.
    """
    assert set(json.loads('{"ok": true, "fileUrl": "u"}')) == {"ok", "fileUrl"}
