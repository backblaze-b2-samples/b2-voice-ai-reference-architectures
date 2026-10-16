"""Publish an immutable dataset version with per-object Object Lock retention.

`release` writes `releases/datasets/<dataset>/<version>/`: the accepted clips, the JSONL manifest and,
last, `release-manifest.json`, which makes the version exist. Every object is written in governance
mode with its own retention date (`now + retain_days`).

The application never configures the bucket. It only reads the bucket's Object Lock configuration
(a read-only call) and refuses to publish unless Object Lock is enabled; enabling it is a bucket
setting that is permanent, shown in `examples/object-lock.json`. It sets no default retention.
Compliance mode is not offered: it would prevent withdrawing a version after a consent is revoked.

Consent is checked again for each clip. A clip whose consent is no longer active, in date and
permitting training is left out of the version and reported.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from . import __version__, consent, storage
from .errors import Conflict, ConfigError, ConsentError, Rejected

MAX_RETAIN_DAYS = 3650
LOCK_MODE = "GOVERNANCE"
CHECKS = ["dataset-line-schema", "sample-rate", "channels", "bit-depth", "minimum-duration", "not-silent",
          "not-clipped", "not-truncated", "sha256-matches-file", "active-consent-per-line"]


def check_retain_days(days: Any, allow_long: bool = False) -> int:
    """Retention in days: a positive integer, at most 3650 unless `allow_long` is set."""
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise Rejected("--retain-days must be a positive whole number of days")
    if days > MAX_RETAIN_DAYS and not allow_long:
        raise Rejected(f"--retain-days is capped at {MAX_RETAIN_DAYS}; pass --allow-long-retention to go beyond it")
    return days


def require_object_lock(client: Any, bucket: str) -> None:
    """Read the bucket's Object Lock configuration and refuse unless it is enabled."""
    hint = "enable Object Lock when you create the bucket (see examples/object-lock.json); it cannot be added by this application"
    try:
        configuration = client.get_object_lock_configuration(Bucket=bucket)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if "ObjectLockConfigurationNotFound" in code or status == 404:
            raise ConfigError(f"Object Lock is not enabled on bucket {bucket}: {hint}") from error
        raise
    if configuration.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled") != "Enabled":
        raise ConfigError(f"Object Lock is not enabled on bucket {bucket}: {hint}")


def _put_locked(client: Any, bucket: str, key: str, body: bytes, content_type: str, lock: dict[str, Any]) -> None:
    """Write one object with retention. S3 requires a Content-MD5 header on a write that sets retention."""
    md5 = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()  # integrity header, not a security use
    storage.put_bytes(client, bucket, key, body, content_type, ContentMD5=md5, **lock)


def _fingerprint(files: list[dict[str, Any]]) -> str:
    text = "".join(f"{item['sha256']}  {item['path']}\n" for item in sorted(files, key=lambda item: item["path"]))
    return hashlib.sha256(text.encode()).hexdigest()


def release_version(client: Any, bucket: str, batch: str, dataset: str, version: str, retain_days: int,
                    allow_long: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Publish one version of a dataset from a processed batch. Returns a summary."""
    storage.check_id(batch, "batch")
    storage.check_id(dataset, "dataset")
    storage.check_id(version, "version")
    days = check_retain_days(retain_days, allow_long)
    current = now or consent.now_utc()
    require_object_lock(client, bucket)

    prefix = storage.release_prefix(dataset, version)
    if storage.list_keys(client, bucket, prefix):
        raise Conflict(f"version {version} of {dataset} already exists; a version is written once, use a new version")

    manifest_key = f"{storage.candidate_prefix(batch)}manifest.jsonl"
    try:
        text = storage.get_bytes(client, bucket, manifest_key).decode()
    except ClientError as error:
        if storage.is_missing(error):
            raise Rejected(f"batch {batch} has no manifest; run process first") from error
        raise
    try:
        lines = [json.loads(item) for item in text.splitlines() if item.strip()]
    except ValueError as error:
        raise Rejected(f"the manifest of batch {batch} is not valid JSON lines") from error

    included: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for line in lines:
        try:
            if not line.get("consent_id") or not line.get("speaker"):
                raise ConsentError("missing", "a manifest line carries no consent_id or speaker")
            records.append(consent.require_consent(client, bucket, line["consent_id"], line["speaker"], current))
            included.append(line)
        except ConsentError as error:
            excluded.append({"audio_filepath": line["audio_filepath"], "reason": f"consent_{error.code}"})
    if not included:
        raise Rejected("no clip of the batch has an active consent that permits training")

    retain_until = current + timedelta(days=days)
    lock = {"ObjectLockMode": LOCK_MODE, "ObjectLockRetainUntilDate": retain_until}
    files: list[dict[str, Any]] = []
    for line in included:
        name = line["audio_filepath"]
        data = storage.get_bytes(client, bucket, f"{storage.candidate_prefix(batch)}{name}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != line["sha256"]:
            raise ConfigError(f"{name}: the candidate clip does not match its manifest line; run process again")
        _put_locked(client, bucket, f"{prefix}{name}", data, "audio/wav", lock)
        files.append({"path": name, "size": len(data), "sha256": digest, "encoding_format": "audio/wav"})

    manifest_body = ("\n".join(json.dumps(line, sort_keys=True) for line in included) + "\n").encode()
    _put_locked(client, bucket, f"{prefix}manifests/manifest.jsonl", manifest_body, "application/jsonl", lock)
    files.append({"path": "manifests/manifest.jsonl", "size": len(manifest_body),
                  "sha256": hashlib.sha256(manifest_body).hexdigest(), "encoding_format": "application/jsonl"})

    licenses = sorted({record["license"]["identifier"] for record in records})
    synthetic = all(record["subject"]["type"] == "synthetic" for record in records)
    fingerprint = _fingerprint(files)
    release = {
        "schema_version": "1.0.0",
        "id": f"{dataset}@{version}",
        "dataset": dataset,
        "version": version,
        "manifest_format": "nemo-jsonl",
        "modalities": ["audio", "text"],
        "license": licenses[0] if len(licenses) == 1 else "LicenseRef-see-consent-records",
        "provenance": {
            "source_type": "synthetic" if synthetic else "collected",
            "generated_by": f"voice-dataset-ingest {__version__}",
            "inputs": [manifest_key] + sorted({storage.consent_key(record["consent_id"]) for record in records}),
        },
        "quality": {"status": "passed", "validator": "voice-dataset-ingest", "checks": CHECKS},
        "statistics": {"file_count": len(files), "total_bytes": sum(item["size"] for item in files)},
        "integrity": {"algorithm": "sha256", "content_fingerprint": fingerprint},
        "fingerprint": fingerprint,
        "files": sorted(files, key=lambda item: item["path"]),
    }
    problems = sorted(consent.schema_validator("release-manifest.schema.json").iter_errors(release), key=str)
    if problems:
        raise ConfigError(f"the release manifest does not match its schema: {problems[0].message}")
    _put_locked(client, bucket, f"{prefix}release-manifest.json", json.dumps(release, indent=2, sort_keys=True).encode() + b"\n",
                "application/json", lock)  # last: the version exists now
    return {"release": release["id"], "prefix": prefix, "clips": len(included), "excluded": excluded,
            "retain_until": retain_until.isoformat(), "mode": LOCK_MODE, "fingerprint": fingerprint}
