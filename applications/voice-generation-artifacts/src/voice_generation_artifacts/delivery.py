"""Promote an approved attempt to a published version, and issue delivery manifests for it.

`promote` copies the attempt's outputs inside B2 (server-side copy, no download) to
`releases/deliveries/<job_id>/<version>/<language>/...` and writes `job-manifest.json` there last. That
manifest is the candidate manifest with the review decision and a `promotion` block: its `input` names
the source key, its version and its SHA-256, its `outputs[].key` names each candidate, `provider.name` is
the worker that produced them and `promotion.lineage` repeats the job id, the input SHA-256 and the
output SHA-256 values. A version is written once; a change is a new version.

`deliver` signs a GET URL for every object of a published version and writes a delivery manifest under
`releases/delivery-manifests/`. The URLs in that manifest are bearer credentials: whoever holds the
manifest can read the objects until the URLs expire.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from . import contracts, storage
from .errors import ConfigError, Conflict, Rejected
from .jobs import MANIFEST_NAME, check_seconds


def load_manifest(client: Any, bucket: str, key: str, missing_hint: str) -> dict[str, Any]:
    try:
        manifest = storage.get_json(client, bucket, key)
    except ClientError as error:
        if storage.is_missing(error):
            raise Rejected(f"{key} does not exist; {missing_hint}") from error
        raise
    contracts.validate(contracts.JOB_SCHEMA, manifest, f"the manifest {key}")
    return manifest


def delivered_name(output_key: str, job_id: str) -> str:
    """The path of a candidate under its attempt prefix, which is also its path under the version prefix."""
    prefix = f"{storage.CANDIDATES}{job_id}/"
    attempt, _, name = output_key[len(prefix):].partition("/") if output_key.startswith(prefix) else ("", "", "")
    if not attempt or not name:
        raise ConfigError(f"the manifest names {output_key}, which is not a candidate of job {job_id}")
    return name


def promote_attempt(client: Any, bucket: str, job_id: str, attempt: str, version: str, approved: bool,
                    now: datetime | None = None) -> dict[str, Any]:
    """Publish the outputs of one attempt as `version`, by server-side copy, with lineage."""
    storage.check_id(job_id, "job id")
    storage.check_id(attempt, "attempt")
    storage.check_id(version, "version")
    if not approved:
        raise Rejected("promotion needs an explicit review decision: pass --approve once the candidates are approved")
    moment = now or contracts.utc_now()

    source_prefix = storage.candidate_prefix(job_id, attempt)
    manifest = load_manifest(client, bucket, f"{source_prefix}{MANIFEST_NAME}",
                             f"run submit for job {job_id} attempt {attempt} first")
    if manifest["status"] != "succeeded" or manifest["review"]["status"] == "rejected":
        raise Rejected("only a succeeded job whose review has not been rejected can be promoted")
    if manifest["job_id"] != job_id:
        raise ConfigError("the manifest's job id does not match the job it was stored under")

    prefix = storage.delivery_prefix(job_id, version)
    if storage.list_keys(client, bucket, prefix):
        raise Conflict(f"version {version} of job {job_id} already exists; a version is written once, use a new version")

    for output in manifest["outputs"]:
        head = client.head_object(Bucket=bucket, Key=output["key"])
        if head["ContentLength"] != output["size"]:
            raise ConfigError(f"{output['key']} does not have the size its manifest records")
    for output in manifest["outputs"]:
        destination = f"{prefix}{delivered_name(output['key'], job_id)}"
        client.copy_object(Bucket=bucket, Key=destination, CopySource={"Bucket": bucket, "Key": output["key"]})

    release_id = f"{job_id}@{version}"
    published = dict(manifest)
    published["review"] = {"status": "approved", "decided_at": contracts.stamp(moment)}
    published["promotion"] = {
        "release_id": release_id,
        "version": version,
        "lineage": {"job_id": job_id, "input_sha256": manifest["input"]["sha256"],
                    "output_sha256": sorted({item["sha256"] for item in manifest["outputs"]})},
    }
    contracts.validate(contracts.JOB_SCHEMA, published, "the promoted job manifest")
    storage.put_json(client, bucket, f"{prefix}{MANIFEST_NAME}", published)  # last: the version exists now
    return {"release": release_id, "prefix": prefix, "objects": len(manifest["outputs"]),
            "worker": manifest["provider"]["name"], "manifest": f"{prefix}{MANIFEST_NAME}"}


def deliver_version(client: Any, bucket: str, job_id: str, version: str, expires: int = contracts.DEFAULT_EXPIRES_SECONDS,
                    out: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Sign a GET URL for each object of a published version and store the delivery manifest.

    The summary this returns names the manifest and its expiry but carries no URL. With `out`, the
    manifest is also written to that local file, readable by its owner only.
    """
    storage.check_id(job_id, "job id")
    storage.check_id(version, "version")
    check_seconds(expires, "--expires")
    moment = now or contracts.utc_now()

    prefix = storage.delivery_prefix(job_id, version)
    published = load_manifest(client, bucket, f"{prefix}{MANIFEST_NAME}", f"promote version {version} of job {job_id} first")
    if "promotion" not in published:
        raise Rejected(f"{prefix}{MANIFEST_NAME} has no promotion block; only a promoted version can be delivered")
    names = [delivered_name(item["key"], job_id) for item in published["outputs"]]
    keys = {name: f"{prefix}{name}" for name in names}
    keys[MANIFEST_NAME] = f"{prefix}{MANIFEST_NAME}"
    for key in keys.values():
        client.head_object(Bucket=bucket, Key=key)  # every object must exist before anything is signed

    manifest = {
        "schema_version": "1.0.0",
        "release_id": published["promotion"]["release_id"],
        "dataset": job_id,
        "version": version,
        "issued_at": contracts.stamp(moment),
        "expires_at": contracts.stamp(moment + timedelta(seconds=expires)),
        "expires_in_seconds": expires,
        "access": {"method": "GET", "credential_type": "presigned-url"},
        "urls": {name: storage.presign_get(client, bucket, key, expires) for name, key in sorted(keys.items())},
    }
    contracts.validate(contracts.DELIVERY_SCHEMA, manifest, "the delivery manifest")
    delivery_id = f"{moment:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    manifest_key = storage.delivery_manifest_key(job_id, version, delivery_id)
    storage.put_json(client, bucket, manifest_key, manifest)  # last: nothing is stored unless every object was signed
    if out is not None:
        _write_private(out, manifest)
    return {"release": manifest["release_id"], "delivery_manifest": manifest_key, "urls": len(manifest["urls"]),
            "expires_at": manifest["expires_at"], "expires_in_seconds": expires,
            "local_copy": str(out) if out is not None else None}


def _write_private(path: Path, document: dict[str, Any]) -> None:
    """Write a JSON file that only its owner can read: a delivery manifest is a bearer credential."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    os.chmod(path, 0o600)
