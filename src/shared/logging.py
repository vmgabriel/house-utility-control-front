"""Structured JSON logging and request-id correlation.

Standard library only -- there is no ``python-json-logger`` dependency. Every
log record in the process goes through the stdlib logging hierarchy, so
configuring the root logger here also covers Gunicorn's worker records.

Two pieces:

* :class:`JsonFormatter` renders one JSON object per line with the fields
  operators actually query on: ``timestamp``, ``level``, ``logger``,
  ``message`` and ``request_id``.
* :class:`RequestIdMiddleware` mints (or propagates) an ``X-Request-ID`` for
  every request and binds it to a :class:`contextvars.ContextVar`, so any log
  emitted while handling that request carries it. ``asgiref`` copies the
  context onto the thread that runs an ``async def`` view, so the id survives
  the sync/async boundary Flask inserts.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

#: Response/request header used to correlate a request with its log lines.
REQUEST_ID_HEADER = "X-Request-ID"

#: WSGI spells headers as ``HTTP_<UPPER_WITH_UNDERSCORES>``.
_ENVIRON_REQUEST_ID = "HTTP_X_REQUEST_ID"

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """The request id bound to the current context, or ``""`` outside one."""
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """Format a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        # Only present on an error record, so a normal line carries exactly the
        # fields above and nothing else.
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Install the JSON formatter when ``LOG_FORMAT=json``.

    A no-op otherwise, so local development and the test suite keep Python's
    default human-readable output. Level is read from ``LOG_LEVEL``.
    """
    if os.getenv("LOG_FORMAT", "text").lower() != "json":
        return

    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Gunicorn's own loggers propagate to root, so they inherit this handler
    # and level; the access logger is left at INFO for request visibility.
    logging.getLogger("gunicorn.error").propagate = True


class RequestIdMiddleware:
    """WSGI middleware that correlates logs and echoes ``X-Request-ID``.

    Placed outermost, so it runs before Flask and its after-request hooks and
    therefore also covers a traceback that never reaches a view.
    """

    def __init__(self, app, header_name: str = REQUEST_ID_HEADER) -> None:
        self.app = app
        self.header_name = header_name

    def __call__(self, environ, start_response):
        request_id = environ.get(_ENVIRON_REQUEST_ID) or uuid4().hex
        token: Token = _request_id.set(request_id)

        def _start_response(status, headers, exc_info=None):
            # A handler upstream may already have set the header and echoed it
            # back; replace rather than duplicate so the value is ours.
            filtered = [
                (name, value)
                for name, value in headers
                if name.lower() != self.header_name.lower()
            ]
            filtered.append((self.header_name, request_id))
            return start_response(status, filtered, exc_info)

        try:
            return self.app(environ, _start_response)
        finally:
            # Flask runs every view -- and therefore every log record for this
            # request -- inside the call above, so the id is finished with by
            # the time it returns. Resetting here keeps the set/reset pair in
            # one context; a generator-based reset can fire in a different
            # context once the response is garbage-collected, which raises.
            _request_id.reset(token)
