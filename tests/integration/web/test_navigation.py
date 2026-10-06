"""The nav's layout contract, asserted on the rendered markup.

**Why these live here and not in the E2E suite.** Tailwind is loaded from a CDN
(`cdn.tailwindcss.com`), and the Playwright browser has no route to it. Every
page in the E2E suite therefore renders completely unstyled: `window.tailwind`
is undefined and the body computes to Times New Roman. A geometric assertion
there -- "the account button is 64px tall", "its centre matches the row's" --
would be measuring unstyled flow layout and would pass or fail for reasons
entirely unrelated to the CSS under review. It was tried, and it reported the
button as 70.8px while the nav row itself measured 107px, because no class in
the document had been compiled.

So the layout is asserted where it is actually decided: as the class contract in
the HTML. That catches the regression that matters -- a class dropped while
fixing alignment -- without depending on a stylesheet that never loads.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from src.interfaces.web.app import create_app
from src.shared.auth.jwt_cookie_manager import ACCESS_COOKIE, REFRESH_COOKIE

BASE = "http://api.test/api/v1"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DRF_API_BASE_URL", BASE)
    monkeypatch.setenv("FLASK_DEBUG", "false")
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def nav_html(client) -> str:
    """The rendered `<nav>` block from an authenticated page.

    The login page cannot be used: it does not extend `base.html` at all, so it
    carries no nav. Every authenticated page shares one nav, since it lives in
    `base.html`.
    """
    client.set_cookie(ACCESS_COOKIE, "acc-1")
    client.set_cookie(REFRESH_COOKIE, "ref-1")

    with respx.mock:
        respx.get(f"{BASE}/dashboard/overview/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "today": None,
                    "this_week": None,
                    "this_month": None,
                },
            )
        )
        respx.get(f"{BASE}/profile/").mock(
            return_value=httpx.Response(500, json={"detail": "unavailable"})
        )
        html = client.get("/dashboard/").get_data(as_text=True)

    match = re.search(r"<nav\b.*?</nav>", html, re.DOTALL)
    assert match, "no <nav> rendered"
    return match.group(0)


class TestFinancialLinks:
    def test_exactly_one_labelled_financial_link(self, nav_html):
        """One link for the financial section, not two.

        Two sibling links ("Dashboard" and "Transactions") pointed at the same
        bounded context, which is the clutter this replaced.

        Counted by label rather than by href, because the brand wordmark also
        links to the dashboard and always should.
        """
        assert nav_html.count(">Transactions</a>") == 1
        assert nav_html.count(">Dashboard</a>") == 0
        # The transaction *list* is reachable from the overview, so a nav link
        # straight at it is redundant.
        assert 'href="/transactions/"' not in nav_html

    def test_the_brand_still_links_to_the_overview(self, nav_html):
        assert nav_html.count('href="/dashboard/"') == 2, (
            "expected the wordmark and the Transactions link to both point at "
            "the overview"
        )

    def test_the_link_is_labelled_transactions(self, nav_html):
        assert ">Transactions</a>" in nav_html
        # The old label must be gone, or it reappears as a duplicate.
        assert ">Dashboard</a>" not in nav_html

    def test_rentals_link_survives(self, nav_html):
        assert 'href="/rentals/"' in nav_html


class TestAlignment:
    def test_row_has_two_children_not_three(self, nav_html):
        """`justify-between` distributes free space between *every* child.

        With three direct children -- brand, links, account menu -- the links
        spread apart from each other and from the account menu. Two children
        keeps them in one right-hand cluster.
        """
        assert "justify-between" in _row_classes(nav_html)
        # Counted structurally rather than by matching markup: the row's direct
        # children are the brand wrapper and the right-hand cluster.
        assert _direct_child_count(_row_inner(nav_html)) == 2, (
            "the nav row must have exactly two direct children; "
            "three means justify-between spreads the links apart"
        )

    def test_row_is_item_centred_and_fixed_height(self, nav_html):
        classes = _row_classes(nav_html)
        assert "items-center" in classes
        assert "h-16" in classes

    def test_links_are_grouped_in_one_cluster(self, nav_html):
        # The cluster that holds both the links and the account menu.
        assert 'class="flex items-center space-x-6"' in nav_html

    def test_account_button_matches_the_row_height(self, nav_html):
        """`h-16` on the button is what centres it.

        The avatar badge is `w-8 h-8` inside a 64px row. Without `h-16` the
        button's own content decides its height and it hugs the top of the line,
        which is the misalignment this fixes.
        """
        button = re.search(r"<button\b[^>]*>", nav_html, re.DOTALL)
        assert button, "account menu button not rendered"
        assert "h-16" in button.group(
            0
        ), "account button lost `h-16`; it will sit high in the nav row"
        assert "items-center" in button.group(0)

    def test_account_menu_is_inside_the_cluster(self, nav_html):
        """The cluster must contain the account menu, or `space-x-6` skips it
        and `justify-between` pushes it to the far edge of the bar.

        Asserted by bracket balance over the cluster's span, so a failure reports
        *which* side drifted rather than raising a bare `ValueError` from `.index`.
        """
        inner = _cluster_inner(nav_html)
        assert 'x-data="{ open: false }"' in inner, (
            "the account menu escaped the right-hand cluster; justify-between "
            "will push it away from the links"
        )
        # And the links are still inside the same cluster.
        assert ">Transactions</a>" in inner
        assert ">Rentals</a>" in inner


def _row_span(nav_html: str) -> re.Match[str]:
    """The `<div>` carrying `justify-between`, i.e. the nav's top-level row."""
    match = re.search(r'<div class="flex justify-between items-center h-16">', nav_html)
    assert match, "the justify-between nav row was not found in base.html"
    return match


def _row_classes(nav_html: str) -> str:
    return _row_span(nav_html).group(0)


def _row_inner(nav_html: str) -> str:
    """The row's children, from just after its opening tag to its close."""
    start = _row_span(nav_html).end()
    depth = 1
    for index in range(start, len(nav_html)):
        if nav_html.startswith("<div", index):
            depth += 1
        elif nav_html.startswith("</div>", index):
            depth -= 1
            if depth == 0:
                return nav_html[start:index]
    raise AssertionError("unbalanced <div> in the nav row")


def _cluster_inner(nav_html: str) -> str:
    """The cluster's full span, opening tag through its matching close."""
    opening = '<div class="flex items-center space-x-6">'
    start = nav_html.index(opening)
    depth = 0
    for index in range(start, len(nav_html)):
        if nav_html.startswith("<div", index):
            depth += 1
        elif nav_html.startswith("</div>", index):
            depth -= 1
            if depth == 0:
                return nav_html[start : index + len("</div>")]
    raise AssertionError("unbalanced <div> in the nav cluster")


def _direct_child_count(inner: str) -> int:
    """How many `<div>` elements open directly inside `inner`.

    Depth is tracked so a nested wrapper does not inflate the count -- the
    cluster contains its own children, and those are not siblings of the brand.
    """
    count = 0
    depth = 0
    index = 0
    while index < len(inner):
        if inner.startswith("<div", index):
            if depth == 0:
                count += 1
            depth += 1
        elif inner.startswith("</div>", index):
            depth -= 1
        index += 1
    return count
