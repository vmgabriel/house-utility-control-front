"""Helpers for asserting against a rendered page.

Kept separate from ``conftest.py`` because these are plain functions, not
fixtures, and importing a helper out of a conftest hides where it comes from.
"""

from __future__ import annotations


def main_content(html: str) -> str:
    """The page's own markup, with the shared layout stripped off.

    Assertions about a page's Alpine scopes, forms or fields must run against
    this rather than the raw response body.

    The nav is an independent interactive zone defined in ``base.html`` -- the
    account dropdown owns an ``x-data`` scope of its own. Counting ``x-data=``
    across the whole document therefore reports the nav's scope alongside the
    page's own, which would make every "this template has exactly one Alpine
    root" assertion wrong by one. Scoping to ``<main>`` measures what those
    assertions actually mean, and still fails if a page grows a second scope of
    its own.
    """
    start = html.find("<main")
    end = html.rfind("</main>")
    assert start != -1 and end != -1, "no <main> block in the rendered page"
    return html[start:end]
