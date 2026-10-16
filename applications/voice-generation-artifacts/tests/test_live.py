"""Opt-in checks against a real B2 bucket.

They run only with RUN_B2_LIVE_TESTS=1 and the B2_* variables set, write only under
`live-test/<uuid>/` and delete what they write. The key needs `writeFiles`, `readFiles`,
`listFiles` and `deleteFiles` for this test only. The workflow commands write under the zone
prefixes of the application, not under `live-test/`, so they are not run here.
"""
from __future__ import annotations

import os
import urllib.request
import uuid

import pytest

from voice_generation_artifacts import storage
from voice_generation_artifacts.config import Settings

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
