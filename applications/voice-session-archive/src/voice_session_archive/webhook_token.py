"""Verify the token that LiveKit sends with each webhook.

LiveKit documents that every webhook request carries a signed JWT in the `Authorization` header,
signed with an API key the server is configured with, and that the token includes a SHA-256 hash
of the raw body. Its access-token documentation says tokens are signed with the API secret and
that `iss` is the API key, `nbf` the start of validity and `exp` the expiry.

This module implements that scheme with the Python standard library only. It accepts a token only
when all of these hold:

- it has three base64url parts, and its header is a JSON object whose `alg` is exactly `HS256`
  (`none` and every other algorithm are refused) and that carries no `crit` extension;
- the signature is the HMAC SHA-256 of `<header>.<payload>` with the API secret, compared in
  constant time;
- `exp` is present and is a number, and the token has not expired (30 seconds of clock leeway);
- `nbf`, when present, is a number and has been reached (same leeway);
- `iss` equals the configured API key;
- the `sha256` claim equals the base64 SHA-256 of the raw body, compared in constant time.

A token is never logged or stored.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from typing import Any

from .errors import TokenRejected

ALGORITHM = "HS256"
LEEWAY_SECONDS = 30
MAX_TOKEN_LENGTH = 8192
_PART = re.compile(r"^[A-Za-z0-9_-]*$")


def body_digest(body: bytes) -> str:
    """The value LiveKit puts in the `sha256` claim: base64 of the body's SHA-256."""
    return base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")


def _b64url_decode(part: str, label: str) -> bytes:
    if not _PART.match(part):
        raise TokenRejected(f"token {label} is not base64url")
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (binascii.Error, ValueError) as error:
        raise TokenRejected(f"token {label} is not base64url") from error


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as error:
        raise TokenRejected(f"token {label} is not JSON") from error
    if not isinstance(value, dict):
        raise TokenRejected(f"token {label} is not a JSON object")
    return value


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def strip_scheme(authorization: str | None) -> str:
    """The token from an `Authorization` value, with or without a `Bearer ` prefix."""
    value = (authorization or "").strip()
    if value[:7].lower() == "bearer ":
        value = value[7:].strip()
    return value


def verify(authorization: str | None, body: bytes, api_key: str, api_secret: str,
           now: float | None = None) -> dict[str, Any]:
    """Return the token's claims, or raise TokenRejected. No network call is made."""
    token = strip_scheme(authorization)
    if not token:
        raise TokenRejected("missing Authorization token")
    if len(token) > MAX_TOKEN_LENGTH:
        raise TokenRejected("Authorization token is too long")
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenRejected("Authorization token is not a JWT")
    header_part, payload_part, signature_part = parts

    header = _json_object(_b64url_decode(header_part, "header"), "header")
    if header.get("alg") != ALGORITHM:
        raise TokenRejected(f"token algorithm must be {ALGORITHM}")
    if "crit" in header:
        raise TokenRejected("token header extensions are not supported")

    signature = _b64url_decode(signature_part, "signature")
    expected = hmac.new(api_secret.encode(), f"{header_part}.{payload_part}".encode("ascii"),
                        hashlib.sha256).digest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise TokenRejected("token signature does not verify")

    claims = _json_object(_b64url_decode(payload_part, "payload"), "payload")
    current = time.time() if now is None else now
    if "exp" not in claims or not _number(claims["exp"]):
        raise TokenRejected("token has no numeric exp claim")
    if current > claims["exp"] + LEEWAY_SECONDS:
        raise TokenRejected("token has expired")
    if "nbf" in claims:
        if not _number(claims["nbf"]):
            raise TokenRejected("token nbf claim is not a number")
        if current < claims["nbf"] - LEEWAY_SECONDS:
            raise TokenRejected("token is not valid yet")
    issuer = claims.get("iss")
    if not isinstance(issuer, str) or not hmac.compare_digest(issuer.encode(), api_key.encode()):
        raise TokenRejected("token issuer is not the configured LiveKit API key")
    claimed = claims.get("sha256")
    if not isinstance(claimed, str) or not hmac.compare_digest(claimed.encode(), body_digest(body).encode()):
        raise TokenRejected("token sha256 claim does not match the body")
    return claims
