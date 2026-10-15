"""Multipart upload with retry, resume, complete and abort.

Parts are uploaded one at a time. Every call is retried with exponential backoff
on transient failures. When a run stops (process killed, network lost, retries
exhausted) the unfinished upload stays in B2 and the next call with the same
key finds it, lists the parts already stored, checks each against the local file
and uploads only the rest. `abort_upload` discards an upload explicitly; the
lifecycle templates in this repository also cancel unfinished uploads so parts
that are never resumed do not accumulate.
"""
from __future__ import annotations

import hashlib
import random
import time
from pathlib import Path
from typing import Any, Callable

from botocore.exceptions import ClientError, ConnectionError as BotoConnectionError, ReadTimeoutError

MIN_PART_SIZE = 5 * 1024 * 1024  # smallest part size for every part but the last
MAX_PARTS = 10_000
TRANSIENT_CODES = {"InternalError", "ServiceUnavailable", "SlowDown", "RequestTimeout", "Throttling", "ThrottlingException"}


class UploadFailed(RuntimeError):
    """Raised when retries are exhausted. The upload is kept for resume unless it was aborted."""

    def __init__(self, message: str, upload_id: str | None):
        super().__init__(message)
        self.upload_id = upload_id


def is_transient(error: Exception) -> bool:
    if isinstance(error, (BotoConnectionError, ReadTimeoutError)):
        return True
    if isinstance(error, ClientError):
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return status >= 500 or error.response.get("Error", {}).get("Code") in TRANSIENT_CODES
    return False


def with_retry(call: Callable[[], Any], attempts: int = 5, base_delay: float = 0.5,
               sleep: Callable[[float], None] = time.sleep) -> Any:
    """Run `call`, retrying transient errors with exponential backoff and jitter."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as error:  # non-transient errors are re-raised below
            if not is_transient(error) or attempt == attempts:
                raise
            sleep(base_delay * 2 ** (attempt - 1) * (0.5 + random.random()))
    raise AssertionError("unreachable")


def find_upload(client: Any, bucket: str, key: str) -> str | None:
    """Return the id of the newest unfinished upload for exactly this key, if any."""
    found: list[dict[str, Any]] = []
    paginator = client.get_paginator("list_multipart_uploads")
    for page in paginator.paginate(Bucket=bucket, Prefix=key):
        found.extend(item for item in page.get("Uploads", []) if item["Key"] == key)
    if not found:
        return None
    return max(found, key=lambda item: item["Initiated"])["UploadId"]


def stored_parts(client: Any, bucket: str, key: str, upload_id: str) -> dict[int, dict[str, Any]]:
    """Parts already stored for an upload, by part number."""
    parts: dict[int, dict[str, Any]] = {}
    paginator = client.get_paginator("list_parts")
    for page in paginator.paginate(Bucket=bucket, Key=key, UploadId=upload_id):
        for part in page.get("Parts", []):
            parts[part["PartNumber"]] = part
    return parts


def abort_upload(client: Any, bucket: str, key: str, upload_id: str) -> None:
    """Discard an unfinished upload and the parts stored for it."""
    client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)


def upload_file(client: Any, bucket: str, key: str, path: str | Path, part_size: int = MIN_PART_SIZE,
                resume: bool = True, abort_on_failure: bool = False, attempts: int = 5,
                sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Upload a file in parts and return the complete_multipart_upload response.

    resume: continue an unfinished upload for the same key instead of starting over.
    abort_on_failure: abort the upload when a part still fails after all retries.
    """
    path = Path(path)
    size = path.stat().st_size
    if part_size < MIN_PART_SIZE:
        raise ValueError(f"part_size must be at least {MIN_PART_SIZE} bytes")
    total_parts = max(1, -(-size // part_size))
    if total_parts > MAX_PARTS:
        raise ValueError(f"{total_parts} parts exceeds the limit of {MAX_PARTS}; raise part_size")

    upload_id = find_upload(client, bucket, key) if resume else None
    if upload_id is None:
        upload_id = with_retry(lambda: client.create_multipart_upload(Bucket=bucket, Key=key),
                               attempts, sleep=sleep)["UploadId"]
        done: dict[int, dict[str, Any]] = {}
    else:
        done = with_retry(lambda: stored_parts(client, bucket, key, upload_id), attempts, sleep=sleep)

    completed: list[dict[str, Any]] = []
    try:
        with path.open("rb") as handle:
            for number in range(1, total_parts + 1):
                handle.seek((number - 1) * part_size)
                body = handle.read(part_size)
                etag = f'"{hashlib.md5(body).hexdigest()}"'  # part checksum, not a security use
                stored = done.get(number)
                if stored and stored["Size"] == len(body) and stored["ETag"] == etag:
                    completed.append({"PartNumber": number, "ETag": stored["ETag"]})
                    continue
                response = with_retry(
                    lambda body=body, number=number: client.upload_part(
                        Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=body),
                    attempts, sleep=sleep)
                completed.append({"PartNumber": number, "ETag": response["ETag"]})
        return with_retry(
            lambda: client.complete_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id,
                                                     MultipartUpload={"Parts": completed}),
            attempts, sleep=sleep)
    except Exception as error:
        if abort_on_failure:
            abort_upload(client, bucket, key, upload_id)
            raise UploadFailed(f"upload of {key} aborted: {error}", None) from error
        if is_transient(error):
            raise UploadFailed(f"upload of {key} stopped; call upload_file again to resume", upload_id) from error
        raise
