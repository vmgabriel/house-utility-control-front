"""Root of the application's exception hierarchy.

This lives in the shared kernel rather than in a bounded context because two
otherwise-unrelated layers both need it:

- Every context's domain errors derive from :class:`DomainException`, so a view
  can catch one type and render a friendly flash.
- :mod:`src.shared.http.drf_client` raises its own errors from that same base.

If the base lived inside a context, `src.shared.http` would have to import from
that context to declare its exceptions -- which is the dependency inversion rule
(AGENTS.md, constraint 1) turned inside out. The shared kernel is below every
context, so it is the only place both can depend on.

:class:`AuthenticationError` is here for the same reason, with a caveat worth
stating plainly: it is raised by the shared DRF client on a rejected login, so
it cannot live in the identity context without `src.shared.http` importing
`src.identity`. The identity context treats it as its own vocabulary and
re-exports it; nothing outside identity should import it from here directly.
"""


class DomainException(Exception):
    """Base exception for every error the application raises deliberately."""


class AuthenticationError(DomainException):
    """Raised when the backend rejects the supplied credentials."""
