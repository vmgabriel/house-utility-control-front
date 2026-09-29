"""Security headers middleware for Flask."""

from flask import Flask, Response

#: Header name -> value. Kept as data rather than four attribute assignments so
#: the full policy can be read, diffed, and asserted on in one place.
SECURITY_HEADERS: dict[str, str] = {
    # Stops a browser from re-interpreting a response as a type we never
    # intended -- the classic vector for a JSON response being run as script.
    "X-Content-Type-Options": "nosniff",
    # This app has no reason to be framed, so deny it outright rather than
    # allow-listing origins. Note that a `DENY` here is the modern replacement
    # for the deprecated `X-Frame-Options` behaviour browsers still honour.
    "X-Frame-Options": "DENY",
    # Legacy XSS auditor. Modern browsers have retired it in favour of CSP, but
    # it costs nothing and still protects older engines.
    "X-XSS-Protection": "1; mode=block",
    # Send the full origin cross-origin, but only the origin itself on a
    # downgrade or same-origin request. Keeps tokens and any future query-string
    # data out of a third party's Referer header.
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def add_security_headers(response: Response) -> Response:
    """Add standard security headers to all responses."""
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value

    # Note: Content-Security-Policy can be added here if needed, but
    # Tailwind/Alpine CDN requires 'unsafe-inline' for scripts in dev.
    return response


def init_security(app: Flask) -> None:
    """Register security hooks with the Flask app."""
    app.after_request(add_security_headers)
