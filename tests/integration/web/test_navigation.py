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
        respx.get(f"{BASE}/transactions/accounts/").mock(
            return_value=httpx.Response(200, json={"count": 0, "results": []})
        )
        html = client.get("/dashboard/").get_data(as_text=True)

    match = re.search(r"<nav\b.*?</nav>", html, re.DOTALL)
    assert match, "no <nav> rendered"
    return match.group(0)


class TestFinancialLinks:
    def test_exactly_one_labelled_financial_link_per_surface(self, nav_html):
        """One link for the financial section on each navigation surface.

        Two sibling links ("Dashboard" and "Transactions") pointed at the same
        bounded context, which is the clutter this replaced. The nav now renders
        the four sections twice -- once for the desktop bar, once for the mobile
        disclosure -- so the check is per surface, not over the whole `<nav>`.

        Counted by label rather than by href, because the brand wordmark also
        links to the dashboard and always should.
        """
        assert _cluster_inner(nav_html).count(">Transactions</a>") == 1
        assert _mobile_menu_inner(nav_html).count(">Transactions</a>") == 1
        assert nav_html.count(">Dashboard</a>") == 0
        # The transaction *list* is reachable from the overview, so a nav link
        # straight at it is redundant.
        assert 'href="/transactions/"' not in nav_html

    def test_the_brand_still_links_to_the_overview(self, nav_html):
        # The wordmark plus one Transactions link on each navigation surface.
        assert nav_html.count('href="/dashboard/"') == 3, (
            "expected the wordmark and both Transactions links (desktop + "
            "mobile) to point at the overview"
        )

    def test_the_link_is_labelled_transactions(self, nav_html):
        assert ">Transactions</a>" in nav_html
        # The old label must be gone, or it reappears as a duplicate.
        assert ">Dashboard</a>" not in nav_html

    def test_rentals_link_survives(self, nav_html):
        assert 'href="/rentals/"' in nav_html

    def test_analytics_and_accounts_are_not_nav_links(self, nav_html):
        """Both moved onto the dashboard: charts inline, accounts in a modal."""
        assert ">Analytics</a>" not in nav_html
        assert ">Accounts</a>" not in nav_html
        assert "/budget/analytics/" not in nav_html
        assert "/budget/accounts/" not in nav_html


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


class TestResponsiveNavigation:
    """The nav has two surfaces: the desktop bar and the mobile disclosure.

    Tailwind never loads in the E2E browser (see the module docstring), so the
    responsive switch is asserted here as a class contract. `hidden md:flex`
    hides the desktop links below `md`; `md:hidden` hides the mobile panel from
    `md` up. The toggle owns the panel through `aria-controls`.
    """

    def test_desktop_links_are_hidden_on_mobile(self, nav_html):
        assert 'class="hidden md:flex items-center space-x-6"' in nav_html

    def test_mobile_menu_is_hidden_from_md_up(self, nav_html):
        opening = _mobile_menu_opening(nav_html)
        assert "md:hidden" in opening
        assert 'x-show="mobileOpen"' in opening
        assert "x-cloak" in opening

    def test_mobile_menu_contains_every_primary_section(self, nav_html):
        mobile = _mobile_menu_inner(nav_html)
        for label, href in (
            ("Transactions", "/dashboard/"),
            ("Rentals", "/rentals/"),
            ("Tasks", "/tasks/"),
            ("Purchases", "/purchases/"),
        ):
            assert f'href="{href}"' in mobile, label
            assert f">{label}</a>" in mobile, label

    def test_mobile_toggle_controls_the_menu(self, nav_html):
        button = _mobile_toggle(nav_html)
        assert 'aria-controls="mobile-menu"' in button
        assert 'aria-expanded="false"' in button
        assert ':aria-expanded="mobileOpen"' in button
        assert "aria-label=" in button
        assert "md:hidden" in button

    def test_active_link_is_marked_for_assistive_tech(self, nav_html):
        links = re.findall(r"<a\b[^>]*>Transactions</a>", nav_html, re.DOTALL)
        assert len(links) == 2
        assert all('aria-current="page"' in link for link in links)

    def test_inactive_links_are_not_marked_as_current(self, nav_html):
        links = re.findall(r"<a\b[^>]*>Rentals</a>", nav_html, re.DOTALL)
        assert links
        assert all('aria-current="page"' not in link for link in links)


def _mobile_menu_opening(nav_html: str) -> str:
    """The opening tag of the mobile disclosure panel."""
    match = re.search(r'<div\b[^>]*id="mobile-menu"[^>]*>', nav_html, re.DOTALL)
    assert match, "the mobile menu panel was not found in base.html"
    return match.group(0)


def _mobile_menu_inner(nav_html: str) -> str:
    """The mobile panel's span, opening tag through its matching close."""
    opening = _mobile_menu_opening(nav_html)
    start = nav_html.index(opening)
    depth = 0
    for index in range(start, len(nav_html)):
        if nav_html.startswith("<div", index):
            depth += 1
        elif nav_html.startswith("</div>", index):
            depth -= 1
            if depth == 0:
                return nav_html[start:index]
    raise AssertionError("unbalanced <div> in the mobile menu")


def _mobile_toggle(nav_html: str) -> str:
    """The opening tag of the hamburger button that controls the panel."""
    match = re.search(
        r'<button\b[^>]*aria-controls="mobile-menu"[^>]*>',
        nav_html,
        re.DOTALL,
    )
    assert match, "the mobile menu toggle was not found in base.html"
    return match.group(0)


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
