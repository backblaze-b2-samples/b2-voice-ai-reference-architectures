"""A small HTTP receiver for LiveKit webhooks, built on the standard library.

It listens on 127.0.0.1 by default. Put a TLS-terminating reverse proxy in front of it to receive
webhooks from LiveKit Cloud or from another host. Answers:

- 411 without `Content-Length`, 413 for a body larger than 1 MiB (the body is not read);
- 401 without an `Authorization` header or with a token that does not verify;
- 400 for a body that is not a usable webhook event;
- 200 when the event was archived, ignored on purpose or already processed;
- 500 for any other error (B2 or configuration), so that LiveKit retries.

The request log shows the method, path and status only; headers and tokens are never logged.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .errors import ArchiveError, Rejected, TokenRejected
from .webhook import MAX_BODY_BYTES, Receiver

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def make_server(receiver: Receiver, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> HTTPServer:
    """Bind the receiver. Requests are handled one at a time."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "voice-session-archive"
        sys_version = ""

        def _answer(self, status: int, document: dict[str, Any]) -> None:
            body = json.dumps(document).encode() + b"\n"
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 (name required by BaseHTTPRequestHandler)
            length_text = self.headers.get("Content-Length")
            if length_text is None:
                self.close_connection = True
                return self._answer(411, {"error": "Content-Length is required"})
            try:
                length = int(length_text)
            except ValueError:
                self.close_connection = True
                return self._answer(400, {"error": "Content-Length is not a number"})
            if length < 0 or length > MAX_BODY_BYTES:
                self.close_connection = True
                return self._answer(413, {"error": f"body is larger than {MAX_BODY_BYTES} bytes"})
            authorization = self.headers.get("Authorization")
            if not authorization:
                self.close_connection = True
                return self._answer(401, {"error": "Authorization is required"})
            body = self.rfile.read(length)
            try:
                result = receiver.handle(body, authorization)
            except TokenRejected as error:
                return self._answer(401, {"error": str(error)})
            except Rejected as error:
                return self._answer(400, {"error": str(error)})
            except (ArchiveError, ClientError, BotoCoreError) as error:
                print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
                return self._answer(500, {"error": "the event could not be archived; it can be retried"})
            return self._answer(200, result)

        def do_GET(self) -> None:  # noqa: N802
            self._answer(405, {"error": "only POST is accepted"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            sys.stderr.write(f"{self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}\n")

    return HTTPServer((host, port), Handler)
