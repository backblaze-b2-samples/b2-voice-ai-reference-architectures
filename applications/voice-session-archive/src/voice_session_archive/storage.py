"""The application's own S3 client for Backblaze B2, key layout and small object helpers.

Every request carries the user agent suffix below. One bucket holds all zones, with the zones as
top-level prefixes; production deployments use one bucket per zone, as the architecture does.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import Settings, endpoint_for_region
from .errors import Rejected

USER_AGENT = "b2-voice-ai-reference-architectures-voice-session-archive"

# Zone and purpose prefixes inside the single bucket.
SESSIONS = "incoming/sessions/"
EVALUATIONS = "work/evaluations/"
CONSENTS = "protected/consents/"

REGISTRATION_NAME = "session.json"
MANIFEST_NAME = "session-manifest.json"

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ROOM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PARTICIPANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def make_client(settings: Settings, endpoint_url: str | None = None) -> Any:
    """Build the boto3 S3 client for B2.

    `endpoint_url` replaces the region-derived endpoint. It exists only so the tests can point the
    client at a local S3-compatible server; the command line never sets it.
    """
    config = Config(
        region_name=settings.region,
        signature_version="s3v4",
        user_agent_extra=USER_AGENT,
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=10,
        read_timeout=60,
        # Send checksum headers only when an operation requires them.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        s3={"addressing_style": "path"},
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or endpoint_for_region(settings.region),
        aws_access_key_id=settings.key_id,
        aws_secret_access_key=settings.application_key,
        config=config,
    )


def check_id(value: Any, label: str, pattern: re.Pattern[str] = ID_PATTERN) -> str:
    if not isinstance(value, str) or not pattern.match(value) or len(value) > 100:
        raise Rejected(f"{label} must match {pattern.pattern}, got {value!r}")
    return value


def room_prefix(room: str) -> str:
    return f"{SESSIONS}{room}/"


def session_prefix(room: str, session_id: str) -> str:
    return f"{room_prefix(room)}{session_id}/"


def registration_key(room: str, session_id: str) -> str:
    return session_prefix(room, session_id) + REGISTRATION_NAME


def manifest_key(room: str, session_id: str) -> str:
    return session_prefix(room, session_id) + MANIFEST_NAME


def consent_key(consent_id: str) -> str:
    return f"{CONSENTS}{consent_id}.json"


def get_bytes(client: Any, bucket: str, key: str) -> bytes:
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def get_json(client: Any, bucket: str, key: str) -> Any:
    return json.loads(get_bytes(client, bucket, key))


def put_bytes(client: Any, bucket: str, key: str, body: bytes, content_type: str) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)


def put_json(client: Any, bucket: str, key: str, document: Any) -> None:
    body = json.dumps(document, indent=2, sort_keys=True).encode() + b"\n"
    put_bytes(client, bucket, key, body, "application/json")


def is_missing(error: ClientError) -> bool:
    code = error.response.get("Error", {}).get("Code", "")
    return code in {"NoSuchKey", "404", "NotFound"}


def head(client: Any, bucket: str, key: str) -> dict[str, Any] | None:
    """The object's metadata, or None when it does not exist."""
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if is_missing(error):
            return None
        raise


def sha256_of(client: Any, bucket: str, key: str, chunk_size: int = 1024 * 1024) -> str:
    """Stream the object and return its SHA-256 as hex, without holding it in memory."""
    digest = hashlib.sha256()
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    for chunk in iter(lambda: body.read(chunk_size), b""):
        digest.update(chunk)
    return digest.hexdigest()
