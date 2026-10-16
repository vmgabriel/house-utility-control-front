"""The JSON formatter and the request-id middleware.

These pin the contract the production log collector depends on: one JSON object
per line with a fixed set of keys, and a request id that correlates the lines of
a single request and is echoed on the response.
"""

from __future__ import annotations

import json
import logging
import sys

from src.shared.logging import (
    JsonFormatter,
    RequestIdMiddleware,
    get_request_id,
)


def make_record(message: str = "hello", level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestJsonFormatter:
    def test_emits_exactly_the_required_fields(self):
        payload = json.loads(JsonFormatter().format(make_record()))

        assert set(payload) == {
            "timestamp",
            "level",
            "logger",
            "message",
            "request_id",
        }
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert payload["message"] == "hello"

    def test_timestamp_is_iso_8601_utc(self):
        payload = json.loads(JsonFormatter().format(make_record()))
        assert payload["timestamp"].endswith("+00:00")

    def test_request_id_is_empty_outside_a_request(self):
        payload = json.loads(JsonFormatter().format(make_record()))
        assert payload["request_id"] == ""

    def test_exception_is_rendered_only_when_present(self):
        try:
            raise ValueError("boom")
        except ValueError:
            record = make_record()
            record.exc_info = sys.exc_info()

        payload = json.loads(JsonFormatter().format(record))
        assert "ValueError: boom" in payload["exception"]


class TestRequestIdMiddleware:
    def _run(self, environ: dict) -> tuple[dict, dict]:
        captured: dict = {}

        def inner_app(environ, start_response):  # noqa: ARG001
            captured["request_id"] = get_request_id()
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        response: dict = {}

        def start_response(status, headers, exc_info=None):
            response["status"] = status
            response["headers"] = dict(headers)
            return None

        middleware = RequestIdMiddleware(inner_app)
        body = b"".join(middleware(environ, start_response))

        assert body == b"ok"
        return captured, response

    def test_mints_an_id_echoes_it_and_resets_it(self):
        captured, response = self._run({})

        assert captured["request_id"]
        assert response["headers"]["X-Request-ID"] == captured["request_id"]
        # The contextvar must not leak into the next request on this thread.
        assert get_request_id() == ""

    def test_propagates_an_incoming_id(self):
        captured, response = self._run({"HTTP_X_REQUEST_ID": "trace-42"})

        assert captured["request_id"] == "trace-42"
        assert response["headers"]["X-Request-ID"] == "trace-42"
