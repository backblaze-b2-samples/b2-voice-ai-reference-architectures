"""Opt-in checks against a real B2 bucket.

They run only with RUN_B2_LIVE_TESTS=1 and the B2_* variables set, write only under
`live-test/<uuid>/` and delete what they write. The key needs `writeFiles`, `readFiles`,
`listFiles` and `deleteFiles` for this test only. They never call `release`: published objects
carry retention and cannot be deleted before it ends.
"""
from __future__ import annotations

import os
import urllib.request
import uuid

import pytest

from voice_dataset_ingest import storage
from voice_dataset_ingest.config import Settings

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
    for key in written:
        client.delete_object(Bucket=settings.bucket, Key=key)


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
