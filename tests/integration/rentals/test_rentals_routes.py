"""Request-path tests for the rentals web layer.

Each test drives a real Flask route with only the DRF HTTP boundary mocked, so
routing, CSRF, use-case validation, adapter status handling, and the mappers are
all exercised together.

Uses respx's `respx_mock` fixture rather than the `@respx.mock` decorator: the
decorator replaces the *first* positional argument of the test, which would
shadow the `authed_client` fixture.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx

from tests.integration.rentals.conftest import (
    APARTMENT_ID,
    BASE,
    HOUSE_ID,
    TODAY,
    apartment_payload,
    document_payload,
    house_payload,
    payment_payload,
    reading_payload,
    summary_payload,
)

HUB = f"{BASE}/rentals/apartments/{APARTMENT_ID}"
CSRF_MARKER = 'name="csrf_token" value="'


def _csrf_from(html: str) -> str:
    start = html.find(CSRF_MARKER)
    if start == -1:
        return ""
    start += len(CSRF_MARKER)
    return html[start : html.find('"', start)]


def profile_payload(currency: str = "USD", language: str = "en-US") -> dict:
    """A `UserProfile` payload with a deterministic currency and locale."""
    return {
        "id": str(uuid4()),
        "first_name": "Ana",
        "last_name": "Silva",
        "timezone": "America/New_York",
        "language": language,
        "currency": currency,
        "date_format": "YYYY-MM-DD",
        "avatar_url": None,
        "bio": None,
    }


def _stub_hub(mock) -> None:
    """Route every rentals endpoint the apartment hub reads.

    Also stubs `/profile/me/`: the hub renders every amount through the user's
    `profile.currency`, so without this the currency falls back to the default
    and the formatted assertions become ambiguous.
    """
    mock.get(f"{HUB}/").mock(return_value=httpx.Response(200, json=apartment_payload()))
    mock.get(f"{HUB}/payments/summary/").mock(
        return_value=httpx.Response(200, json=summary_payload())
    )
    mock.get(f"{HUB}/utilities/").mock(return_value=httpx.Response(200, json=[]))
    mock.get(f"{HUB}/payments/").mock(return_value=httpx.Response(200, json=[]))
    mock.get(f"{HUB}/documents/").mock(return_value=httpx.Response(200, json=[]))
    mock.get(f"{BASE}/profile/me/").mock(
        return_value=httpx.Response(200, json=profile_payload())
    )


def _submit(client, action: str, **fields):
    """GET the hub for a live CSRF token, then POST one of its forms."""
    url = f"/rentals/apartments/{APARTMENT_ID}"
    token = _csrf_from(client.get(url).get_data(as_text=True))
    return client.post(url, data={"csrf_token": token, "action": action, **fields})


def _code_only(path: Path) -> str:
    """Read a source file with comments and docstrings removed.

    Lets a source-level assertion target executable code only, so explanatory
    prose ("there is no multipart branch") cannot satisfy or break it.
    """
    import io
    import tokenize

    source = path.read_text()
    kept: list[str] = []
    prev_type = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and prev_type in (
            tokenize.INDENT,
            tokenize.NEWLINE,
            tokenize.NL,
        ):
            # A bare string statement is a docstring.
            continue
        kept.append(token.string)
        if token.type not in (tokenize.NL, tokenize.NEWLINE):
            prev_type = token.type
    return " ".join(kept)


class TestHousesIndex:
    def test_lists_houses(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[house_payload()])
        )
        response = authed_client.get("/rentals/")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Sobrado House" in body
        assert "Rua das Flores" in body

    def test_empty_state_when_no_houses(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[])
        )
        response = authed_client.get("/rentals/")
        assert response.status_code == 200
        assert "No properties yet" in response.get_data(as_text=True)

    def test_redirects_to_login_without_a_token(self, app):
        with app.test_client() as anonymous:
            response = anonymous.get("/rentals/")
            assert response.status_code == 302
            assert "/login" in response.headers["Location"]

    def test_renders_503_when_backend_unreachable(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            side_effect=httpx.ConnectError("refused")
        )
        assert authed_client.get("/rentals/").status_code == 503


class TestCreateHouse:
    def _submit(self, client, **overrides):
        page = client.get("/rentals/")
        token = _csrf_from(page.get_data(as_text=True))
        data = {
            "name": "New House",
            "street": "Rua Nova",
            "city": "Sao Paulo",
            "state": "SP",
            "country": "BR",
        }
        data.update(overrides)
        return client.post("/rentals/houses", data={"csrf_token": token, **data})

    def test_index_exposes_a_create_control(self, respx_mock, authed_client):
        """Regression: the button was missing, so nothing could create a house."""
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[])
        )
        html = authed_client.get("/rentals/").get_data(as_text=True)
        assert 'action="/rentals/houses"' in html
        assert "Create house" in html
        # Button and modal must share one x-data root, or the button is inert.
        assert html.count("x-data=") == 1
        for field in ("name", "street", "city", "state", "country"):
            assert f'name="{field}"' in html

    def test_creates_a_house(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[])
        )
        route = respx_mock.post(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(201, json=house_payload(name="New House"))
        )
        response = self._submit(authed_client)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/rentals/")
        assert route.called
        sent = route.calls.last.request.content.decode()
        assert '"name": "New House"' in sent or '"name":"New House"' in sent

    def test_owner_is_never_sent(self, respx_mock, authed_client):
        """Ownership comes from the JWT; a client-supplied one is ignored."""
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[])
        )
        route = respx_mock.post(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(201, json=house_payload())
        )
        self._submit(authed_client)
        assert "owner_id" not in route.calls.last.request.content.decode()

    def test_rejects_a_blank_name_before_calling_the_api(
        self, respx_mock, authed_client
    ):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[])
        )
        route = respx_mock.post(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(201, json=house_payload())
        )
        self._submit(authed_client, name="   ")
        assert not route.called

    def test_rejects_a_blank_address_field(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[])
        )
        route = respx_mock.post(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(201, json=house_payload())
        )
        self._submit(authed_client, city="")
        assert not route.called

    def test_rejects_an_over_long_city(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[])
        )
        route = respx_mock.post(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(201, json=house_payload())
        )
        self._submit(authed_client, city="x" * 101)
        assert not route.called

    def test_rejects_forged_csrf_token(self, respx_mock, authed_client):
        route = respx_mock.post(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(201, json=house_payload())
        )
        response = authed_client.post(
            "/rentals/houses",
            data={
                "csrf_token": "forged",
                "name": "X",
                "street": "Y",
                "city": "Z",
                "state": "SP",
                "country": "BR",
            },
        )
        assert response.status_code == 302
        assert not route.called

    def test_redirects_to_login_without_a_token(self, app):
        with app.test_client() as anonymous:
            response = anonymous.post("/rentals/houses", data={})
            assert response.status_code == 302
            assert "/login" in response.headers["Location"]

    def test_normalises_whitespace_in_name_and_address(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[])
        )
        route = respx_mock.post(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(201, json=house_payload())
        )
        self._submit(
            authed_client, name="  Sobrado   House  ", street="  Rua  das Flores "
        )
        sent = route.calls.last.request.content.decode()
        # The Address VO collapses internal runs of whitespace.
        assert "Sobrado House" in sent
        assert "Rua das Flores" in sent
        assert "  " not in sent


class TestCreateApartment:
    def test_house_detail_exposes_a_create_control(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[house_payload()])
        )
        respx_mock.get(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(200, json=[])
        )
        html = authed_client.get(f"/rentals/houses/{HOUSE_ID}").get_data(as_text=True)
        assert "Add apartment" in html
        for field in ("number", "floor", "monthly_rent"):
            assert f'name="{field}"' in html
        assert html.count("x-data=") == 1

    def test_creates_an_apartment(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[house_payload()])
        )
        respx_mock.get(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(200, json=[])
        )
        route = respx_mock.post(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(201, json=apartment_payload())
        )
        page = authed_client.get(f"/rentals/houses/{HOUSE_ID}")
        token = _csrf_from(page.get_data(as_text=True))
        response = authed_client.post(
            f"/rentals/houses/{HOUSE_ID}/apartments",
            data={
                "csrf_token": token,
                "number": "  303  ",
                "floor": "4",
                "monthly_rent": "1800.00",
            },
        )
        assert response.status_code == 302
        assert route.called
        sent = route.calls.last.request.content.decode()
        # ApartmentNumber normalises "  303  " to "303".
        assert '"303"' in sent or '"number": "303"' in sent

    def test_rejects_a_non_numeric_floor(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[house_payload()])
        )
        respx_mock.get(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(200, json=[])
        )
        route = respx_mock.post(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(201, json=apartment_payload())
        )
        page = authed_client.get(f"/rentals/houses/{HOUSE_ID}")
        token = _csrf_from(page.get_data(as_text=True))
        response = authed_client.post(
            f"/rentals/houses/{HOUSE_ID}/apartments",
            data={
                "csrf_token": token,
                "number": "1",
                "floor": "second",
                "monthly_rent": "1000.00",
            },
        )
        # A flash plus a redirect, not a 500.
        assert response.status_code == 302
        assert not route.called

    def test_rejects_a_non_numeric_rent(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[house_payload()])
        )
        respx_mock.get(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(200, json=[])
        )
        route = respx_mock.post(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(201, json=apartment_payload())
        )
        page = authed_client.get(f"/rentals/houses/{HOUSE_ID}")
        token = _csrf_from(page.get_data(as_text=True))
        authed_client.post(
            f"/rentals/houses/{HOUSE_ID}/apartments",
            data={
                "csrf_token": token,
                "number": "1",
                "floor": "1",
                "monthly_rent": "free",
            },
        )
        assert not route.called


class TestHouseDetail:
    def test_lists_apartments(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[house_payload()])
        )
        respx_mock.get(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(200, json=[apartment_payload()])
        )
        respx_mock.get(f"{BASE}/profile/me/").mock(
            return_value=httpx.Response(200, json=profile_payload())
        )
        response = authed_client.get(f"/rentals/houses/{HOUSE_ID}")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Apartment 101" in body
        assert "$2,500.00" in body

    def test_passes_house_id_as_a_query_parameter(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[house_payload()])
        )
        route = respx_mock.get(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(200, json=[])
        )
        authed_client.get(f"/rentals/houses/{HOUSE_ID}")
        assert route.calls.last.request.url.params["house_id"] == str(HOUSE_ID)

    def test_unknown_house_flashes_and_redirects(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[])
        )
        assert authed_client.get(f"/rentals/houses/{HOUSE_ID}").status_code == 302


class TestApartmentHub:
    def test_renders_summary_and_tabs(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        respx_mock.get(f"{HUB}/utilities/").mock(
            return_value=httpx.Response(200, json=[reading_payload()])
        )
        respx_mock.get(f"{HUB}/payments/").mock(
            return_value=httpx.Response(200, json=[payment_payload()])
        )
        respx_mock.get(f"{HUB}/documents/").mock(
            return_value=httpx.Response(200, json=[document_payload()])
        )
        response = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Rent status" in body
        assert "Apartment 101" in body
        assert "$99.33" in body
        assert "lease.pdf" in body
        # The nullable description must not render as the string "None".
        assert ">None<" not in body

    def test_sends_period_query_parameters_for_the_summary(
        self, respx_mock, authed_client
    ):
        _stub_hub(respx_mock)
        route = respx_mock.get(f"{HUB}/payments/summary/").mock(
            return_value=httpx.Response(200, json=summary_payload())
        )
        authed_client.get(f"/rentals/apartments/{APARTMENT_ID}")
        params = route.calls.last.request.url.params
        assert params["year"] == str(TODAY.year)
        assert params["month"] == str(TODAY.month)

    def test_renders_when_summary_is_missing(self, respx_mock, authed_client):
        """An apartment with no payments still gets a page, not a 500."""
        _stub_hub(respx_mock)
        respx_mock.get(f"{HUB}/payments/summary/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )
        response = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}")
        assert response.status_code == 200
        assert "No payments recorded" in response.get_data(as_text=True)

    def test_nextcloud_config_reaches_the_template(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        body = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
            as_text=True
        )
        # Same-origin upload path; no Nextcloud credential is rendered.
        assert "/nextcloud-dav/rentals" in body
        assert "NEXTCLOUD_PASSWORD" not in body
        assert "btoa" not in body
        assert "nextcloudConfigured" in body

    def test_single_x_data_root_in_the_hub(self, respx_mock, authed_client):
        """AGENTS.md: Alpine ignores directives outside an x-data subtree."""
        _stub_hub(respx_mock)
        html = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
            as_text=True
        )
        assert html.count("x-data=") == 1
        assert re.search(r'<div x-data="rentalsHub\(\)"', html)


class TestRecordUtilityReading:
    def test_creates_a_reading(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/utilities/").mock(
            return_value=httpx.Response(201, json=reading_payload())
        )
        response = _submit(
            authed_client,
            "record_reading",
            utility_type="WATER",
            reading_date=TODAY.isoformat(),
            current_reading="150.50",
            previous_reading="120.00",
            unit_cost="3.2567",
        )
        assert response.status_code == 302
        assert route.called
        sent = route.calls.last.request.content.decode()
        # consumption and total_cost are derived server-side; sending them
        # would be rejected as unknown fields.
        assert "consumption" not in sent
        assert "total_cost" not in sent

    def test_rejects_future_date_without_calling_the_api(
        self, respx_mock, authed_client
    ):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/utilities/").mock(
            return_value=httpx.Response(201, json=reading_payload())
        )
        _submit(
            authed_client,
            "record_reading",
            utility_type="WATER",
            reading_date=(TODAY + timedelta(days=1)).isoformat(),
            current_reading="150.50",
            previous_reading="120.00",
            unit_cost="3.2567",
        )
        assert not route.called

    def test_rejects_current_lower_than_previous(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/utilities/").mock(
            return_value=httpx.Response(201, json=reading_payload())
        )
        _submit(
            authed_client,
            "record_reading",
            utility_type="WATER",
            reading_date=TODAY.isoformat(),
            current_reading="10.00",
            previous_reading="20.00",
            unit_cost="1.0000",
        )
        assert not route.called

    def test_rejects_non_numeric_amount(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/utilities/").mock(
            return_value=httpx.Response(201, json=reading_payload())
        )
        _submit(
            authed_client,
            "record_reading",
            utility_type="WATER",
            reading_date=TODAY.isoformat(),
            current_reading="abc",
            previous_reading="20.00",
            unit_cost="1.0000",
        )
        assert not route.called

    def test_rejects_forged_csrf_token(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/utilities/").mock(
            return_value=httpx.Response(201, json=reading_payload())
        )
        response = authed_client.post(
            f"/rentals/apartments/{APARTMENT_ID}",
            data={
                "csrf_token": "forged",
                "action": "record_reading",
                "utility_type": "WATER",
                "reading_date": TODAY.isoformat(),
                "current_reading": "10",
                "previous_reading": "1",
                "unit_cost": "1",
            },
        )
        assert response.status_code == 302
        assert not route.called

    def test_unknown_action_is_rejected(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        assert _submit(authed_client, "delete_everything").status_code == 302


class TestRecordPayment:
    def test_creates_a_payment(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/payments/").mock(
            return_value=httpx.Response(201, json=payment_payload())
        )
        response = _submit(
            authed_client,
            "record_payment",
            payment_date=TODAY.isoformat(),
            amount="2500.00",
            notes="Paid by transfer",
        )
        assert response.status_code == 302
        assert route.called
        # status is derived server-side from amount vs monthly rent.
        assert "PAID" not in route.calls.last.request.content.decode()

    def test_rejects_future_date(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/payments/").mock(
            return_value=httpx.Response(201, json=payment_payload())
        )
        _submit(
            authed_client,
            "record_payment",
            payment_date=(TODAY + timedelta(days=2)).isoformat(),
            amount="2500.00",
            notes="",
        )
        assert not route.called

    def test_rejects_zero_amount(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/payments/").mock(
            return_value=httpx.Response(201, json=payment_payload())
        )
        _submit(
            authed_client,
            "record_payment",
            payment_date=TODAY.isoformat(),
            amount="0",
            notes="",
        )
        assert not route.called


class TestRegisterDocument:
    def test_registers_a_nextcloud_url(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/documents/").mock(
            return_value=httpx.Response(201, json=document_payload())
        )
        response = _submit(
            authed_client,
            "register_document",
            document_type="LEASE_CONTRACT",
            file_url="https://cloud.example.com/s/lease.pdf",
            description="Signed lease",
        )
        assert response.status_code == 302
        assert route.called
        assert "lease.pdf" in route.calls.last.request.content.decode()

    def test_rejects_a_bare_path_before_calling_the_api(
        self, respx_mock, authed_client
    ):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/documents/").mock(
            return_value=httpx.Response(201, json=document_payload())
        )
        _submit(
            authed_client,
            "register_document",
            document_type="LEASE_CONTRACT",
            file_url="lease.pdf",
            description="",
        )
        assert not route.called

    def test_rejects_an_unknown_document_type(self, respx_mock, authed_client):
        _stub_hub(respx_mock)
        route = respx_mock.post(f"{HUB}/documents/").mock(
            return_value=httpx.Response(201, json=document_payload())
        )
        response = authed_client.post(
            f"/rentals/apartments/{APARTMENT_ID}",
            data={
                "csrf_token": _csrf_from(
                    authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
                        as_text=True
                    )
                ),
                "action": "register_document",
                "document_type": "PASSPORT",
                "file_url": "https://cloud.example.com/s/p.pdf",
            },
        )
        # A bad enum surfaces as a flash + redirect, not a 500.
        assert response.status_code == 302
        assert not route.called


class TestNoFileUploadPathExists:
    """AGENTS.md rule 6: document uploads must not route through the BFF.

    There is exactly one documented exception --
    `src/rentals/interfaces/web/upload_proxy.py`, a local-development fallback
    that is off unless `NEXTCLOUD_UPLOAD_VIA_BFF` is set. The point of these
    tests is no longer "no file handling anywhere"; it is that the exception
    stays contained in its own module and the production path does not quietly
    grow file handling of its own.
    """

    #: The single module allowed to touch uploaded bytes.
    FALLBACK_MODULE = Path("src/rentals/interfaces/web/upload_proxy.py")

    def test_routes_module_has_no_file_handling(self):
        """The main views stay Rule-6 clean, exception or not."""
        code = _code_only(Path("src/rentals/interfaces/web/routes.py"))
        assert "request.files" not in code
        assert "FileStorage" not in code
        assert "multipart" not in code
        assert "secure_filename" not in code

    def test_drf_client_still_sends_json_only(self):
        """No rentals call to the *backend* can carry a file payload.

        Reads code with comments stripped and matches the exact `httpx` kwargs.
        A substring check for `data=` would false-positive on the `json_data=`
        parameter name, and on prose that explains why uploads are direct.

        Checked on the shared `DRFAPIClient`, which is where the single request
        helper actually lives -- the rentals adapter only delegates to it. Both
        are asserted so neither can grow a side channel.
        """
        shared = _code_only(Path("src/infrastructure/api/drf_client.py"))
        assert "files=" not in shared
        assert "multipart" not in shared
        # The single request helper forwards JSON and query params only.
        # Whitespace-tolerant: the tokenizer round-trip re-joins tokens with
        # spaces, so `json=json_data` comes back as `json = json_data`.
        assert re.search(r"\bjson\s*=\s*json_data\b", shared)
        assert not re.search(r"(?<![\w_])data=", shared)
        assert not re.search(r"(?<![\w_])content=", shared)

        rentals = _code_only(Path("src/rentals/infrastructure/drf_client.py"))
        assert "files=" not in rentals
        assert "multipart" not in rentals

    def test_only_the_fallback_module_handles_uploads(self):
        """`request.files` must not leak out of the one exception module.

        Scoped deliberately: a new module growing its own upload handling is the
        regression this guards against, and asserting it repo-wide would just
        re-state the rule without bounding it.
        """
        offenders = [
            str(path)
            for path in Path("src").rglob("*.py")
            if "request.files" in _code_only(path) and path != self.FALLBACK_MODULE
        ]
        assert (
            offenders == []
        ), f"file handling escaped the fallback module: {offenders}"

    def test_fallback_module_documents_its_exception(self):
        """The exception must state why it exists, or it rots into a feature.

        Reads the RAW text, not `_code_only`: this asserts on prose, which that
        helper exists to strip.
        """
        text = self.FALLBACK_MODULE.read_text()
        assert "rule 6" in text
        assert "LOCAL DEVELOPMENT" in text
        # It must also say what it does NOT fix, since the usual wrong
        # justification for this pattern is a CORS claim that does not hold.
        assert "CORS" in text
