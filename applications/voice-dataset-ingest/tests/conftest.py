"""Shared fixtures.

The tests run against a moto server on 127.0.0.1 (a real server is needed because presigned URLs are
fetched over HTTP). They use readable fake credentials and no B2 account. Every client the application
uses is built with `storage.make_client`; the bucket is created by a separate admin client, outside
the workflow under test.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from helpers import FAKE_ENV, Recorder
from voice_dataset_ingest import storage, synth
from voice_dataset_ingest.config import Settings


@pytest.fixture(scope="session")
def server_url():
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture(autouse=True)
def fake_environment(request, monkeypatch, tmp_path):
    """Fake credentials and a private working directory for every test except the live ones."""
    if request.node.get_closest_marker("live"):
        return
    for name, value in FAKE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("B2_BUCKET_NAME", "unset-bucket")
    monkeypatch.chdir(tmp_path)


def make_bucket(server_url: str, object_lock: bool) -> tuple[str, Any]:
    name = f"ingest-{uuid.uuid4().hex[:12]}"
    settings = Settings.from_mapping({**FAKE_ENV, "B2_BUCKET_NAME": name})
    admin = storage.make_client(settings, endpoint_url=server_url)
    admin.create_bucket(Bucket=name, ObjectLockEnabledForBucket=object_lock,
                        CreateBucketConfiguration={"LocationConstraint": FAKE_ENV["B2_REGION"]})
    return name, admin


class Lab:
    """A bucket, an admin client that sets up state, and the application's own client."""

    def __init__(self, server_url: str, bucket: str, admin: Any, monkeypatch: pytest.MonkeyPatch):
        self.server_url = server_url
        self.bucket = bucket
        self.admin = admin
        monkeypatch.setenv("B2_BUCKET_NAME", bucket)
        self.settings = Settings.from_environ()
        self.client = storage.make_client(self.settings, endpoint_url=server_url)

    def keys(self, prefix: str = "") -> list[str]:
        return storage.list_keys(self.admin, self.bucket, prefix)

    def read(self, key: str) -> bytes:
        return storage.get_bytes(self.admin, self.bucket, key)

    def read_json(self, key: str) -> Any:
        return json.loads(self.read(key))

    def consent(self, speaker: str, consent_id: str | None = None, **fields: Any) -> str:
        """Write a synthetic consent record for a speaker and return its id."""
        consent_id = consent_id or f"consent_{speaker}"
        status = fields.pop("status", "active")
        if status == "active":
            fields.setdefault("expires_at", synth.FUTURE)
        record = synth.consent_record(consent_id, speaker, status, **fields)
        storage.put_json(self.admin, self.bucket, storage.consent_key(consent_id), record)
        return consent_id

    def clip(self, batch: str, clip_id: str, speaker: str, consent_id: str, kind: str = "valid",
             seed: int = 1, text: str = "The delivery window opens at nine.", workdir: Path | None = None) -> str:
        """Put a clip and its sidecar into the incoming zone, as an earlier upload would have."""
        path = (workdir or Path.cwd()) / f"{clip_id}.wav"
        synth.write_clip(path, kind, seed)
        sidecar = {"batch": batch, "clip_id": clip_id, "speaker": speaker, "consent_id": consent_id,
                   "text": text, "lang": "en", "split": "train"}
        storage.put_json(self.admin, self.bucket, storage.sidecar_key(batch, clip_id), sidecar)
        storage.put_bytes(self.admin, self.bucket, storage.clip_key(batch, clip_id), path.read_bytes(), "audio/wav")
        return storage.clip_key(batch, clip_id)

    def event_body(self, *object_names: str, event_prefix: str = "ev") -> bytes:
        events = [{"eventVersion": 1, "eventId": f"{event_prefix}-{index}", "eventType": "b2:ObjectCreated:Upload",
                   "bucketName": self.bucket, "objectName": name} for index, name in enumerate(object_names)]
        return json.dumps({"events": events}).encode()


@pytest.fixture()
def lab(server_url, monkeypatch):
    bucket, admin = make_bucket(server_url, object_lock=True)
    return Lab(server_url, bucket, admin, monkeypatch)


@pytest.fixture()
def plain_lab(server_url, monkeypatch):
    bucket, admin = make_bucket(server_url, object_lock=False)
    return Lab(server_url, bucket, admin, monkeypatch)


@pytest.fixture()
def recorded(monkeypatch):
    """Make every client built by `storage.make_client` record its requests; returns the recorders."""
    recorders: list[Recorder] = []
    original = storage.make_client

    def wrapped(settings, endpoint_url=None):
        client = original(settings, endpoint_url)
        recorders.append(Recorder(client))
        return client

    monkeypatch.setattr(storage, "make_client", wrapped)
    return recorders


