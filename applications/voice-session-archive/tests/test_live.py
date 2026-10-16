"""Opt-in check against a real B2 bucket.

It runs only with RUN_B2_LIVE_TESTS=1 and the B2_* variables set. It writes, reads and deletes a
session manifest under `live-test/<uuid>/` and nothing else. The key needs `writeFiles`,
`readFiles` and `deleteFiles` for this test only. No LiveKit server is involved.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from voice_session_archive import sessions, storage, synth
from voice_session_archive.config import Settings

REQUIRED = ("B2_APPLICATION_KEY_ID", "B2_APPLICATION_KEY", "B2_BUCKET_NAME", "B2_REGION")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_B2_LIVE_TESTS") != "1" or not all(os.environ.get(name) for name in REQUIRED),
        reason="set RUN_B2_LIVE_TESTS=1 and the B2_* variables to run against a real bucket"),
]


def test_manifest_round_trip():
    settings = Settings.from_environ()
    client = storage.make_client(settings)
    prefix = f"live-test/{uuid.uuid4()}/"
    written: list[str] = []
    try:
        data = synth.recording_bytes(1.0)
        recording_key = prefix + "recording.wav"
        written.append(recording_key)
        storage.put_bytes(client, settings.bucket, recording_key, data, "audio/wav")
        assert storage.head(client, settings.bucket, recording_key)["ContentLength"] == len(data)

        registration = sessions.build_registration("live-room", "live-session", ["caller_1:caller"], [], "short")
        ended = datetime.now(timezone.utc)
        recording = {"kind": "mixed", "path": "recording.wav", "size": len(data),
                     "sha256": storage.sha256_of(client, settings.bucket, recording_key), "encoding_format": "audio/wav"}
        manifest = sessions.build_manifest(registration, ended - timedelta(seconds=1), ended, [recording])
        manifest_key = prefix + storage.MANIFEST_NAME
        written.append(manifest_key)
        storage.put_json(client, settings.bucket, manifest_key, manifest)
        assert storage.get_json(client, settings.bucket, manifest_key) == manifest
    finally:
        for key in written:
            client.delete_object(Bucket=settings.bucket, Key=key)
