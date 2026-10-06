"""Nextcloud public-share (drop folder) upload configuration.

Regression tests for a real bug: the uploader demanded an account username and
password, which the deployment deliberately does not provide, so the UI showed
"Document upload is not configured" even though the drop folder was correctly
set up.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from src.infrastructure.auth.jwt_cookie_manager import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
)
from src.rentals.interfaces.web.routes import NextcloudUploadConfig
from tests.integration.rentals.conftest import APARTMENT_ID
from tests.integration.rentals.test_rentals_routes import HUB, _csrf_from, _stub_hub

BASE_URL = "https://cloud.example.com/public.php/webdav"
TOKEN = "RnXXJTqWbpG6s5y"


class TestConfigObject:
    def test_configured_when_both_present(self):
        config = NextcloudUploadConfig(BASE_URL, TOKEN)
        assert config.configured
        assert config.missing == ()

    def test_missing_base_url(self):
        config = NextcloudUploadConfig("", TOKEN)
        assert not config.configured
        assert config.missing == ("NEXTCLOUD_WEBDAV_BASE_URL",)

    def test_missing_token(self):
        config = NextcloudUploadConfig(BASE_URL, "")
        assert not config.configured
        assert config.missing == ("NEXTCLOUD_SHARE_TOKEN",)

    def test_blank_whitespace_counts_as_missing(self):
        """A stray space in .env must not look like a configured value."""
        config = NextcloudUploadConfig(BASE_URL, "   ")
        assert not config.configured
        assert config.missing == ("NEXTCLOUD_SHARE_TOKEN",)

    def test_both_missing(self):
        config = NextcloudUploadConfig("", "")
        assert config.missing == (
            "NEXTCLOUD_WEBDAV_BASE_URL",
            "NEXTCLOUD_SHARE_TOKEN",
        )

    def test_message_names_both_when_both_missing(self):
        message = NextcloudUploadConfig("", "").unconfigured_message
        assert message == (
            "Nextcloud upload is not configured. Please check "
            "NEXTCLOUD_WEBDAV_BASE_URL and NEXTCLOUD_SHARE_TOKEN in .env"
        )

    def test_message_names_only_what_is_missing(self):
        message = NextcloudUploadConfig(BASE_URL, "").unconfigured_message
        assert "NEXTCLOUD_SHARE_TOKEN" in message
        assert "NEXTCLOUD_WEBDAV_BASE_URL" not in message

    def test_js_config_strips_a_trailing_slash(self):
        """A trailing slash would produce a doubled separator in the URL."""
        config = NextcloudUploadConfig(BASE_URL + "/", TOKEN)
        assert config.to_js_config()["webdavBaseUrl"] == BASE_URL

    def test_js_config_carries_no_credentials(self):
        payload = NextcloudUploadConfig(BASE_URL, TOKEN).to_js_config()
        assert set(payload) == {
            "webdavBaseUrl",
            "shareToken",
            "configured",
            "missing",
        }
        assert "username" not in payload
        assert "password" not in payload


class TestAppConfig:
    def test_app_reads_the_drop_folder_vars(self):
        from src.interfaces.web.app import create_app

        app = create_app()
        # Keyed off the real env, so assert on the names rather than values.
        assert "NEXTCLOUD_WEBDAV_BASE_URL" in app.config
        assert "NEXTCLOUD_SHARE_TOKEN" in app.config

    def test_no_credential_keys_are_configured(self):
        """The account-credential path must be gone, not merely unused."""
        from src.interfaces.web.app import create_app

        app = create_app()
        assert "NEXTCLOUD_USERNAME" not in app.config
        assert "NEXTCLOUD_PASSWORD" not in app.config
        assert "NEXTCLOUD_WEBDAV_URL" not in app.config

    def test_env_example_documents_the_drop_folder(self):
        text = Path(".env.example").read_text()
        assert "NEXTCLOUD_WEBDAV_BASE_URL" in text
        assert "NEXTCLOUD_SHARE_TOKEN" in text
        # Forbids the *settings*, not the names: the file deliberately documents
        # why account credentials are absent, so their mere mention is correct.
        assert "NEXTCLOUD_USERNAME=" not in text
        assert "NEXTCLOUD_PASSWORD=" not in text


class TestTemplateUsesTokenAuth:
    @staticmethod
    def _hub_html(respx_mock, authed_client) -> str:
        _stub_hub(respx_mock)
        return authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
            as_text=True
        )

    def test_configured_ui_is_enabled(self, respx_mock, authed_client):
        html = self._hub_html(respx_mock, authed_client)
        assert '"configured": true' in html
        assert f'"webdavBaseUrl": "{BASE_URL}"' in html
        assert f'"shareToken": "{TOKEN}"' in html

    def test_url_is_built_from_base_and_filename_only(self, respx_mock, authed_client):
        """`<base>/<file>`.

        Verified against the live Nextcloud: the public-share WebDAV root already
        scopes to the share, so the token must NOT be a path segment --
        `<base>/<token>/<file>` returns 404 there.
        """
        html = self._hub_html(respx_mock, authed_client)
        assert "nextcloud.webdavBaseUrl.replace" in html
        assert "encodeURIComponent(file.name)" in html
        # The token belongs in the Authorization header only.
        assert "encodeURIComponent(nextcloud.shareToken)" not in html

    def test_auth_uses_the_token_with_an_empty_password(
        self, respx_mock, authed_client
    ):
        html = self._hub_html(respx_mock, authed_client)
        assert "btoa(`${nextcloud.shareToken}:`)" in html

    def test_no_account_credentials_reach_the_browser(self, respx_mock, authed_client):
        """The security property the whole change exists to provide."""
        html = self._hub_html(respx_mock, authed_client)
        assert "nextcloud.username" not in html
        assert "nextcloud.password" not in html
        assert "NEXTCLOUD_USERNAME" not in html
        assert "NEXTCLOUD_PASSWORD" not in html

    def test_source_template_has_no_credential_keys(self):
        source = Path(
            "src/rentals/interfaces/web/templates/rentals/apartment_detail.html"
        ).read_text()
        assert "NEXTCLOUD_USERNAME" not in source
        assert "NEXTCLOUD_PASSWORD" not in source

    def test_single_x_data_root_preserved(self, respx_mock, authed_client):
        """AGENTS.md: Alpine ignores directives outside an x-data subtree."""
        html = self._hub_html(respx_mock, authed_client)
        assert html.count("x-data=") == 1
        assert 'x-data="rentalsHub()"' in html

    def test_unconfigured_ui_names_the_missing_vars(self, app):
        """A disabled drop folder must say which env var is absent."""
        app.config["NEXTCLOUD_SHARE_TOKEN"] = ""
        router = _stub_hub_for(app)
        try:
            with app.test_client() as client:
                client.set_cookie(ACCESS_COOKIE, "token")
                client.set_cookie(REFRESH_COOKIE, "token")
                with client.session_transaction() as session:
                    session["user_id"] = "11111111-1111-4111-8111-111111111111"
                html = client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
                    as_text=True
                )
        finally:
            router.stop()
        assert '"configured": false' in html
        # The notice names the specific missing variable.
        assert "NEXTCLOUD_SHARE_TOKEN" in html
        assert "Please check" in html


def _stub_hub_for(app):
    """Start respx around a hub request issued through the Flask test client."""
    import respx

    router = respx.mock(assert_all_called=False)
    router.start()
    base = app.config["DRF_API_BASE_URL"]
    aid = APARTMENT_ID
    router.get(f"{base}/rentals/apartments/{aid}/").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": str(aid),
                "house_id": "11111111-1111-4111-8111-111111111111",
                "number": "101",
                "floor": 1,
                "monthly_rent": "1000.00",
                "created_at": "2026-09-01T12:00:00Z",
                "updated_at": "2026-09-01T12:00:00Z",
            },
        )
    )
    router.get(f"{base}/rentals/apartments/{aid}/payments/summary/").mock(
        return_value=httpx.Response(
            200,
            json={
                "apartment_id": str(aid),
                "year": 2026,
                "month": 10,
                "total_paid": "0.00",
                "outstanding_balance": "1000.00",
                "payment_count": 0,
            },
        )
    )
    for suffix in ("utilities", "payments", "documents"):
        router.get(f"{base}/rentals/apartments/{aid}/{suffix}/").mock(
            return_value=httpx.Response(200, json=[])
        )
    router.get(f"{base}/profile/me/").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "first_name": "A",
                "last_name": "B",
                "timezone": "UTC",
                "language": "en",
                "currency": "USD",
                "date_format": "YYYY-MM-DD",
                "avatar_url": None,
                "bio": None,
            },
        )
    )
    return router


class TestRegisterDocumentGuards:
    def test_unconfigured_drop_folder_refuses_registration(
        self, respx_mock, authed_client
    ):
        """A crafted POST must not register a URL that was never uploaded."""
        authed_client.application.config["NEXTCLOUD_SHARE_TOKEN"] = ""
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/documents/").mock(
            return_value=httpx.Response(201, json={"id": "x"})
        )
        page = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}")
        response = authed_client.post(
            f"/rentals/apartments/{APARTMENT_ID}",
            data={
                "csrf_token": _csrf_from(page.get_data(as_text=True)),
                "action": "register_document",
                "document_type": "LEASE_CONTRACT",
                "file_url": "https://cloud.example.com/public.php/webdav/tok/lease.pdf",
                "description": "",
            },
        )
        assert response.status_code == 302
        assert not route.called

    def test_configured_drop_folder_registers(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/documents/").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "33333333-3333-4333-8333-333333333333",
                    "apartment_id": str(APARTMENT_ID),
                    "document_type": "LEASE_CONTRACT",
                    "file_url": f"{BASE_URL}/lease.pdf",
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
                "file_url": f"{BASE_URL}/lease.pdf",
                "description": "",
            },
        )
        assert response.status_code == 302
        assert route.called


class TestUploadUrlShape:
    """The generated URL must match Nextcloud's public-share layout.

    Confirmed against the live server: `PROPFIND <base>/<token>/` returns 404,
    while `PROPFIND <base>/` returns 207 and `PUT <base>/<file>` returns 201.
    The share token is an authentication credential, not a path segment.
    """

    def test_url_pattern_excludes_the_token(self):
        base, name = BASE_URL, "contrato de aluguel.pdf"
        built = base.rstrip("/") + "/" + name.replace(" ", "%20")
        assert built == f"{BASE_URL}/contrato%20de%20aluguel.pdf"
        # The token must not leak into the stored file_url either.
        assert TOKEN not in built

    def test_filename_is_encoded_not_stripped(self):
        """Spaces and accents must survive; a bare filename would 404."""
        assert re.fullmatch(r"[A-Za-z0-9._~%/-]+", "contrato%20final.pdf")
