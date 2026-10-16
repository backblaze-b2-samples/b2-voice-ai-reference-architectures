"""Opt-in checks against a real B2 bucket.

They run only with RUN_B2_LIVE_TESTS=1 and the B2_* variables set, write only under
`live-test/<uuid>/` and delete what they write. The key needs `writeFiles`, `readFiles`,
`listFiles` and `deleteFiles` for this test only. They never call `release`: published objects
carry retention and cannot be deleted before it ends.

Two tests are slow on purpose: the presigned URL expiry test waits a few seconds for the URL to
expire, and the multipart resume test writes one 6 MiB object. Every other object is a few bytes.
"""
from __future__ import annotations

import hashlib
import os
import random
import time
import urllib.error
import urllib.request
import uuid

import pytest

from voice_dataset_ingest import storage
from voice_dataset_ingest.config import Settings
from voice_dataset_ingest.shared import multipart

MIB = 1024 * 1024
HTTP_TIMEOUT = 15  # seconds, for every request made with urllib

REQUIRED = ("B2_APPLICATION_KEY_ID", "B2_APPLICATION_KEY", "B2_BUCKET_NAME", "B2_REGION")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_B2_LIVE_TESTS") != "1" or not all(os.environ.get(name) for name in REQUIRED),
        reason="set RUN_B2_LIVE_TESTS=1 and the B2_* variables to run against a real bucket"),
]


@pytest.fixture()
def live():
    settings = Settings.from_environ()
    client = storage.make_client(settings)
    prefix = f"live-test/{uuid.uuid4()}/"
    written: list[str] = []
    yield client, settings.bucket, prefix, written
    delete_created(client, settings.bucket, prefix, written)


def delete_created(client, bucket, prefix, keys):
    """Delete the keys this run created. Each delete is attempted on its own; failures are reported at the end."""
    assert prefix.startswith("live-test/") and len(prefix) > len("live-test/")
    failed = []
    for key in keys:
        if not key.startswith(prefix):
            failed.append(f"{key} (outside the test prefix, not deleted)")
            continue
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception as error:  # noqa: BLE001
            failed.append(f"{key} ({type(error).__name__})")
    if failed:
        raise RuntimeError("could not delete: " + ", ".join(failed))


def abort_unfinished(client, bucket, prefix, key):
    """Abort every unfinished multipart upload for exactly this key, each in its own try."""
    assert prefix.startswith("live-test/") and key.startswith(prefix)
    failed = []
    try:
        uploads = [item for page in client.get_paginator("list_multipart_uploads").paginate(Bucket=bucket, Prefix=key)
                   for item in page.get("Uploads", []) if item["Key"] == key]
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"could not list unfinished uploads ({type(error).__name__})") from error
    for item in uploads:
        try:
            multipart.abort_upload(client, bucket, key, item["UploadId"])
        except Exception as error:  # noqa: BLE001
            failed.append(f"{item['UploadId']} ({type(error).__name__})")
    if failed:
        raise RuntimeError("could not abort: " + ", ".join(failed))


def status_of(request):
    """HTTP status of a request made with a short timeout; an HTTP error answer is returned, not raised."""
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as error:
        error.close()
        return error.code


def test_put_get_and_presigned_put(live):
    client, bucket, prefix, written = live
    key = prefix + "hello.txt"
    written.append(key)
    storage.put_bytes(client, bucket, key, b"hello", "text/plain")
    assert storage.get_bytes(client, bucket, key) == b"hello"

    signed = prefix + "signed.wav"
    written.append(signed)
    url = storage.presign_put(client, bucket, signed, "audio/wav", 120)
    request = urllib.request.Request(url, data=b"RIFF", method="PUT", headers={"Content-Type": "audio/wav"})
    assert urllib.request.urlopen(request).status == 200
    assert sorted(storage.list_keys(client, bucket, prefix)) == sorted(written)


def test_object_lock_configuration_can_be_read(live):
    client, bucket, _, _ = live
    # Read only. Skips when the bucket was not created with Object Lock.
    try:
        configuration = client.get_object_lock_configuration(Bucket=bucket)
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"Object Lock is not readable on this bucket or key: {type(error).__name__}")
    assert configuration["ObjectLockConfiguration"]["ObjectLockEnabled"] == "Enabled"


def test_presigned_put_url_expires(live):
    client, bucket, prefix, written = live
    key = prefix + "expiring.wav"
    written.append(key)
    expires = 3
    url = storage.presign_put(client, bucket, key, "audio/wav", expires)
    assert f"X-Amz-Expires={expires}" in url
    headers = {"Content-Type": "audio/wav"}

    # Inside its lifetime the URL uploads the object.
    assert status_of(urllib.request.Request(url, data=b"before", method="PUT", headers=headers)) == 200
    assert storage.get_bytes(client, bucket, key) == b"before"

    # After the lifetime B2 refuses it (401 or 403) and the stored object stays as it was.
    time.sleep(expires + 3)
    # Real B2 answered 401 for an expired presigned URL on 2026-10-16. That is observed behaviour, not a
    # documented guarantee, so 403 is accepted too.
    assert status_of(urllib.request.Request(url, data=b"after", method="PUT", headers=headers)) in (401, 403)
    assert storage.get_bytes(client, bucket, key) == b"before"


def test_multipart_upload_resumes_after_an_interruption(live, tmp_path):
    client, bucket, prefix, written = live
    key = prefix + "resume.bin"
    written.append(key)
    data = random.Random(42).randbytes(6 * MIB)  # deterministic: two parts of 5 MiB and 1 MiB
    path = tmp_path / "resume.bin"
    path.write_bytes(data)

    class Interrupted(Exception):
        pass

    def interrupt_on_part_two(params, **kwargs):
        if int(params["query_string"]["partNumber"]) == 2:
            raise Interrupted()

    uploaded: list[int] = []

    def record_part(params, **kwargs):
        uploaded.append(int(params["query_string"]["partNumber"]))

    events = client.meta.events
    try:
        events.register("before-call.s3.UploadPart", interrupt_on_part_two)
        try:
            with pytest.raises(Interrupted):
                multipart.upload_file(client, bucket, key, path)
        finally:
            events.unregister("before-call.s3.UploadPart", interrupt_on_part_two)

        # The upload is unfinished: B2 holds part 1 and no object exists yet.
        upload_id = multipart.find_upload(client, bucket, key)
        assert upload_id is not None
        assert sorted(multipart.stored_parts(client, bucket, key, upload_id)) == [1]
        assert key not in storage.list_keys(client, bucket, prefix)

        # Resuming uploads only the missing part and completes the same upload.
        events.register("before-call.s3.UploadPart", record_part)
        try:
            multipart.upload_file(client, bucket, key, path)
        finally:
            events.unregister("before-call.s3.UploadPart", record_part)
        assert uploaded == [2]
        assert multipart.find_upload(client, bucket, key) is None

        assert client.head_object(Bucket=bucket, Key=key)["ContentLength"] == len(data)
        assert hashlib.sha256(storage.get_bytes(client, bucket, key)).hexdigest() == hashlib.sha256(data).hexdigest()
    finally:
        abort_unfinished(client, bucket, prefix, key)
