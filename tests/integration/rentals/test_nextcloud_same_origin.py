"""Same-origin Nextcloud upload path.

Regression tests for the move away from sending Nextcloud credentials to the
browser. The previous design PUT cross-origin with an `Authorization` header
built from an App Password in the page source, which exposed the credential to
every signed-in user and relied on a CORS preflight that the browser cannot
authenticate.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from src.rentals.interfaces.web.routes import NextcloudUploadConfig
from tests.integration.rentals.conftest import APARTMENT_ID
from tests.integration.rentals.page_html import main_content
from tests.integration.rentals.test_rentals_routes import _csrf_from, _stub_hub

UPLOAD_PATH = "/nextcloud-dav/rentals"
ORIGIN = "http://localhost"

TEMPLATE = Path("src/rentals/interfaces/web/templates/rentals/apartment_detail.html")


class TestConfigObject:
    def test_configured_with_a_path(self):
        config = NextcloudUploadConfig(UPLOAD_PATH)
        assert config.configured
        assert config.missing == ()

    def test_missing_path_is_unconfigured(self):
        config = NextcloudUploadConfig("")
        assert not config.configured
        assert config.missing == ("NEXTCLOUD_UPLOAD_PATH",)

    def test_whitespace_counts_as_missing(self):
        assert not NextcloudUploadConfig("   ").configured

    def test_message_names_the_variable(self):
        message = NextcloudUploadConfig("").unconfigured_message
        assert "NEXTCLOUD_UPLOAD_PATH" in message
        assert "Please check" in message

    def test_js_config_is_absolute_and_same_origin(self):
        payload = NextcloudUploadConfig(UPLOAD_PATH).to_js_config(ORIGIN + "/")
        assert payload["uploadUrl"] == f"{ORIGIN}{UPLOAD_PATH}"
        assert payload["configured"] is True

    def test_js_config_normalises_slashes(self):
        """A doubled separator would 404 at the proxy."""
        for raw in (
            "nextcloud-dav/rentals",
            "/nextcloud-dav/rentals/",
            "//nextcloud-dav/rentals//",
        ):
            payload = NextcloudUploadConfig(raw).to_js_config(ORIGIN)
            assert payload["uploadUrl"] == f"{ORIGIN}/nextcloud-dav/rentals"

    def test_js_config_carries_no_credentials(self):
        """The JS config is an exhaustive allow-list, not a spot check.

        The exact key set is asserted so a credential cannot be added to it
        later without this test noticing. `viaBff` and `bffUploadPath` are the
        local-dev fallback's switches: a path on this app, and a boolean. Note
        that the fallback still sends no credential to the browser -- Flask holds
        it -- so these two keys stay safe to expose.
        """
        payload = NextcloudUploadConfig(UPLOAD_PATH).to_js_config(ORIGIN)
        assert set(payload) == {
            "uploadUrl",
            "configured",
            "missing",
            "viaBff",
            "bffUploadPath",
        }
        assert "username" not in payload
        assert "password" not in payload
        # Off by default, and the fallback path empty when not in use.
        assert payload["viaBff"] is False
        assert payload["bffUploadPath"] == ""

    def test_js_config_never_carries_the_credential(self):
        """Neither upload mode may put the App Password in the page.

        Guards the local-dev fallback specifically: it moves the upload into
        Flask, which is exactly the kind of change that could tempt someone to
        hand the browser the credential the proxy used to inject.
        """
        payload = NextcloudUploadConfig(UPLOAD_PATH, via_bff=True).to_js_config(
            ORIGIN, bff_upload_path=f"{ORIGIN}rentals/upload-proxy"
        )
        serialised = repr(payload).lower()
        for forbidden in ("basic", "authorization", "app-password", "password"):
            assert forbidden not in serialised


class TestAppHasNoCredentials:
    def test_only_the_upload_path_is_configured(self):
        from src.interfaces.web.app import create_app

        app = create_app()
        assert "NEXTCLOUD_UPLOAD_PATH" in app.config
        # The credential must not live in the application at all.
        for key in (
            "NEXTCLOUD_USERNAME",
            "NEXTCLOUD_PASSWORD",
            "NEXTCLOUD_WEBDAV_BASE_URL",
            "NEXTCLOUD_SHARE_TOKEN",
        ):
            assert key not in app.config

    def test_env_example_ships_no_credential(self):
        text = Path(".env.example").read_text()
        assert "NEXTCLOUD_UPLOAD_PATH" in text
        for line in text.splitlines():
            if line.startswith(("NEXTCLOUD_USERNAME=", "NEXTCLOUD_PASSWORD=")):
                raise AssertionError(f"credential setting present: {line}")

    def test_env_example_documents_the_proxy(self):
        text = Path(".env.example").read_text()
        assert "handle_path /nextcloud-dav" in text
        assert "header_up Authorization" in text
        assert "reverse_proxy" in text


def _hub_html(respx_mock, authed_client) -> str:
    """Render the hub with the DRF boundary stubbed."""
    _stub_hub(respx_mock)
    return authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
        as_text=True
    )


def _hub_js(respx_mock, authed_client) -> str:
    """The hub's rendered JavaScript, with comments stripped.

    Comments in the template *explain* that no credential is sent, so they
    necessarily contain the word "Authorization". Scanning raw HTML would fail on
    the very prose documenting the guarantee, which is why this exists.
    """
    html = _hub_html(respx_mock, authed_client)
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    return re.sub(r"//[^\n]*", "", "\n".join(scripts))


class TestTemplateSendsNoCredentials:
    def test_no_authorization_header(self, respx_mock, authed_client):
        """The core property: nothing secret leaves the application.

        Scoped to the ``headers`` object of the fetch call rather than the whole
        script, because the diagnostic *message* legitimately tells an operator
        to check the proxy's Authorization header -- the word appears in
        user-facing text that is not a credential.
        """
        js = _hub_js(respx_mock, authed_client)
        header_blocks = re.findall(r"headers:\s*\{([^}]*)\}", js)
        assert header_blocks, "no fetch headers block found to inspect"
        for block in header_blocks:
            assert "Authorization" not in block
        assert "btoa" not in js

    def test_target_is_the_same_origin_path(self, respx_mock, authed_client):
        js = _hub_js(respx_mock, authed_client)
        assert "nextcloud.uploadUrl.replace" in js
        assert "encodeURIComponent(file.name)" in js
        assert f"{ORIGIN}{UPLOAD_PATH}" in _hub_html(respx_mock, authed_client)

    def test_configured_depends_only_on_the_url(self, respx_mock, authed_client):
        js = _hub_js(respx_mock, authed_client)
        assert "nextcloudConfigured: Boolean(nextcloud.uploadUrl)" in js

    def test_credential_keys_are_absent_from_the_template(self):
        source = TEMPLATE.read_text()
        for key in (
            "NEXTCLOUD_USERNAME",
            "NEXTCLOUD_PASSWORD",
            "NEXTCLOUD_WEBDAV_BASE_URL",
            "shareToken",
            "nextcloud.username",
            "nextcloud.password",
        ):
            assert key not in source

    def test_single_x_data_root(self, respx_mock, authed_client):
        """AGENTS.md: Alpine ignores directives outside an x-data subtree.

        Scoped to <main> on purpose. The nav's account dropdown is an
        independent x-data zone in base.html, which AGENTS.md explicitly
        allows; counting the whole document would conflate the two.
        """
        html = _hub_html(respx_mock, authed_client)
        assert main_content(html).count("x-data=") == 1


class TestDiagnostics:
    def test_401_points_at_the_proxy_credential(self, respx_mock, authed_client):
        """The proxy forwards the status verbatim, so it is Nextcloud's answer."""
        js = _hub_js(respx_mock, authed_client)
        assert "response.status === 401" in js
        assert "App Password" in js

    def test_network_failure_names_the_proxy(self, respx_mock, authed_client):
        js = _hub_js(respx_mock, authed_client)
        assert "diagnoseUploadFailure" in js
        assert "storage proxy" in js

    def test_diagnostic_lives_inside_the_component(self):
        source = TEMPLATE.read_text()
        start = source.index("Alpine.data('rentalsHub'")
        assert source.index("diagnoseUploadFailure(error) {") > start

    def test_file_url_not_armed_on_failure(self, respx_mock, authed_client):
        js = _hub_js(respx_mock, authed_client)
        assert "this.fileUrl = ''" in js


class TestRegisterDocumentGuards:
    def test_unconfigured_refuses_registration(self, respx_mock, authed_client):
        """A crafted POST must not register a URL that was never uploaded."""
        authed_client.application.config["NEXTCLOUD_UPLOAD_PATH"] = ""
        _stub_hub(respx_mock)
        route = respx_mock.post(
            f"/api/v1/rentals/apartments/{APARTMENT_ID}/documents/"
        ).mock(return_value=httpx.Response(201, json={"id": "x"}))
        page = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}")
        response = authed_client.post(
            f"/rentals/apartments/{APARTMENT_ID}",
            data={
                "csrf_token": _csrf_from(page.get_data(as_text=True)),
                "action": "register_document",
                "document_type": "LEASE_CONTRACT",
                "file_url": f"{ORIGIN}{UPLOAD_PATH}/lease.pdf",
                "description": "",
            },
        )
        assert response.status_code == 302
        assert not route.called

    def test_configured_registers(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(
            f"/api/v1/rentals/apartments/{APARTMENT_ID}/documents/"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "33333333-3333-4333-8333-333333333333",
                    "apartment_id": str(APARTMENT_ID),
                    "document_type": "LEASE_CONTRACT",
                    "file_url": f"{ORIGIN}{UPLOAD_PATH}/lease.pdf",
                    "description": None,
                    "uploaded_at": "2026-10-01T12:00:00Z",
                },
            )
        )
        page = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}")
        response = authed_client.post(
            f"/rentals/apartments/{APARTMENT_ID}",
            data={
                "csrf_token": _csrf_from(page.get_data(as_text=True)),
                "action": "register_document",
                "document_type": "LEASE_CONTRACT",
                "file_url": f"{ORIGIN}{UPLOAD_PATH}/lease.pdf",
                "description": "",
            },
        )
        assert response.status_code == 302
        assert route.called
