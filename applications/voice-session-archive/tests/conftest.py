"""Shared fixtures.

The tests run against a moto server on 127.0.0.1 with readable fake credentials and no B2 account.
Every client the application uses is built with `storage.make_client`; the bucket is created by a
separate admin client, outside the workflow under test. No LiveKit server is needed: the tests
send signed sample `egress_ended` bodies.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from helpers import FAKE_ENV, Recorder, sign
from voice_session_archive import cli, storage, synth
from voice_session_archive.config import Settings


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


class Lab:
    """A bucket, an admin client that sets up state, and helpers to drive the command line."""

    room = "support-room"
    session_id = "session-0001"

    def __init__(self, server_url: str, monkeypatch: pytest.MonkeyPatch):
        self.server_url = server_url
        self.bucket = f"sessions-{uuid.uuid4().hex[:12]}"
        monkeypatch.setenv("B2_BUCKET_NAME", self.bucket)
        self.settings = Settings.from_environ()
        self.admin = storage.make_client(self.settings, endpoint_url=server_url)
        self.admin.create_bucket(Bucket=self.bucket,
                                 CreateBucketConfiguration={"LocationConstraint": FAKE_ENV["B2_REGION"]})
        self.counter = 0

    def run(self, argv: list[str]) -> int:
        return cli.main(argv, endpoint_url=self.server_url)

    def keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        for page in self.admin.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return keys

    def read_json(self, key: str) -> Any:
        return json.loads(self.admin.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    @property
    def manifest_key(self) -> str:
        return storage.manifest_key(self.room, self.session_id)

    def register(self, consent_ids: tuple[str, ...] = ("consent_caller",)) -> None:
        argv = ["register-session", "--room", self.room, "--session-id", self.session_id,
                "--participant", "caller_1:caller", "--participant", "agent_1:agent", "--retention-class", "standard"]
        for consent_id in consent_ids:
            argv += ["--consent-id", consent_id]
        assert self.run(argv) == 0

    def consent(self, consent_id: str = "consent_caller", **fields: Any) -> None:
        record = synth.consent_record(consent_id, "caller_1", **fields)
        storage.put_json(self.admin, self.bucket, storage.consent_key(consent_id), record)

    def recording(self, name: str = "recording.wav", seconds: float = 2.0, upload: bool = True) -> tuple[str, int]:
        data = synth.recording_bytes(seconds)
        key = storage.session_prefix(self.room, self.session_id) + name
        if upload:
            storage.put_bytes(self.admin, self.bucket, key, data, "audio/wav")
        return key, len(data)

    def body(self, files: list[tuple[str, int]], status: str = "EGRESS_COMPLETE", event_id: str | None = None,
             seconds: float = 2.0, room: str | None = None) -> bytes:
        self.counter += 1
        event = synth.egress_ended_event(event_id or f"EV_{self.counter}", room or self.room, files,
                                         int(time.time()) - 60, seconds, status)
        return json.dumps(event).encode()

    def deliver(self, body: bytes, token: str | None = None) -> int:
        path = Path.cwd() / f"body-{uuid.uuid4().hex[:8]}.json"
        path.write_bytes(body)
        return self.run(["handle-webhook", "--body", str(path), "--authorization",
                         sign(body) if token is None else token])


@pytest.fixture()
def lab(server_url, monkeypatch):
    return Lab(server_url, monkeypatch)


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
