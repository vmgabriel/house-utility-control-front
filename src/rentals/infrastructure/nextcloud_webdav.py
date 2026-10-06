"""WebDAV client for the local-development Nextcloud upload fallback.

**This adapter exists to serve one narrow purpose:** letting the browser upload
a document while the Caddy ``/nextcloud-dav`` route is unavailable during local
development. It is reached only through
:mod:`src.rentals.interfaces.web.upload_proxy`, which is the single documented
exception to AGENTS.md rule 6. Nothing in the domain or application layers may
import it.

It deliberately does *not* reuse the shared
:class:`~src.infrastructure.api.drf_client.DRFAPIClient`.
That client speaks JSON with a Bearer token to the DRF backend; Nextcloud's
WebDAV endpoint wants a byte stream with Basic auth and answers ``201``/``204``
rather than JSON. Sharing one class would mean a client with two mutually
exclusive request shapes and two credential schemes.

The credential lives here, server-side, read from the environment by the
composition root. The browser never sees it -- which is the property that
mattered when the three cross-origin designs documented in AGENTS.md were
abandoned. What this fallback gives up is bandwidth and memory, not secrecy.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import IO

import httpx

from src.rentals.domain.exceptions import NextcloudUploadError

logger = logging.getLogger(__name__)

#: Bytes read from the uploaded file per chunk when streaming it upstream.
#: Werkzeug has already spooled anything non-trivial to a temp file, so this
#: bounds the *second* copy Flask would otherwise hold in memory.
CHUNK_SIZE = 64 * 1024

#: Nextcloud caps a single WebDAV PUT. 100 MiB is generous for a tenancy
#: document and comfortably above Flask's own limits.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class NextcloudWebDavClient:
    """PUT a file into a Nextcloud WebDAV folder with Basic auth."""

    def __init__(
        self,
        base_url: str,
        dav_path: str,
        basic_auth: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        :param base_url: Nextcloud origin, e.g. ``https://cloud.example``.
        :param dav_path: WebDAV collection to write into, e.g.
            ``/remote.php/dav/files/bff-uploader/rentals``.
        :param basic_auth: base64 of ``"<username>:<app-password>"``.
        :param transport: injected in tests to avoid real sockets.
        """
        self.base_url = base_url.rstrip("/")
        self.dav_path = "/" + dav_path.strip("/")
        self._headers = {
            # Nextcloud's WebDAV endpoint expects the Authorization header to
            # carry the raw credential; there is no challenge/response step.
            "Authorization": f"Basic {basic_auth}",
        }
        self._timeout = timeout
        self._transport = transport

    def remote_url(self, filename: str) -> str:
        """The canonical Nextcloud URL for `filename`, used for logging."""
        return f"{self.base_url}{self.dav_path}/{filename}"

    async def upload(
        self,
        filename: str,
        stream: IO[bytes],
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Stream `stream` to Nextcloud as `filename` and return its URL.

        Raises:
            NextcloudUploadError: the request could not be completed, or
                Nextcloud answered with a non-success status.
        """
        url = self.remote_url(filename)
        request_kwargs: dict[str, object] = {
            "headers": {**self._headers, "Content-Type": content_type},
            # An async iterator, not `stream.read()` and not a plain generator.
            # httpx rejects a sync iterator on an AsyncClient outright
            # ("Attempted to send an sync request"), and buffering the whole
            # body with `read()` would put a whole document in memory -- the one
            # real cost of routing bytes through Flask, kept as small as it can
            # be.
            "content": _chunked(stream),
        }

        try:
            # The transport belongs on the client only. Passing it per-request
            # as well is not a valid httpx argument.
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.put(url, **request_kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            # Transport-level: DNS, TCP, TLS, or timeout. `status` stays None so
            # the caller reports "could not reach Nextcloud" rather than
            # implying Nextcloud refused the upload.
            logger.warning("Nextcloud upload to %s failed: %s", url, exc)
            raise NextcloudUploadError(
                f"Could not reach Nextcloud at {self.base_url} ({type(exc).__name__})."
            ) from exc

        if response.status_code in (200, 201, 204):
            return url

        # Nextcloud answers 401/403 for a bad App Password and 404 when the
        # target folder does not exist. Both are operator errors with distinct
        # fixes, so the status is carried through rather than flattened.
        logger.warning(
            "Nextcloud refused PUT %s with HTTP %s", url, response.status_code
        )
        raise NextcloudUploadError(
            f"Nextcloud refused the upload (HTTP {response.status_code}).",
            status=response.status_code,
        )


async def _chunked(stream: IO[bytes], size: int = CHUNK_SIZE) -> AsyncIterator[bytes]:
    """Yield `stream` in `size` blocks, without blocking the event loop.

    `asyncio.to_thread` matters more than it looks: `stream.read()` on a
    spooled file is a blocking syscall, and calling it directly from the event
    loop would stall every other in-flight request for the duration of a large
    upload. Werkzeug hands us a `SpooledTemporaryFile`, which has `read()`, so
    anything file-like works.
    """
    while chunk := await asyncio.to_thread(stream.read, size):
        yield chunk


def build_client(
    base_url: str, dav_path: str, basic_auth: str
) -> NextcloudWebDavClient | None:
    """Construct the client, or `None` when the settings are incomplete.

    Returning ``None`` rather than raising lets the composition root leave the
    upload control visibly disabled with a message naming the missing variable,
    which is friendlier than a 500 on first run.
    """
    if not base_url.strip() or not dav_path.strip() or not basic_auth.strip():
        return None
    return NextcloudWebDavClient(base_url, dav_path, basic_auth)


__all__ = [
    "MAX_UPLOAD_BYTES",
    "NextcloudWebDavClient",
    "build_client",
]
