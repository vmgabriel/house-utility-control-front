"""The application's single Flask-Limiter instance.

Rate limiting is a cross-cutting web concern imported by more than one bounded
context -- ``identity`` guards ``/auth/login`` and ``users`` guards
``/users/register`` -- so the shared instance lives in the shared kernel rather
than being duplicated (and drifting) per context. Contexts cannot import each
other, and the kernel is the one agreed crossing point.

Two deployment notes, both consequences of the deliberately simple setup:

* Storage is in-process memory. Correct for the current single-instance
  deployment, but Gunicorn's four workers each keep their own counter, so the
  effective limit is the per-worker limit times the worker count. A shared
  backend (Redis, ...) is what makes the limit global.
* The key is the direct peer address. Behind the production reverse proxy that
  is the proxy, so every client shares one bucket; a real deployment should key
  on ``X-Forwarded-For``/``X-Real-IP`` (once the app is reachable only through
  that proxy) rather than trusting the header unconditionally.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

#: Five submissions per minute per client address is enough for a human
#: retrying a mistyped password, and far too few for credential stuffing.
AUTH_RATE_LIMIT = "5 per minute"

limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
