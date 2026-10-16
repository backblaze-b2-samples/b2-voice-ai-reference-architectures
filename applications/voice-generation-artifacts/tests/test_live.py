"""Opt-in checks against a real B2 bucket.

They run only with RUN_B2_LIVE_TESTS=1 and the B2_* variables set, write only under
`live-test/<uuid>/` and delete what they write. The key needs `writeFiles`, `readFiles`,
`listFiles` and `deleteFiles` for this test only. The workflow commands write under the zone
prefixes of the application, not under `live-test/`, so they are not run here.

The presigned URL expiry test waits a few seconds for the URL to expire. Every object is a few bytes.
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
import uuid

import pytest

from voice_generation_artifacts import storage
from voice_generation_artifacts.config import Settings

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


def status_of(url):
    """HTTP status of a GET made with a short timeout; an HTTP error answer is returned, not raised."""
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as error:
        error.close()
        return error.code


def test_put_server_side_copy_and_presigned_get(live):
    client, bucket, prefix, written = live
    source = prefix + "source.wav"
    copy = prefix + "copy.wav"
    written.extend([source, copy])
    storage.put_bytes(client, bucket, source, b"RIFF", "audio/wav")
    client.copy_object(Bucket=bucket, Key=copy, CopySource={"Bucket": bucket, "Key": source})
    assert storage.get_bytes(client, bucket, copy) == b"RIFF"

    url = storage.presign_get(client, bucket, copy, 120)
    assert urllib.request.urlopen(url).read() == b"RIFF"
    assert sorted(storage.list_keys(client, bucket, prefix)) == sorted(written)


def test_presigned_get_url_expires(live):
    client, bucket, prefix, written = live
    key = prefix + "expiring.wav"
    written.append(key)
    storage.put_bytes(client, bucket, key, b"RIFF", "audio/wav")
    expires = 3
    url = storage.presign_get(client, bucket, key, expires)
    assert f"X-Amz-Expires={expires}" in url

    # Inside its lifetime the URL returns the object.
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
        assert response.status == 200
        assert response.read() == b"RIFF"

    # After the lifetime B2 refuses it (401 or 403), while the object itself is still there.
    time.sleep(expires + 3)
    # Real B2 answered 401 for an expired presigned URL on 2026-10-16. That is observed behaviour, not a
    # documented guarantee, so 403 is accepted too.
    assert status_of(url) in (401, 403)
    assert storage.get_bytes(client, bucket, key) == b"RIFF"
