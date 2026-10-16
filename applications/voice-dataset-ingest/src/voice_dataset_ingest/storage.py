"""The application's own S3 client for Backblaze B2, key layout and small object helpers.

Every request carries the user agent suffix below. One bucket holds all zones, with the zones
as top-level prefixes; production deployments use one bucket per zone, as the architecture does.
"""
from __future__ import annotations

import json
import re
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import Settings, endpoint_for_region
from .errors import Rejected

USER_AGENT = "b2-voice-ai-reference-architectures-voice-dataset-ingest"

# Zone and purpose prefixes inside the single bucket.
CONTRIBUTIONS = "incoming/contributions/"
CANDIDATES = "work/candidates/"
EVALUATIONS = "work/evaluations/"
DATASETS = "releases/datasets/"
CONSENTS = "protected/consents/"

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SPEAKER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
LANG_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]+)*$")
SPLITS = ("train", "dev", "test")


def make_client(settings: Settings, endpoint_url: str | None = None) -> Any:
    """Build the boto3 S3 client for B2.

    `endpoint_url` replaces the region-derived endpoint. It exists only so the tests can point
    the client at a local S3-compatible server; the command line never sets it.
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


def check_id(value: str, label: str, pattern: re.Pattern[str] = ID_PATTERN) -> str:
    if not isinstance(value, str) or not pattern.match(value) or len(value) > 100:
        raise Rejected(f"{label} must match {pattern.pattern}, got {value!r}")
    return value


def contribution_prefix(batch: str) -> str:
    return f"{CONTRIBUTIONS}{batch}/"


def clip_key(batch: str, clip_id: str) -> str:
    return f"{contribution_prefix(batch)}{clip_id}.wav"


def sidecar_key(batch: str, clip_id: str) -> str:
    return f"{contribution_prefix(batch)}{clip_id}.json"


def consent_key(consent_id: str) -> str:
    return f"{CONSENTS}{consent_id}.json"


def candidate_prefix(batch: str) -> str:
    return f"{CANDIDATES}{batch}/"


def release_prefix(dataset: str, version: str) -> str:
    return f"{DATASETS}{dataset}/{version}/"


def get_bytes(client: Any, bucket: str, key: str) -> bytes:
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def get_json(client: Any, bucket: str, key: str) -> Any:
    return json.loads(get_bytes(client, bucket, key))


def put_bytes(client: Any, bucket: str, key: str, body: bytes, content_type: str, **extra: Any) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type, **extra)


def put_json(client: Any, bucket: str, key: str, document: Any, **extra: Any) -> None:
    body = json.dumps(document, indent=2, sort_keys=True).encode() + b"\n"
    put_bytes(client, bucket, key, body, "application/json", **extra)


def is_missing(error: ClientError) -> bool:
    code = error.response.get("Error", {}).get("Code", "")
    return code in {"NoSuchKey", "404", "NotFound"}


def list_keys(client: Any, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys


def presign_put(client: Any, bucket: str, key: str, content_type: str, expires: int) -> str:
    """URL that uploads one object without credentials; the content type is part of the signature."""
    return client.generate_presigned_url(
        "put_object", Params={"Bucket": bucket, "Key": key, "ContentType": content_type}, ExpiresIn=expires)
