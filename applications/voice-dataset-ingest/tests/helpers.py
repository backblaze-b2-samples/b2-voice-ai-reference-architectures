"""Helpers shared by the tests: fake settings, the Event Notifications signature and a request recorder."""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

FAKE_ENV = {
    "B2_APPLICATION_KEY_ID": "testing",
    "B2_APPLICATION_KEY": "testing",
    "B2_REGION": "us-west-004",
    "B2_EVENT_SIGNING_SECRET": "unit-test-signing-secret",
}
SIGNING_SECRET = FAKE_ENV["B2_EVENT_SIGNING_SECRET"]


def sign(body: bytes, secret: str = SIGNING_SECRET) -> str:
    """The Event Notifications signature, computed here without the application's code."""
    return "v1=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class Recorder:
    """Records every B2 request a client makes: (operation, path)."""

    def __init__(self, client: Any):
        self.calls: list[tuple[str, str]] = []
        client.meta.events.register("before-call.s3", self._record)

    def _record(self, model, params, **kwargs):
        self.calls.append((model.name, params.get("url_path", "")))

    def operations(self) -> list[str]:
        return [name for name, _ in self.calls]

    def put_keys(self) -> list[str]:
        return [path for name, path in self.calls if name == "PutObject"]


def all_calls(recorders: list[Recorder]) -> list[tuple[str, str]]:
    return [call for recorder in recorders for call in recorder.calls]
