"""Nextcloud upload diagnostics.

Regression tests for the cross-origin upload failure mode. The browser hides the
reason a `fetch` was rejected, so the component classifies the error itself and
names the actual cause -- otherwise an operator is told to "check the server
configuration" when the real gap is a missing CORS header on the reverse proxy.
"""

from __future__ import annotations

from pathlib import Path

from src.rentals.interfaces.web.routes import NextcloudUploadConfig
from tests.integration.rentals.conftest import APARTMENT_ID
from tests.integration.rentals.test_rentals_routes import _stub_hub

TEMPLATE = Path("src/rentals/interfaces/web/templates/rentals/apartment_detail.html")


def _hub_html(respx_mock, authed_client) -> str:
    _stub_hub(respx_mock)
    return authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
        as_text=True
    )


class TestAuthorizationHeaderIsRetained:
    """The header is the only working authentication; it must stay."""

    def test_header_is_still_sent(self, respx_mock, authed_client):
        html = _hub_html(respx_mock, authed_client)
        assert "Authorization: `Basic ${credentials}`" in html
        assert "btoa(`${nextcloud.shareToken}:`)" in html

    def test_no_credential_free_fetch_variant_exists(self):
        """Guards against a well-meaning 'fix' that drops the header again."""
        source = TEMPLATE.read_text()
        # Any second fetch() in the template would be a credential-free variant.
        assert source.count("await fetch(") == 1


class TestErrorClassification:
    def test_type_error_is_reported_as_cors(self, respx_mock, authed_client):
        """A rejected promise means CORS, not a dead network."""
        html = _hub_html(respx_mock, authed_client)
        assert "diagnoseUploadFailure" in html
        assert "error instanceof TypeError" in html

    def test_message_names_the_header_and_the_path(self, respx_mock, authed_client):
        html = _hub_html(respx_mock, authed_client)
        assert "Access-Control-Allow-Origin" in html
        assert "/public.php/webdav" in html

    def test_message_includes_the_configured_host(self, respx_mock, authed_client):
        """An operator needs to know WHICH Nextcloud to fix."""
        html = _hub_html(respx_mock, authed_client)
        assert "new URL(nextcloud.webdavBaseUrl).host" in html

    def test_401_gets_its_own_message(self, respx_mock, authed_client):
        """A reachable 401 means a rotated token, not CORS."""
        html = _hub_html(respx_mock, authed_client)
        assert "response.status === 401" in html
        assert "NEXTCLOUD_SHARE_TOKEN" in html

    def test_other_statuses_report_the_code(self, respx_mock, authed_client):
        html = _hub_html(respx_mock, authed_client)
        assert "HTTP ${response.status}" in html

    def test_upload_error_state_is_set_on_failure(self, respx_mock, authed_client):
        html = _hub_html(respx_mock, authed_client)
        assert "this.uploadError = true" in html

    def test_file_url_is_not_armed_when_upload_fails(self, respx_mock, authed_client):
        """A failed PUT must never leave a URL the form can submit."""
        html = _hub_html(respx_mock, authed_client)
        assert "this.fileUrl = ''" in html
        assert "this.uploadedUrl = ''" in html

    def test_uploading_flag_is_always_cleared(self, respx_mock, authed_client):
        html = _hub_html(respx_mock, authed_client)
        assert "this.uploading = false" in html


class TestNoAccountCredentials:
    def test_env_example_documents_only_the_drop_folder_vars(self):
        text = Path(".env.example").read_text()
        assert "NEXTCLOUD_WEBDAV_BASE_URL" in text
        assert "NEXTCLOUD_SHARE_TOKEN" in text
        # The credentials must stay out, with the reason documented.
        assert "NEXTCLOUD_USERNAME=" not in text
        assert "NEXTCLOUD_PASSWORD=" not in text
        assert "deliberately NO NEXTCLOUD_USERNAME" in text

    def test_template_has_no_credential_keys(self):
        source = TEMPLATE.read_text()
        assert "NEXTCLOUD_USERNAME" not in source
        assert "NEXTCLOUD_PASSWORD" not in source

    def test_app_config_has_no_credential_keys(self):
        from src.interfaces.web.app import create_app

        app = create_app()
        assert "NEXTCLOUD_USERNAME" not in app.config
        assert "NEXTCLOUD_PASSWORD" not in app.config


class TestAlpineScopeIntact:
    def test_single_x_data_root(self, respx_mock, authed_client):
        html = _hub_html(respx_mock, authed_client)
        assert html.count("x-data=") == 1
        assert 'x-data="rentalsHub()"' in html

    def test_diagnostic_lives_inside_the_component(self):
        """A helper defined outside the x-data scope would not be reachable."""
        source = TEMPLATE.read_text()
        component_start = source.index("Alpine.data('rentalsHub'")
        assert source.index("diagnoseUploadFailure(") > component_start
        assert source.index("diagnoseUploadFailure(error) {") > component_start


class TestConfigValidation:
    def test_both_vars_required(self):
        config = NextcloudUploadConfig("", "")
        assert not config.configured
        assert config.missing == (
            "NEXTCLOUD_WEBDAV_BASE_URL",
            "NEXTCLOUD_SHARE_TOKEN",
        )

    def test_only_the_two_vars_are_ever_required(self):
        """No credential checks may reappear in the message."""
        message = NextcloudUploadConfig("", "").unconfigured_message
        assert "NEXTCLOUD_USERNAME" not in message
        assert "NEXTCLOUD_PASSWORD" not in message
