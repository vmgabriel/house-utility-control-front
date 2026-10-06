"""E2E tests for the rentals apartment hub and its Alpine behaviour.

These exist because the hub's interactivity cannot be verified any other way.
A JavaScript syntax error in the hub's inline `<script>` leaves the whole page
looking fine to a server-side test -- the HTML renders, the ViewModels are
correct, every assertion on rendered text passes -- while Alpine never boots and
every button on the page is inert. That failure mode shipped once during
development and is invisible without a browser.
"""

import re
from uuid import uuid4

from playwright.sync_api import Page, Route, expect

from tests.e2e.drf_stub import StubResponse

#: The upload now targets a SAME-ORIGIN path, so this is not a cross-origin
#: request at all and the URL is whatever host the test server runs on. Matched
#: by path so it stays independent of the deployment's .env.
WEBDAV = re.compile(r".*/nextcloud-dav/.*")

APARTMENT_ID = uuid4()
HOUSE_ID = uuid4()
USER_ID = uuid4()


def house() -> dict:
    return {
        "id": str(HOUSE_ID),
        "owner_id": str(USER_ID),
        "name": "E2E House",
        "street": "Rua das Flores",
        "city": "Sao Paulo",
        "state": "SP",
        "country": "BR",
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "2026-09-02T12:00:00Z",
    }


def apartment() -> dict:
    return {
        "id": str(APARTMENT_ID),
        "house_id": str(HOUSE_ID),
        "number": "101",
        "floor": 2,
        "monthly_rent": "2500.00",
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "2026-09-02T12:00:00Z",
    }


def mock_rentals(mock_drf) -> None:
    """Route every rentals endpoint the hub reads."""
    base = "/rentals"
    mock_drf.on("GET", f"{base}/houses/", StubResponse(200, [house()]))
    mock_drf.on("GET", f"{base}/apartments/", StubResponse(200, [apartment()]))
    mock_drf.on(
        "GET", f"{base}/apartments/{APARTMENT_ID}/", StubResponse(200, apartment())
    )
    mock_drf.on(
        "GET",
        f"{base}/apartments/{APARTMENT_ID}/payments/summary/",
        StubResponse(
            200,
            {
                "apartment_id": str(APARTMENT_ID),
                "year": 2026,
                "month": 10,
                "total_paid": "2500.00",
                "outstanding_balance": "0.00",
                "payment_count": 1,
            },
        ),
    )
    for suffix in ("utilities", "payments", "documents"):
        mock_drf.on(
            "GET", f"{base}/apartments/{APARTMENT_ID}/{suffix}/", StubResponse(200, [])
        )


class TestHubBoots:
    def test_alpine_component_initialises(self, page: Page, authed: Page, mock_drf):
        """Regression: a JS syntax error left the page rendered but inert.

        The tabs are produced by `<template x-for>`, so they only exist in the
        DOM once Alpine has booted and registered the component.
        """
        mock_rentals(mock_drf)
        page.goto(f"/rentals/apartments/{APARTMENT_ID}")

        expect(page.locator("[role=tab]")).to_have_count(3)
        expect(page.get_by_role("tab", name="Documents")).to_be_visible()

    def test_no_alpine_expression_errors(self, page: Page, authed: Page, mock_drf):
        """Any 'x is not defined' warning means a property is missing."""
        errors: list[str] = []
        page.on(
            "console",
            lambda message: (
                errors.append(message.text)
                if message.type in ("error", "warning")
                else None
            ),
        )
        mock_rentals(mock_drf)
        page.goto(f"/rentals/apartments/{APARTMENT_ID}")
        page.wait_for_timeout(500)

        offending = [text for text in errors if "is not defined" in text]
        assert not offending, f"Alpine could not resolve: {offending}"

    def test_inline_component_script_parses(self, page: Page, authed: Page, mock_drf):
        """Parse the inline script the way the browser does.

        The most direct guard against the regression: a syntax error is silent
        to every other test in the suite.
        """
        mock_rentals(mock_drf)
        page.goto(f"/rentals/apartments/{APARTMENT_ID}")

        verdict = page.evaluate("""() => {
                const script = Array.from(document.scripts).find(
                    (s) => !s.src && s.textContent.includes('rentalsHub')
                );
                if (!script) return 'the hub component script is missing';
                try {
                    new Function(script.textContent);
                    return 'ok';
                } catch (error) {
                    return `syntax error: ${error.message}`;
                }
            }""")
        assert verdict == "ok", verdict

    def test_single_x_data_root(self, page: Page, authed: Page, mock_drf):
        """AGENTS.md: directives outside an x-data subtree are ignored.

        Scoped to <main>. The nav's account dropdown is an independent x-data
        zone defined in base.html, which AGENTS.md explicitly allows; counting
        the whole document would conflate the two.
        """
        mock_rentals(mock_drf)
        page.goto(f"/rentals/apartments/{APARTMENT_ID}")
        assert page.locator("main [x-data]").count() == 1
        # And the layout does have its own, so the scope above is not passing
        # merely because nothing was found.
        assert page.locator("nav [x-data]").count() == 1


class TestDocumentUploadDiagnostics:
    """The uploader must explain a CORS block instead of failing silently."""

    @staticmethod
    def _block_nextcloud(page: Page) -> list[dict]:
        """Simulate the browser refusing to read a CORS-less response.

        Playwright can intercept this request because it originates inside the
        page -- unlike the DRF API, which the browser never calls directly.
        """
        seen: list[dict] = []

        def handler(route: Route) -> None:
            request = route.request
            seen.append(
                {
                    "method": request.method,
                    "url": request.url,
                    "has_authorization": "authorization" in request.headers,
                }
            )
            # An opaque failure: the browser blocks the response, exactly as it
            # does when Nextcloud omits Access-Control-Allow-Origin.
            route.abort("accessdenied")

        page.route(WEBDAV, handler)
        return seen

    def test_cors_failure_shows_an_actionable_message(
        self, page: Page, authed: Page, mock_drf
    ):
        mock_rentals(mock_drf)
        self._block_nextcloud(page)
        page.goto(f"/rentals/apartments/{APARTMENT_ID}")

        page.get_by_role("tab", name="Documents").click()
        page.get_by_role("button", name="Upload document").click()
        page.set_input_files(
            'input[type="file"]',
            {
                "name": "lease.pdf",
                "mimeType": "application/pdf",
                "buffer": b"%PDF-1.4 lease\n",
            },
        )

        status = page.locator('[role="status"]').last
        expect(status).to_contain_text("storage proxy", timeout=15000)
        # The message must name the configured path, not a hardcoded one.
        expect(status).to_contain_text("/nextcloud-dav/")

    def test_failed_upload_never_arms_the_url(self, page: Page, authed: Page, mock_drf):
        """A blocked PUT must not leave a submittable file_url behind."""
        mock_rentals(mock_drf)
        self._block_nextcloud(page)
        page.goto(f"/rentals/apartments/{APARTMENT_ID}")

        page.get_by_role("tab", name="Documents").click()
        page.get_by_role("button", name="Upload document").click()
        page.set_input_files(
            'input[type="file"]',
            {"name": "lease.pdf", "mimeType": "application/pdf", "buffer": b"x"},
        )
        expect(page.locator('[role="status"]').last).to_contain_text(
            "storage proxy", timeout=15000
        )

        assert page.locator('input[name="file_url"]').input_value() == ""
        expect(page.get_by_role("button", name="Save document")).to_be_disabled()

    def test_upload_sends_no_authorization_header(
        self, page: Page, authed: Page, mock_drf
    ):
        """The browser must not carry any Nextcloud credential.

        The proxy injects it. A header here would put the App Password back into
        the page source, readable by every signed-in user.
        """
        mock_rentals(mock_drf)
        seen = self._block_nextcloud(page)
        page.goto(f"/rentals/apartments/{APARTMENT_ID}")

        page.get_by_role("tab", name="Documents").click()
        page.get_by_role("button", name="Upload document").click()
        page.set_input_files(
            'input[type="file"]',
            {"name": "lease.pdf", "mimeType": "application/pdf", "buffer": b"x"},
        )
        expect(page.locator('[role="status"]').last).to_contain_text(
            "storage proxy", timeout=15000
        )

        assert seen, "the browser never issued an upload request"
        assert seen[0]["method"] == "PUT"
        # The credential must be injected by the proxy, never sent by the browser.
        assert not seen[0]["has_authorization"]
        # The target must be the same-origin proxy path, not Nextcloud directly.
        assert "/nextcloud-dav/" in seen[0]["url"]

    def test_successful_upload_arms_the_url(self, page: Page, authed: Page, mock_drf):
        """The happy path: a 201 unlocks Save and fills the hidden field."""
        mock_rentals(mock_drf)
        page.route(
            WEBDAV,
            lambda route: route.fulfill(status=201, content_type="text/plain", body=""),
        )
        page.goto(f"/rentals/apartments/{APARTMENT_ID}")

        page.get_by_role("tab", name="Documents").click()
        page.get_by_role("button", name="Upload document").click()
        page.set_input_files(
            'input[type="file"]',
            {"name": "lease.pdf", "mimeType": "application/pdf", "buffer": b"x"},
        )

        expect(page.locator('[role="status"]').last).to_contain_text(
            "You can now save", timeout=15000
        )
        assert (
            page.locator('input[name="file_url"]').input_value().endswith("lease.pdf")
        )
        expect(page.get_by_role("button", name="Save document")).to_be_enabled()
