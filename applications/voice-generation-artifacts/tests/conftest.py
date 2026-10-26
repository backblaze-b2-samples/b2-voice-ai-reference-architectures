"""Shared fixtures.

The tests run against a moto server on 127.0.0.1 (a real server is needed because presigned URLs are
fetched over HTTP). They use readable fake credentials and no B2 account. Every client the application
uses is built with `storage.make_client`; the bucket is created by a separate admin client, outside
the workflow under test.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from helpers import FAKE_ENV, Recorder
from voice_generation_artifacts import jobs, storage, synth
from voice_generation_artifacts.config import Settings
from voice_generation_artifacts.worker import PlaceholderWorker


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
    """A bucket, an admin client that sets up state, and the application's own recorded client."""

    def __init__(self, server_url: str, monkeypatch: pytest.MonkeyPatch):
        self.server_url = server_url
        self.bucket = f"generation-{uuid.uuid4().hex[:12]}"
        settings = Settings.from_mapping({**FAKE_ENV, "B2_BUCKET_NAME": self.bucket})
        self.admin = storage.make_client(settings, endpoint_url=server_url)
        self.admin.create_bucket(Bucket=self.bucket, CreateBucketConfiguration={"LocationConstraint": FAKE_ENV["B2_REGION"]})
        monkeypatch.setenv("B2_BUCKET_NAME", self.bucket)
        self.settings = Settings.from_environ()
        self.client = storage.make_client(self.settings, endpoint_url=server_url)
        self.recorder = Recorder(self.client)

    def keys(self, prefix: str = "") -> list[str]:
        return storage.list_keys(self.admin, self.bucket, prefix)

    def read(self, key: str) -> bytes:
        return storage.get_bytes(self.admin, self.bucket, key)

    def read_json(self, key: str) -> Any:
        return json.loads(self.read(key))

    def source(self, source_id: str = "src-1", seconds: float = 2.0, seed: int = 3) -> str:
        """Put a synthetic source into the incoming zone, as an earlier upload would have."""
        data = synth.make_source(seconds, seed)
        storage.put_bytes(self.admin, self.bucket, storage.source_key(source_id), data, "audio/wav")
        return source_id

    def consent(self, speaker_id: str, consent_id: str | None = None, **fields: Any) -> str:
        """Put a synthetic consent record into the protected zone, as a consent service would have."""
        consent_id = consent_id or f"consent-{speaker_id}"
        storage.put_json(self.admin, self.bucket, storage.consent_key(consent_id),
                         synth.consent_record(consent_id, speaker_id, **fields))
        return consent_id

    def submit(self, source_id: str = "src-1", job_id: str = "job-1", attempt: str = "a1",
               languages: tuple[str, ...] = ("es",), speakers: tuple[str, ...] = ("spk_a", "spk_b"), **fields: Any) -> dict:
        """Submit a job. Without `consent_ids`, an active consent is put for every valid speaker first."""
        if "consent_ids" not in fields:
            fields["consent_ids"] = tuple(self.consent(speaker) for speaker in speakers
                                          if storage.SPEAKER_PATTERN.match(speaker))
        return jobs.submit_job(self.client, self.bucket, PlaceholderWorker(), source_id=source_id, job_id=job_id,
                               attempt=attempt, target_languages=languages, speakers=speakers, **fields)


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
