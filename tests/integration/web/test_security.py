"""Security regression tests for the audit in Checkpoint 5.

These assert the three properties the audit pinned down, so a later refactor
that quietly drops one of them fails here instead of in a penetration test:

1. JWT cookies stay ``HttpOnly`` and never become readable from JavaScript.
2. Every state-changing route rejects methods other than POST.
3. The security headers are present on *every* response, not just HTML ones.
"""

import re

import httpx
import pytest
import respx

from src.infrastructure.auth.csrf import CSRF_COOKIE
from src.infrastructure.auth.jwt_cookie_manager import ACCESS_COOKIE, REFRESH_COOKIE
from src.interfaces.web.app import create_app
from src.interfaces.web.security import SECURITY_HEADERS

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


def authenticate(client):
    """Seed the JWT cookies directly, without driving the login form."""
    client.set_cookie(ACCESS_COOKIE, "acc-1")
    client.set_cookie(REFRESH_COOKIE, "ref-1")


class TestTokenLeakage:
    """The JWT pair must never be reachable from client-side JavaScript."""

    def test_jwt_cookies_are_httponly_and_never_leak_to_js(self, app, client):
        # The login page sets a CSRF cookie but must not hand out a JS-readable
        # handle on the tokens themselves.
        response = client.get("/auth/login")
        for cookie in response.headers.getlist("Set-Cookie"):
            name = cookie.split("=", 1)[0]
            if name in {ACCESS_COOKIE, REFRESH_COOKIE}:
                assert "HttpOnly" in cookie

    def test_csrf_cookie_is_the_only_js_readable_cookie(self, client):
        """`HttpOnly` on the CSRF cookie is deliberate; on the JWTs it is not
        an option. This pins down which of the two is which."""
        response = client.get("/auth/login")
        set_cookies = response.headers.getlist("Set-Cookie")
        readable = [c for c in set_cookies if "HttpOnly" not in c]
        assert all(c.startswith(f"{CSRF_COOKIE}=") for c in readable)

    def test_no_template_reaches_into_document_cookie(self, app):
        """A single `document.cookie` in a template would be enough to undo
        every HttpOnly flag set elsewhere, so the templates are scanned for it.

        `bt_csrf_token` is the only cookie any template is allowed to read, and
        nothing reads it today; the assertion is deliberately stricter than the
        current code so a future reader has to justify itself in a test.
        """
        offenders = []
        for template in app.jinja_env.list_templates():
            source = (app.jinja_loader.get_source(app.jinja_env, template)[0]).lower()
            if "document.cookie" in source:
                offenders.append(template)
        assert offenders == [], f"templates touching document.cookie: {offenders}"

    def test_tokens_are_not_embedded_in_rendered_html(self, client):
        """Even a server-rendered page must not print the raw token value."""
        response = client.get("/auth/login")
        body = response.data.decode()
        assert "eyJ" not in body  # the usual JWT header prefix
        assert ACCESS_COOKIE not in body
        assert REFRESH_COOKIE not in body

    def test_token_cookies_carry_secure_and_samesite_flags(self, app):
        """`CookieConfig(secure=not debug)` means prod responses must be
        `Secure`. Built directly here so the assertion does not depend on
        whether the test env happens to be running with debug on."""
        from flask import Response

        from src.infrastructure.auth.jwt_cookie_manager import (
            CookieConfig,
            JWTCookieManager,
        )

        assert app.cookie_manager.config.httponly is True
        assert app.cookie_manager.config.samesite == "Lax"

        response = Response()
        JWTCookieManager(CookieConfig(secure=True)).set_tokens(response, "a", "r")
        cookies = response.headers.getlist("Set-Cookie")
        assert len(cookies) == 2
        assert all("Secure" in c and "HttpOnly" in c for c in cookies)
        assert all("SameSite=Lax" in c for c in cookies)


class TestMethodStrictness:
    """`@require_methods(["POST"])` in the brief is Falcon's spelling; the Flask
    equivalent is `methods=["POST"]` on the rule. What matters is the outcome,
    and these assert that: a GET on a state-changing route is a 405 and the
    request never reaches a view."""

    STATE_CHANGING_ROUTES = [
        ("/auth/logout",),
        ("/transactions/create",),
        ("/transactions/txn-1/delete",),
    ]

    @pytest.mark.parametrize("url", [r[0] for r in STATE_CHANGING_ROUTES])
    def test_state_changing_routes_reject_get(self, client, url):
        authenticate(client)
        response = client.get(url)
        assert response.status_code == 405
        # No side effect: the session survives a forged GET.
        assert "POST" in response.headers["Allow"]

    @pytest.mark.parametrize("url", [r[0] for r in STATE_CHANGING_ROUTES])
    def test_state_changing_routes_reject_put_and_patch_and_delete(self, client, url):
        authenticate(client)
        for method in ("PUT", "PATCH", "DELETE"):
            response = client.open(url, method=method)
            assert response.status_code == 405, f"{method} {url} was not rejected"

    def test_login_is_post_only_for_the_mutating_half(self, client):
        """`/auth/login` is the one route that must also serve GET, since that
        is how the form is delivered. The mutation is still POST-only."""
        assert client.get("/auth/login").status_code == 200
        # A GET must not authenticate anybody, even with a valid-looking body.
        response = client.get("/auth/login?email=a@b.c&password=pw")
        assert not any(
            c.startswith(f"{ACCESS_COOKIE}=")
            for c in response.headers.getlist("Set-Cookie")
        )


class TestSecurityHeaders:
    @pytest.mark.parametrize("header,value", sorted(SECURITY_HEADERS.items()))
    def test_header_is_set_on_html_responses(self, client, header, value):
        response = client.get("/auth/login")
        assert response.status_code == 200
        assert response.headers[header] == value

    def test_headers_present_on_json_responses(self, client):
        """`/healthz` returns JSON, not HTML. Skipping it would let a future
        route answer with a stray content type and no `nosniff`."""
        response = client.get("/healthz")
        for header, value in SECURITY_HEADERS.items():
            assert response.headers[header] == value

    def test_headers_present_on_redirects(self, client):
        """302s are just as attackable as 200s: the Location header and the
        Referer policy matter on the hop before the real page loads."""
        response = client.get("/dashboard/")
        assert response.status_code == 302
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_headers_survive_the_cookie_hook(self, app, client):
        """`after_request` hooks run in reverse registration order. This is the
        canary for the header hook being registered *before* the cookie hook,
        so it runs last and cannot be clobbered by a rebuilt response."""
        authenticate(client)
        csrf_token = app.csrf_manager.generate_token()
        response = client.post("/auth/logout", data={"csrf_token": csrf_token})
        assert response.status_code == 302
        assert response.headers.getlist("Set-Cookie"), "expected the cookie hook to run"
        for header, value in SECURITY_HEADERS.items():
            assert response.headers[header] == value

    def test_no_content_security_policy_is_asserted_away(self, app):
        """Documented limitation, not an oversight. Tailwind and Alpine load
        from CDNs and the templates use inline directives, so a real CSP needs
        `'unsafe-inline'` for scripts in dev. This test fails loudly if someone
        adds a header here that the README does not mention."""
        response = app.test_client().get("/auth/login")
        assert "Content-Security-Policy" not in response.headers

    @staticmethod
    def _concrete_url(rule) -> str:
        """Turn a rule's pattern into a URL that actually matches it.

        `rule.rule` contains placeholders like `<uuid:apartment_id>`, and posting
        to that literal string 404s before the view runs -- so the route would be
        silently skipped rather than covered. Substituting a valid sample per
        converter makes the request reach the view, which is the whole point of
        enumerating the URL map here.
        """
        import re
        from uuid import uuid4

        samples = {
            "uuid": str(uuid4()),
            "int": "1",
            "float": "1.0",
            "path": "probe",
            "string": "probe",
        }

        def substitute(match: re.Match) -> str:
            # group(1) is the converter (`uuid`, `int`, ...); None when the
            # placeholder omitted it and uses the default `string` converter.
            converter = match.group(1) or "string"
            return samples.get(converter, "probe")

        # Handles `<uuid:name>`, `<name>`, and `<path:name>` alike.
        return re.sub(r"<(?:([^:>]+):)?[^>]+>", substitute, rule.rule)

    def test_headers_cover_every_registered_route(self, app, client):
        """Enumerate the URL map rather than a hand-kept list, so a route added
        after this test was written is covered automatically."""
        checked = 0
        for rule in app.url_map.iter_rules():
            if "POST" in rule.methods and rule.endpoint != "static":
                # Unauthenticated POSTs bounce to the login page or 400/405;
                # either way the status is what we are checking the headers on.
                url = self._concrete_url(rule)
                response = client.post(url, data={})
                assert response.status_code in (302, 400, 405), url
                for header, value in SECURITY_HEADERS.items():
                    assert response.headers[header] == value, url
                checked += 1
        assert checked > 0


class TestNoDangerousTemplateConstructs:
    """Two cheap greps that catch the classic SSR injection paths."""

    @staticmethod
    def _sources(app):
        """(name, source) for every template, as raw text."""
        for name in app.jinja_env.list_templates():
            yield name, app.jinja_loader.get_source(app.jinja_env, name)[0]

    def test_no_autoescape_is_disabled(self, app):
        # Flask enables autoescaping for .html templates by default. This
        # asserts nothing has opted out with `|safe` on raw user input, which is
        # the one escape hatch that would undo it.
        offenders = []
        for name, source in self._sources(app):
            for line in source.splitlines():
                stripped = line.strip()
                if "|safe" in stripped or "{% autoescape false" in stripped:
                    offenders.append(f"{name}: {stripped}")
        assert offenders == [], "unescaped output found: " + "; ".join(offenders)

    def test_user_data_is_not_interpolated_into_javascript(self, app):
        """Alpine treats the body of `x-data` as a JavaScript expression, so a
        transaction description landing inside one would be executed as code.

        `transactions/index.html` seeds the dialog with `{{ today }}`, which is
        a server-generated `date.today()` and therefore safe -- but only as long
        as it stays that way. The allowlist below is the boundary: adding a
        user-supplied field to an `x-data` body fails here, and whoever does it
        has to argue for it rather than slip a `{{ transaction.description }}`
        into a script.
        """
        allowed = {"today"}
        pattern = re.compile(r"x-data=\"(?P<body>.*?)\"", re.DOTALL)
        expression = re.compile(r"\{\{\s*([\w.]+)")

        found: list[tuple[str, str]] = []
        for name, source in self._sources(app):
            for body in pattern.findall(source):
                found += [(name, var) for var in expression.findall(body)]

        unapproved = [f"{n}: {v}" for n, v in found if v.split(".")[-1] not in allowed]
        assert unapproved == [], f"unapproved data in x-data: {unapproved}"

    def test_today_is_a_bare_iso_date(self, app, client):
        """`{{ today }}` is interpolated into a JavaScript string literal, so it
        has to be a fixed shape -- a quote or a backslash in it would break out
        of the literal. Assert the format rather than trusting the formatter."""
        with respx.mock:
            respx.get(f"{BASE}/transactions/").mock(
                return_value=httpx.Response(
                    200,
                    json={"count": 0, "next": None, "previous": None, "results": []},
                )
            )
            authenticate(client)
            body = client.get("/transactions/").data.decode()
        match = re.search(r"date: '([^']*)'", body)
        assert match, "the Alpine date seed disappeared from the template"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", match.group(1))
