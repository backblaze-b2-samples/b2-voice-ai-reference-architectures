"""Helpers shared by the tests: fake settings, webhook tokens and a request recorder.

Tokens are signed at run time with PyJWT, an implementation independent of the application's own
verifier. No token is stored in the repository.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

import jwt

LIVEKIT_KEY = "unit-test-livekit-key"
LIVEKIT_SECRET = "unit-test-livekit-secret"
FAKE_ENV = {
    "B2_APPLICATION_KEY_ID": "testing",
    "B2_APPLICATION_KEY": "testing",
    "B2_REGION": "us-west-004",
    "LIVEKIT_API_KEY": LIVEKIT_KEY,
    "LIVEKIT_API_SECRET": LIVEKIT_SECRET,
}
FORBIDDEN = ("PutBucket", "CreateBucket", "DeleteBucket", "PutObjectLockConfiguration")


def digest(body: bytes) -> str:
    return base64.b64encode(hashlib.sha256(body).digest()).decode()


def claims(body: bytes, **overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    values: dict[str, Any] = {"iss": LIVEKIT_KEY, "sha256": digest(body), "nbf": now - 5, "exp": now + 300}
    for name, value in overrides.items():
        if value is None:
            values.pop(name, None)
        else:
            values[name] = value
    return values


def sign(body: bytes, secret: str = LIVEKIT_SECRET, algorithm: str = "HS256", **overrides: Any) -> str:
    """A webhook token for the body; `overrides` change or (with None) remove claims."""
    return jwt.encode(claims(body, **overrides), secret, algorithm=algorithm)


def unsigned_token(body: bytes) -> str:
    """A token with `alg` `none` and an empty signature, assembled by hand."""
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    return part({"alg": "none", "typ": "JWT"}) + "." + part(claims(body)) + "."


class Recorder:
    """Records every B2 request a client makes: (operation, path)."""

    def __init__(self, client: Any):
        self.calls: list[tuple[str, str]] = []
        client.meta.events.register("before-call.s3", self._record)

    def _record(self, model, params, **kwargs):
        self.calls.append((model.name, params.get("url_path", "")))


def all_calls(recorders: list[Recorder]) -> list[tuple[str, str]]:
    return [call for recorder in recorders for call in recorder.calls]
