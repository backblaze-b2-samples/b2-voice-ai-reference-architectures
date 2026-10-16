"""Helpers shared by the tests: fake settings and a request recorder."""
from __future__ import annotations

from typing import Any

FAKE_ENV = {
    "B2_APPLICATION_KEY_ID": "testing",
    "B2_APPLICATION_KEY": "testing",
    "B2_REGION": "us-west-004",
}


class Recorder:
    """Records every B2 request a client makes: (operation, path)."""

    def __init__(self, client: Any):
        self.calls: list[tuple[str, str]] = []
        client.meta.events.register("before-call.s3", self._record)

    def _record(self, model, params, **kwargs):
        self.calls.append((model.name, params.get("url_path", "")))

    def operations(self) -> list[str]:
        return [name for name, _ in self.calls]

    def paths(self, operation: str) -> list[str]:
        return [path.lstrip("/") for name, path in self.calls if name == operation]

    def clear(self) -> None:
        self.calls.clear()


def all_calls(recorders: list[Recorder]) -> list[tuple[str, str]]:
    return [call for recorder in recorders for call in recorder.calls]
