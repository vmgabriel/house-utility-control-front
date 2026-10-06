"""Test-suite-wide setup, loaded before any test module is imported.

Everything the application reads from the environment is pinned here, so the
suite cannot be changed by the contents of a developer's local ``.env``.

``src/interfaces/web/app.py`` calls ``load_dotenv()`` at import time, so the
gitignored ``.env`` silently becomes the application's configuration --
including under pytest. That produced two separate classes of confusing failure
before this file existed:

1. The integration suite mocks the DRF boundary with respx against
   ``http://localhost:8000/api/v1``, while the app built its client from
   whatever ``.env`` happened to say, so every request missed the mock::

       respx.models.AllMockedAssertionError: <Request('GET',
       'http://host.docker.internal:8000/api/v1/rentals/...')> not mocked!

2. With ``NEXTCLOUD_UPLOAD_VIA_BFF=true`` in a developer's ``.env``, the
   local-dev upload route was registered during tests, and a test asserting the
   *production* URL map -- plus a security-header test that enumerates every
   registered route -- failed for reasons having nothing to do with the change
   under test.

Both mean the suite's result depends on something outside version control, so a
contributor's setup silently changes what CI verifies.

**These are plain assignments, not ``setdefault``.** That is deliberate and load-
bearing. This used to be ``setdefault``, on the reasoning that ``load_dotenv()``
never overwrites an already-set variable, so declaring here wins over ``.env``.
That was true, and it stopped being true the moment the suite moved into Docker:
``docker compose run`` injects the ``bff`` service's ``environment`` block as
*real* process environment, so by the time conftest runs every one of these is
already set and ``setdefault`` was a no-op. The container's ``.env`` values won
and 54 tests failed for reasons unrelated to the code. Assignment is the only
thing that holds regardless of how the suite was launched.

Tests that need a *different* value (the upload fallback, for instance) set it
explicitly with ``monkeypatch.setenv`` and build their own app, so they still
exercise the real configuration path rather than a special case.
"""

import os

#: Must stay in sync with the base URL the respx mocks are registered against,
#: and with ``DEFAULT_API_BASE_URL`` in ``src/interfaces/web/app.py``.
os.environ["DRF_API_BASE_URL"] = "http://localhost:8000/api/v1"

# The Nextcloud upload fallback is off in tests by default. Individual tests
# turn it on deliberately; leaving it to `.env` would make the URL map -- and
# therefore the security-header test that walks it -- depend on how the suite
# was launched.
os.environ["NEXTCLOUD_UPLOAD_VIA_BFF"] = "false"

# No credential is present, so the fallback client is always `None` unless a
# test supplies one. Keeps `tests/integration/rentals/test_nextcloud_same_origin.py`
# honest about the application not holding one.
os.environ["NEXTCLOUD_BASIC_AUTH"] = ""
os.environ["NEXTCLOUD_BASE_URL"] = ""
os.environ["NEXTCLOUD_DAV_PATH"] = ""
