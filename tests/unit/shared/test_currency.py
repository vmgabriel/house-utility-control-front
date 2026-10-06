"""Currency formatting rules.

These pin the behaviour a user's `profile.currency` drives, since a regression
here silently misreports every monetary amount on the rentals pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from src.shared.utils.currency import (
    DEFAULT_CURRENCY,
    CurrencyPreference,
    currency_symbol,
    decimal_places,
    format_currency,
    format_currency_for_locale,
    normalize_currency,
)

RENT = Decimal("700000.00")


@dataclass(frozen=True)
class FakeProfile:
    """Stands in for `profile.domain.entities.UserProfile`.

    `CurrencyPreference.from_profile` reads attributes dynamically, so rentals
    never imports the profile entity -- this stub proves that boundary holds.
    """

    currency: str = "COP"
    language: str = "es-CO"


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("COP", "COP"),
            ("cop", "COP"),
            (" usd ", "USD"),
            ("", DEFAULT_CURRENCY),
            (None, DEFAULT_CURRENCY),
            ("toolong", DEFAULT_CURRENCY),
            ("12", DEFAULT_CURRENCY),
            (42, DEFAULT_CURRENCY),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert normalize_currency(raw) == expected


class TestSymbolAndPlaces:
    @pytest.mark.parametrize(
        ("code", "symbol", "places"),
        [
            ("COP", "COP ", 0),
            ("USD", "$", 2),
            ("EUR", "\u20ac", 2),
            ("BRL", "R$", 2),
            ("MXN", "MX$ ", 2),
            ("JPY", "\u00a5", 0),
        ],
    )
    def test_symbol_and_places(self, code, symbol, places):
        assert currency_symbol(code) == symbol
        assert decimal_places(code) == places

    def test_unknown_currency_is_visible_not_silent(self):
        """An unmapped code must not render as some other currency."""
        assert currency_symbol("ZZZ") == "ZZZ "


class TestFormat:
    def test_cop_has_no_decimals(self):
        assert format_currency(RENT, "COP") == "COP 700,000"

    def test_usd_has_two_decimals(self):
        assert format_currency(RENT, "USD") == "$700,000.00"

    def test_eur(self):
        assert format_currency(Decimal("1234.5"), "EUR") == "\u20ac1,234.50"

    def test_brl(self):
        assert format_currency(Decimal("1234.5"), "BRL") == "R$1,234.50"

    def test_mxn_is_disambiguated(self):
        assert format_currency(Decimal("100"), "MXN") == "MX$ 100.00"

    def test_fallback_when_currency_missing(self):
        assert format_currency(RENT, None) == "$700,000.00"

    def test_fallback_when_currency_blank(self):
        assert format_currency(RENT, "") == "$700,000.00"


class TestLocaleSeparators:
    def test_spanish_uses_comma_fraction(self):
        assert (
            format_currency_for_locale(Decimal("1234.5"), "COP", language="es-CO")
            == "COP 1.235"
        )

    def test_english_uses_period_fraction(self):
        assert (
            format_currency_for_locale(Decimal("1234.5"), "USD", language="en-US")
            == "$1,234.50"
        )

    def test_portuguese_uses_comma_fraction(self):
        assert (
            format_currency_for_locale(Decimal("1234.5"), "BRL", language="pt-BR")
            == "R$1.234,50"
        )

    def test_no_language_defaults_to_the_majority_convention(self):
        assert (
            format_currency_for_locale(Decimal("1234.5"), "USD", language=None)
            == "$1.234,50"
        )

    def test_unrecognised_language_defaults_to_the_majority_convention(self):
        assert (
            format_currency_for_locale(Decimal("1234.5"), "USD", language="zz")
            == "$1.234,50"
        )

    def test_format_currency_default_is_invariant_english_style(self):
        """The no-locale entry point keeps invariant separators."""
        assert format_currency(Decimal("1234.5"), "USD") == "$1,234.50"


class TestSignPlacement:
    def test_minus_precedes_the_symbol(self):
        assert format_currency(Decimal("-1234.5"), "USD") == "-$1,234.50"

    def test_minus_with_grouped_amount(self):
        assert format_currency(Decimal("-700000"), "USD") == "-$700,000.00"

    def test_no_minus_for_positive(self):
        assert "$-" not in format_currency(RENT, "USD")


class TestZeroDecimalRounding:
    """Half-up, not banker's rounding.

    Banker's rounding turns 0.50 into 0 for an even 700000, which reads as a
    calculation bug on an invoice.
    """

    def test_half_up_on_even_thousands(self):
        assert format_currency(Decimal("700000.50"), "COP") == "COP 700,001"

    def test_half_up_on_odd_thousands(self):
        assert format_currency(Decimal("700001.50"), "COP") == "COP 700,002"

    def test_sub_unit_rounds_up(self):
        assert format_currency(Decimal("0.50"), "COP") == "COP 1"

    def test_exact_value_is_untouched(self):
        assert format_currency(Decimal("1.00"), "COP") == "COP 1"


class TestPrecision:
    def test_string_input(self):
        assert format_currency("99.9", "USD") == "$99.90"

    def test_no_float_drift(self):
        assert format_currency(Decimal("0.1") + Decimal("0.2"), "USD") == "$0.30"

    def test_int_input(self):
        assert format_currency(1500, "USD") == "$1,500.00"


class TestCurrencyPreference:
    def test_from_profile_reads_both_fields(self):
        preference = CurrencyPreference.from_profile(
            FakeProfile(currency="COP", language="es-CO")
        )
        assert preference.code == "COP"
        assert preference.language == "es-CO"
        assert preference.symbol == "COP "
        assert preference.places == 0

    def test_from_none_falls_back(self):
        """No profile means a usable currency, not a blank amount.

        With no language either, the separators follow the majority convention
        (`.` grouped, `,` fraction) rather than English.
        """
        preference = CurrencyPreference.from_profile(None)
        assert preference.code == DEFAULT_CURRENCY
        assert preference.format(RENT) == "$700.000,00"

    def test_from_profile_with_blank_currency(self):
        preference = CurrencyPreference.from_profile(
            FakeProfile(currency="   ", language="es-CO")
        )
        assert preference.code == DEFAULT_CURRENCY

    def test_object_without_attributes_still_works(self):
        """Defensive: any object, not just a profile entity."""
        assert CurrencyPreference.from_profile(object()).code == DEFAULT_CURRENCY

    def test_js_config_matches_server_formatting(self):
        config = CurrencyPreference.from_profile(
            FakeProfile(currency="COP", language="es-CO")
        ).to_js_config()
        assert config == {
            "code": "COP",
            "symbol": "COP ",
            "places": 0,
            "fractionSeparator": ",",
            "groupSeparator": ".",
        }

    def test_js_config_for_english(self):
        config = CurrencyPreference(code="USD", language="en-US").to_js_config()
        assert config["fractionSeparator"] == "."
        assert config["groupSeparator"] == ","
