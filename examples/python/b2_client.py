"""S3-compatible client for Backblaze B2, presigned URLs and object versions.

Configuration comes from environment variables only:

    B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY, B2_REGION

The endpoint is derived from the region (for example us-west-004), so there is
no endpoint setting to keep in sync. Every request carries the user agent
suffix below, which identifies the traffic of these examples.
"""
from __future__ import annotations

import re
from os import environ
from typing import Any, Mapping

import boto3
from botocore.config import Config

USER_AGENT = "b2-voice-ai-reference-architectures"
REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]+-[0-9]{3}$")

# Zone buckets used by the architectures (see examples/README.md).
ZONE_ENV = {
    "incoming": "B2_INCOMING_BUCKET",
    "work": "B2_WORK_BUCKET",
    "releases": "B2_RELEASES_BUCKET",
    "protected": "B2_PROTECTED_BUCKET",
}


def endpoint_for_region(region: str) -> str:
    """Return the S3-compatible endpoint of a B2 region."""
    if not REGION_PATTERN.match(region):
        raise ValueError(f"B2_REGION must look like 'us-west-004', got {region!r}")
    return f"https://s3.{region}.backblazeb2.com"


def require_setting(name: str, source: Mapping[str, str] | None = None) -> str:
    value = (source if source is not None else environ).get(name, "")
    if not value:
        raise RuntimeError(f"Set {name} in the environment")
    return value


def zone_bucket(zone: str, source: Mapping[str, str] | None = None) -> str:
    """Return the bucket name of a zone from its B2_*_BUCKET variable."""
    if zone not in ZONE_ENV:
        raise ValueError(f"Unknown zone {zone!r}; expected one of {sorted(ZONE_ENV)}")
    return require_setting(ZONE_ENV[zone], source)


def make_client(source: Mapping[str, str] | None = None, endpoint_url: str | None = None) -> Any:
    """Build a boto3 S3 client for B2.

    `source` is the mapping the settings are read from (the process environment
    when omitted). `endpoint_url` replaces the region-derived endpoint. It exists
    so tests can point the client at a local S3-compatible server; applications
    leave it unset.
    """
    region = require_setting("B2_REGION", source)
    config = Config(
        region_name=region,
        signature_version="s3v4",
        user_agent_extra=USER_AGENT,
        retries={"max_attempts": 3, "mode": "standard"},
        # Send checksum headers only when an operation requires them.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        s3={"addressing_style": "path"},
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or endpoint_for_region(region),
        aws_access_key_id=require_setting("B2_APPLICATION_KEY_ID", source),
        aws_secret_access_key=require_setting("B2_APPLICATION_KEY", source),
        config=config,
    )


def presign_get(client: Any, bucket: str, key: str, expires: int = 3600, version_id: str | None = None) -> str:
    """URL that downloads one object (or one version of it) without credentials."""
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    return client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)


def presign_put(client: Any, bucket: str, key: str, content_type: str | None = None, expires: int = 900) -> str:
    """URL that uploads one object without credentials.

    When `content_type` is set it is part of the signature, so the uploader must
    send the same Content-Type header.
    """
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if content_type:
        params["ContentType"] = content_type
    return client.generate_presigned_url("put_object", Params=params, ExpiresIn=expires)


def list_versions(client: Any, bucket: str, prefix: str = "") -> list[dict[str, Any]]:
    """Every version and hide marker under a prefix."""
    versions: list[dict[str, Any]] = []
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Versions", []):
            versions.append({"key": item["Key"], "version_id": item["VersionId"],
                             "is_latest": item["IsLatest"], "size": item["Size"],
                             "last_modified": item["LastModified"], "hide_marker": False})
        for item in page.get("DeleteMarkers", []):
            versions.append({"key": item["Key"], "version_id": item["VersionId"],
                             "is_latest": item["IsLatest"], "size": 0,
                             "last_modified": item["LastModified"], "hide_marker": True})
    return versions


def read_version(client: Any, bucket: str, key: str, version_id: str) -> bytes:
    """Read the content of one specific version."""
    return client.get_object(Bucket=bucket, Key=key, VersionId=version_id)["Body"].read()


def delete_version(client: Any, bucket: str, key: str, version_id: str) -> None:
    """Permanently delete one version. A plain delete only hides the key."""
    client.delete_object(Bucket=bucket, Key=key, VersionId=version_id)
