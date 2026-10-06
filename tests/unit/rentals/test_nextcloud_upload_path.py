"""`NEXTCLOUD_UPLOAD_PATH` parsing.

Regression tests for a real bug: the setting was documented as a path but
operators reasonably wrote an absolute URL, and the code prefixed the app origin
onto it unconditionally. That produced

    http://127.0.0.1:5000/https://drive.ghostlabhomecenter.work/nextcloud-dav/rentals/lease.pdf

which is a well-formed absolute URL pointing nowhere, and which passed the
domain's own absolute-URL validation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.rentals.domain.exceptions import InvalidDocumentError, InvalidRentalsInputError
from src.rentals.domain.value_objects import validate_document_url
from src.rentals.interfaces.web.routes import NextcloudUploadConfig

APP_ORIGIN = "http://127.0.0.1:5000/"
PUBLIC_ORIGIN = "https://budget.example/"


def _url(path: str, origin: str = APP_ORIGIN) -> str:
    return NextcloudUploadConfig(path).to_js_config(origin)["uploadUrl"]


class TestPathForm:
    def test_absolute_path(self):
        assert _url("/nextcloud-dav/rentals") == (f"{APP_ORIGIN}nextcloud-dav/rentals")

    def test_path_without_leading_slash(self):
        assert _url("nextcloud-dav/rentals") == (f"{APP_ORIGIN}nextcloud-dav/rentals")

    def test_trailing_slash_is_removed(self):
        assert _url("/nextcloud-dav/rentals/") == (f"{APP_ORIGIN}nextcloud-dav/rentals")

    def test_repeated_slashes_are_normalised(self):
        for raw in ("//nextcloud-dav/rentals", "/nextcloud-dav//rentals//"):
            assert _url(raw) == f"{APP_ORIGIN}nextcloud-dav/rentals"

    def test_trailing_slash_on_origin_is_not_doubled(self):
        assert _url("/x", "https://budget.example") == "https://budget.example/x"


class TestAbsoluteUrlForm:
    def test_same_origin_absolute_url_is_accepted(self):
        raw = "https://budget.example/nextcloud-dav/rentals"
        assert _url(raw, PUBLIC_ORIGIN) == raw

    def test_same_origin_trailing_slash_trimmed(self):
        raw = "https://budget.example/nextcloud-dav/rentals/"
        assert (
            _url(raw, PUBLIC_ORIGIN) == "https://budget.example/nextcloud-dav/rentals"
        )

    def test_same_origin_beats_the_host_port_default(self):
        """With a real deployment the origin is not the dev default."""
        origin = "https://budget.example:8443/"
        raw = "https://budget.example:8443/nextcloud-dav/rentals"
        assert _url(raw, origin) == raw

    def test_cross_origin_is_rejected(self):
        """The exact value from the bug report."""
        raw = "https://drive.ghostlabhomecenter.work/nextcloud-dav/rentals"
        with pytest.raises(InvalidRentalsInputError) as excinfo:
            _url(raw)
        message = str(excinfo.value)
        assert "NEXTCLOUD_UPLOAD_PATH" in message
        assert "drive.ghostlabhomecenter.work" in message
        # The operator is told why, and what the expected origin is.
        assert "same origin" in message
        assert "127.0.0.1:5000" in message

    def test_cross_origin_explains_the_consequence(self):
        with pytest.raises(InvalidRentalsInputError, match="preflight"):
            _url("https://cloud.example/dav/rentals")


class TestEmptyValue:
    def test_empty_is_unconfigured_not_an_error(self):
        payload = NextcloudUploadConfig("").to_js_config(APP_ORIGIN)
        assert payload == {
            "uploadUrl": "",
            "configured": False,
            "missing": ["NEXTCLOUD_UPLOAD_PATH"],
            "viaBff": False,
            "bffUploadPath": "",
        }

    def test_whitespace_is_unconfigured(self):
        payload = NextcloudUploadConfig("   ").to_js_config(APP_ORIGIN)
        assert payload["configured"] is False


class TestDomainRejectsJoinedUrls:
    """The generated value must not reach the database.

    `http://app/https://cloud/dav/lease.pdf` is a syntactically valid absolute
    URL -- the second `://` simply sits in the path -- so the domain's
    absolute-URL checks alone let it through.
    """

    def test_joined_urls_are_rejected(self):
        with pytest.raises(InvalidDocumentError, match="two URLs"):
            validate_document_url(
                "http://127.0.0.1:5000/https://drive.example/nextcloud/lease.pdf"
            )

    def test_a_single_valid_url_is_accepted(self):
        assert (
            validate_document_url(
                "https://budget.example/nextcloud-dav/rentals/lease.pdf"
            )
            == "https://budget.example/nextcloud-dav/rentals/lease.pdf"
        )

    def test_bare_path_still_rejected(self):
        with pytest.raises(InvalidDocumentError, match="absolute URL"):
            validate_document_url("lease.pdf")

    def test_percent_encoded_name_is_accepted(self):
        assert validate_document_url(
            "https://budget.example/nextcloud-dav/rentals/carta%20de%20recomend.pdf"
        )


class TestEndToEndUrlConstruction:
    def test_filename_is_appended_to_the_configured_base(self):
        base = _url("/nextcloud-dav/rentals")
        target = f"{base}/carta%20de%20recomendacion.pdf"
        # Whatever the browser PUTs must be a usable absolute URL.
        assert validate_document_url(target) == target

    def test_path_form_survives_the_round_trip(self):
        for raw in ("/nextcloud-dav/rentals", "nextcloud-dav/rentals"):
            base = _url(raw)
            assert validate_document_url(f"{base}/lease.pdf")

    def test_decimal_is_unaffected(self):
        """Sanity check that the import graph is intact after the edit."""
        assert Decimal("1.50") + Decimal("2.25") == Decimal("3.75")
