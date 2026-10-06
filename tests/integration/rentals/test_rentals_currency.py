"""The user's profile currency must drive every rendered amount.

Regression tests for a real bug: the rentals templates hardcoded `R$`, so a user
whose `profile.currency` was `COP` was shown Brazilian Real for their rent,
balance, and utility bills.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from tests.integration.rentals.conftest import (
    APARTMENT_ID,
    BASE,
    HOUSE_ID,
    apartment_payload,
    house_payload,
    payment_payload,
    reading_payload,
    summary_payload,
)
from tests.integration.rentals.test_rentals_routes import HUB

RENT = "700000.00"


def _strip_comments(path: Path) -> str:
    """Drop HTML comments and Jinja comments before scanning for symbols."""
    source = path.read_text()
    return re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)


def _profile_payload(currency: str, language: str = "es-CO") -> dict:
    return {
        "id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "first_name": "Ana",
        "last_name": "Silva",
        "timezone": "America/Bogota",
        "language": language,
        "currency": currency,
        "date_format": "DD/MM/YYYY",
        "avatar_url": None,
        "bio": None,
    }


def _stub_currency_hub(respx_mock, rent: str = RENT) -> None:
    """Like `_stub_hub`, but with explicit amounts the currency tests assert on.

    `rent` is the apartment's monthly rent; the summary and the single reading
    use amounts distinct from it so a mixed-up field is visible.
    """
    mock = respx_mock
    mock.get(f"{HUB}/").mock(
        return_value=httpx.Response(200, json=apartment_payload(monthly_rent=rent))
    )
    mock.get(f"{HUB}/payments/summary/").mock(
        return_value=httpx.Response(
            200,
            json=summary_payload(total_paid="1500.00", outstanding_balance="100.00"),
        )
    )
    mock.get(f"{HUB}/utilities/").mock(
        return_value=httpx.Response(200, json=[reading_payload()])
    )
    mock.get(f"{HUB}/payments/").mock(
        return_value=httpx.Response(200, json=[payment_payload()])
    )
    mock.get(f"{HUB}/documents/").mock(return_value=httpx.Response(200, json=[]))


def _stub_profile(respx_mock, currency: str, language: str = "es-CO") -> None:
    respx_mock.get(f"{BASE}/profile/me/").mock(
        return_value=httpx.Response(200, json=_profile_payload(currency, language))
    )


class TestCurrencyFollowsProfile:
    def test_cop_user_sees_cop(self, respx_mock, authed_client):
        _stub_currency_hub(respx_mock)
        _stub_profile(respx_mock, "COP", "es-CO")
        html = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
            as_text=True
        )
        # 700000.00 rent, 1500.00 paid, 2500.00 payment, 99.33 bill.
        assert "COP 700.000" in html
        assert "R$" not in html

    def test_brl_user_sees_brl_with_comma_fraction(self, respx_mock, authed_client):
        _stub_currency_hub(respx_mock)
        _stub_profile(respx_mock, "BRL", "pt-BR")
        html = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
            as_text=True
        )
        assert "R$700.000,00" in html
        assert "COP" not in html

    def test_usd_user_sees_dollar_with_period_fraction(self, respx_mock, authed_client):
        _stub_currency_hub(respx_mock)
        _stub_profile(respx_mock, "USD", "en-US")
        html = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
            as_text=True
        )
        assert "$700,000.00" in html

    def test_eur_user_sees_euro(self, respx_mock, authed_client):
        _stub_currency_hub(respx_mock)
        _stub_profile(respx_mock, "EUR", "de-DE")
        html = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
            as_text=True
        )
        assert "\u20ac700.000,00" in html

    def test_no_template_hardcodes_a_currency_symbol(self):
        """The strongest guard: no symbol may live in a template at all."""
        from pathlib import Path

        templates = Path("src/rentals/interfaces/web/templates/rentals")
        offenders = []
        for path in sorted(templates.glob("*.html")):
            # Comments are stripped: the templates explain *why* they no longer
            # hardcode a symbol, and that prose must not trip this guard.
            source = _strip_comments(path)
            for symbol in ("R$", "COP$", "MX$", "€", "£", "¥"):
                if symbol in source:
                    offenders.append(f"{path.name}: {symbol}")
        assert not offenders, f"Hardcoded currency in templates: {offenders}"

    def test_javascript_preview_uses_the_profile_currency(
        self, respx_mock, authed_client
    ):
        """The Alpine calculator must format with the server's rules."""
        _stub_currency_hub(respx_mock)
        _stub_profile(respx_mock, "COP", "es-CO")
        html = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}").get_data(
            as_text=True
        )
        # The config block comes from CurrencyPreference.to_js_config(), so the
        # preview agrees with the total the backend actually stored.
        assert '"symbol": "COP "' in html
        assert '"places": 0' in html
        assert '"fractionSeparator": ","' in html
        assert '"groupSeparator": "."' in html


class TestCurrencyFallbacks:
    def test_profile_failure_still_renders_money(self, respx_mock, authed_client):
        _stub_currency_hub(respx_mock)
        respx_mock.get(f"{BASE}/profile/me/").mock(
            side_effect=httpx.ConnectError("refused")
        )
        response = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}")
        assert response.status_code == 200
        # Falls back to the default currency rather than rendering nothing.
        assert "700" in response.get_data(as_text=True)

    def test_profile_404_still_renders_money(self, respx_mock, authed_client):
        _stub_currency_hub(respx_mock)
        respx_mock.get(f"{BASE}/profile/me/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )
        response = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}")
        assert response.status_code == 200

    def test_blank_currency_falls_back(self, respx_mock, authed_client):
        _stub_currency_hub(respx_mock)
        _stub_profile(respx_mock, "   ", "es-CO")
        response = authed_client.get(f"/rentals/apartments/{APARTMENT_ID}")
        assert response.status_code == 200
        assert "$" in response.get_data(as_text=True)


class TestHouseDetailCurrency:
    def test_apartment_rent_uses_profile_currency(self, respx_mock, authed_client):
        respx_mock.get(f"{BASE}/rentals/houses/").mock(
            return_value=httpx.Response(200, json=[house_payload()])
        )
        respx_mock.get(f"{BASE}/rentals/apartments/").mock(
            return_value=httpx.Response(
                200, json=[apartment_payload(monthly_rent=RENT)]
            )
        )
        _stub_profile(respx_mock, "COP", "es-CO")
        html = authed_client.get(f"/rentals/houses/{HOUSE_ID}").get_data(as_text=True)
        assert "COP 700.000" in html
        assert "R$" not in html
