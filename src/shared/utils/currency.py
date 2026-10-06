"""Currency formatting driven by the user's profile preference.

The currency *code* comes from `profile.currency` (ISO 4217) and belongs to the
profile bounded context. This module is the shared kernel's translation of that
code into a display string, so every context renders money the same way and no
template ever hardcodes a symbol.

Two behaviours are worth knowing:

**Symbols are ambiguous, so codes win where they exist.** `R$` means both BRL
and (informally) other currencies, and a bare `$` tells a user nothing about
which dollar they are looking at. ISO codes are therefore used as the symbol for
the currencies that have no unambiguous glyph, and a real glyph is used only
where it is unique.

**Zero-decimal currencies are not rounded silently.** ISO 4217 assigns a minor
unit of 0 to COP, JPY, KRW and others, so `700000.50` has no representable peso.
Those amounts are quantised with ROUND_HALF_UP for display. Banker's rounding is
deliberately avoided: it would round `0.50` to `0` for an even `700000`, which
reads as an error on an invoice.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

#: Glyphs that are unambiguous on their own.
#:
#: USD renders as a bare `$` because it is overwhelmingly the most common dollar
#: and users expect to see it; MXN gets a disambiguating `MX$` prefix precisely
#: because a bare `$` there would be actively misleading. The same reasoning
#: leaves the other regional currencies spelled out as ISO codes below.
_UNAMBIGUOUS_SYMBOLS = {
    "BRL": "R$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "JPY": "\u00a5",
    "INR": "\u20b9",
    "KRW": "\u20a9",
    "MXN": "MX$",
    "PHP": "\u20b1",
    "THB": "\u0e3f",
    "USD": "$",
}

#: Trailing-space glyphs, so "MX$ 100.00" does not read as "MX$100.00".
_SPACED = {"MXN"}

#: ISO 4217 currencies whose minor unit is 0.
_ZERO_DECIMAL = frozenset(
    {
        "BIF",
        "CLP",
        "COP",
        "DJF",
        "GNF",
        "ISK",
        "JPY",
        "KMF",
        "KRW",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    }
)

#: Fallback when the profile has no usable preference. USD rather than BRL
#: because a missing preference is not a statement about the user's country, and
#: `$` is the least surprising neutral default for an unconfigured account.
DEFAULT_CURRENCY = "USD"


def normalize_currency(currency_code: str | None) -> str:
    """Return a usable ISO 4217 code.

    Falls back to :data:`DEFAULT_CURRENCY` for `None`, blank, or malformed
    input, so a profile row with an empty `currency` cannot break every money
    render on the page.
    """
    if not isinstance(currency_code, str):
        return DEFAULT_CURRENCY
    code = currency_code.strip().upper()
    return code if code.isalpha() and len(code) == 3 else DEFAULT_CURRENCY


def currency_symbol(currency_code: str | None) -> str:
    """The glyph or code to prefix amounts with.

    Falls back to the uppercase ISO code for anything unmapped -- an unknown
    currency should be *visibly* unrecognised rather than silently rendered as
    the wrong currency.
    """
    code = normalize_currency(currency_code)
    symbol = _UNAMBIGUOUS_SYMBOLS.get(code)
    if symbol is None:
        return f"{code} "
    return f"{symbol} " if code in _SPACED else symbol


def decimal_places(currency_code: str | None) -> int:
    """Fractional digits to display for `currency_code`."""
    return 0 if normalize_currency(currency_code) in _ZERO_DECIMAL else 2


def format_currency(amount: Decimal | int | str, currency_code: str | None) -> str:
    """Format `amount` with invariant separators: ``$1,234.56``.

    Locale-agnostic on purpose: this is the right choice for logs, test
    assertions, and anywhere a stable, parseable string is needed. For user-facing
    money prefer :meth:`CurrencyPreference.format`, which honours
    `profile.language`.
    """
    return _render(amount, currency_code, group_sep=",", fraction_sep=".")


def format_currency_for_locale(
    amount: Decimal | int | str,
    currency_code: str | None,
    *,
    language: str | None = None,
) -> str:
    """Format `amount`, choosing the separators from `language`.

    `profile.language` is an ISO 639-1 code such as ``pt-BR`` or ``es-CO``, and
    it is the only locale signal the BFF has. English-family locales group with
    a comma and separate the fraction with a period (``1,234.56``); everything
    else -- including pt-BR, es-CO and de -- groups with a period and separates
    the fraction with a comma (``1.234,56``).

    With no language, or an unrecognised one, the non-English form is used:
    grouping with a period and a comma fraction. That is the majority
    convention across pt, es, de, fr, it and nl, so a blank `profile.language`
    renders money the way most of the world writes it rather than the way
    English does.
    """
    if _uses_english_separators(language):
        return _render(amount, currency_code, group_sep=",", fraction_sep=".")
    return _render(amount, currency_code, group_sep=".", fraction_sep=",")


def _render(
    amount: Decimal | int | str,
    currency_code: str | None,
    *,
    group_sep: str,
    fraction_sep: str,
) -> str:
    """Quantise, group, and prefix the symbol.

    The sign is placed *before* the symbol, as in ``-$1,234.56``; appending it
    after the amount produces ``$-1,234.56``, which reads as a typo.
    """
    code = normalize_currency(currency_code)
    places = decimal_places(code)
    value = _to_decimal(amount).quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
    )
    # Build the grouping with the invariant form, then swap the separators.
    # Going through `locale` machinery is avoided because the available locales
    # are a property of the host, not of the user.
    text = f"{abs(value):,.{places}f}"
    if fraction_sep != ".":
        text = (
            text.replace(",", "\x00")
            .replace(".", fraction_sep)
            .replace("\x00", group_sep)
        )
    sign = "-" if value < 0 else ""
    return f"{sign}{currency_symbol(code)}{text}"


def _uses_english_separators(language: str | None) -> bool:
    """True when `language` is in the comma-grouping family.

    Defaults to `False` for a missing or unrecognised language, because
    `.`-grouped with a `,` fraction is the more common convention worldwide.
    """
    if not isinstance(language, str):
        return False
    return language.strip().lower().startswith("en")


def group_amount(
    amount: Decimal | int | str, places: int, language: str | None = None
) -> str:
    """Format a bare number with the locale's separators, unsigned.

    Exposed for values that must not be quantised to the currency's display
    places -- notably a per-unit utility tariff, which carries four decimals and
    would be rounded to two (or to zero for a zero-decimal currency) by
    `format_currency`. Shared so the separator rules live in exactly one place.
    """
    text = f"{abs(_to_decimal(amount)):,.{places}f}"
    if _uses_english_separators(language):
        return text
    # Swap the two separators; going through `locale` is avoided because the
    # available locales are a property of the host, not of the user.
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _to_decimal(amount: Decimal | int | str) -> Decimal:
    """Coerce to `Decimal` without going through a float."""
    if isinstance(amount, Decimal):
        return amount
    return Decimal(str(amount))


@dataclass(frozen=True, slots=True)
class CurrencyPreference:
    """A user's currency choice, bundled for template-facing formatting.

    Holds the ISO 4217 code and the locale hint together so view models and
    templates receive one value instead of two loosely-related strings that can
    get swapped at a call site.

    Constructed from the profile bounded context's entity, but without importing
    it: bounded contexts never import each other, so this accepts anything with
    ``currency`` and ``language`` attributes and reads them dynamically.
    """

    code: str = DEFAULT_CURRENCY
    language: str | None = None

    @classmethod
    def from_profile(cls, profile: object | None) -> CurrencyPreference:
        """Build from a profile-like object, or fall back when there is none.

        Accepts `None` deliberately: a user who has never created a profile must
        still see readable money, and a profile whose `currency` is blank must
        not blank out every amount on the page.
        """
        if profile is None:
            return cls()
        raw_code = getattr(profile, "currency", None)
        raw_language = getattr(profile, "language", None)
        return cls(
            code=normalize_currency(raw_code if isinstance(raw_code, str) else None),
            language=raw_language if isinstance(raw_language, str) else None,
        )

    @property
    def symbol(self) -> str:
        """The glyph or code prefix, for hand-rolled formatting (e.g. Alpine)."""
        return currency_symbol(self.code)

    @property
    def places(self) -> int:
        """Fractional digits this currency displays."""
        return decimal_places(self.code)

    def format(self, amount: Decimal | int | str) -> str:
        """Format `amount` in this currency and locale."""
        return format_currency_for_locale(amount, self.code, language=self.language)

    def to_js_config(self) -> dict[str, object]:
        """A serialisable payload for the browser-side cost preview.

        The Alpine calculator formats its own estimate, so it needs the same
        rules as the server or the preview will disagree with the stored total.
        """
        return {
            "code": self.code,
            "symbol": self.symbol,
            "places": self.places,
            "fractionSeparator": (
                "." if _uses_english_separators(self.language) else ","
            ),
            "groupSeparator": ("," if _uses_english_separators(self.language) else "."),
        }
