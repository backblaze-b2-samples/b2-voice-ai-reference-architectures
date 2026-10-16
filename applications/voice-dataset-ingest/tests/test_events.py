"""Event Notifications: signature checks happen before any B2 call; each event is processed once."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_dataset_ingest import cli, storage

from helpers import sign

CLIP = "incoming/contributions/b1/utt_t.wav"


def write_body(path: Path, body: bytes) -> str:
    path.write_bytes(body)
    return str(path)


def run(lab, body: bytes, signature: str, name: str = "body.json") -> int:
    path = Path.cwd() / name
    return cli.main(["handle-event", "--body", write_body(path, body), "--signature", signature],
                    endpoint_url=lab.server_url)


@pytest.fixture()
def no_b2(monkeypatch):
    """Any attempt to build a B2 client fails the test: a rejection must come first."""
    def refuse(*args, **kwargs):
        raise AssertionError("the application reached for B2 before rejecting the event")

    monkeypatch.setattr(storage, "make_client", refuse)


def test_invalid_event_signature_rejected(lab, no_b2):
    body = lab.event_body(CLIP)
    cases = {
        "wrong secret": (body, sign(body, "not-the-secret")),
        "garbled": (body, "v1=zz"),
        "empty": (body, ""),
        "no version prefix": (body, sign(body).removeprefix("v1=")),
        "altered after signing": (body.replace(b"utt_t", b"utt_x"), sign(body)),
    }
    for label, (data, signature) in cases.items():
        assert run(lab, data, signature) == 2, label
    assert not Path(".voice-dataset-ingest").exists()  # no event was claimed


def test_malformed_and_oversized_events_are_rejected(lab, no_b2):
    for label, body in {
        "not json": b"not json",
        "no events": json.dumps({"events": []}).encode(),
        "event without id": json.dumps({"events": [{"eventType": "b2:ObjectCreated:Upload",
                                                    "objectName": CLIP}]}).encode(),
        "other bucket": json.dumps({"events": [{"eventId": "e1", "eventType": "b2:ObjectCreated:Upload",
                                                "bucketName": "someone-elses-bucket", "objectName": CLIP}]}).encode(),
        "clip outside the layout": lab.event_body("incoming/contributions/b1/sub/dir/x.wav"),
        "larger than 1 MiB": b" " * (1024 * 1024 + 1),
    }.items():
        assert run(lab, body, sign(body)) == 2, label


def test_missing_signing_secret_is_a_configuration_error(lab, monkeypatch):
    monkeypatch.delenv("B2_EVENT_SIGNING_SECRET")
    body = lab.event_body(CLIP)
    assert run(lab, body, sign(body)) == 1


def test_repeated_event_processed_once(lab, recorded, capsys):
    lab.consent("spk_t", "consent_t")
    lab.clip("b1", "utt_t", "spk_t", "consent_t")
    body = lab.event_body(CLIP)

    assert run(lab, body, sign(body)) == 0
    first = json.loads(capsys.readouterr().out)
    assert (first["processed"], first["duplicates"]) == (1, 0)
    assert lab.keys("work/candidates/b1/audio/") == ["work/candidates/b1/audio/utt_t.wav"]
    assert len(lab.read("work/candidates/b1/manifest.jsonl").splitlines()) == 1
    assert recorded[0].calls  # the first delivery did work

    assert run(lab, body, sign(body)) == 0
    second = json.loads(capsys.readouterr().out)
    assert (second["processed"], second["duplicates"]) == (0, 1)
    second_run = [call for recorder in recorded[1:] for call in recorder.calls]
    assert second_run == []  # the repeat made no request to B2


def test_failed_event_can_be_retried(lab, capsys):
    lab.consent("spk_t", "consent_t")
    body = lab.event_body(CLIP)
    sidecar = {"batch": "b1", "clip_id": "utt_t", "speaker": "spk_t", "consent_id": "consent_t",
               "text": "hello", "lang": "en", "split": "train"}
    storage.put_json(lab.admin, lab.bucket, storage.sidecar_key("b1", "utt_t"), sidecar)
    assert run(lab, body, sign(body)) == 1  # the clip is not there yet: a B2 error, not a rejection
    capsys.readouterr()
    lab.clip("b1", "utt_t", "spk_t", "consent_t")
    assert run(lab, body, sign(body)) == 0
    assert json.loads(capsys.readouterr().out)["processed"] == 1


def test_other_events_are_ignored(lab, capsys):
    events = [
        {"eventId": "e1", "eventType": "b2:ObjectDeleted:Delete", "bucketName": lab.bucket, "objectName": CLIP},
        {"eventId": "e2", "eventType": "b2:ObjectCreated:Upload", "bucketName": lab.bucket,
         "objectName": "incoming/contributions/b1/utt_t.json"},
        {"eventId": "e3", "eventType": "b2:ObjectCreated:Upload", "bucketName": lab.bucket,
         "objectName": "work/candidates/b1/audio/utt_t.wav"},
    ]
    body = json.dumps({"events": events}).encode()
    assert run(lab, body, sign(body)) == 0
    assert json.loads(capsys.readouterr().out)["ignored"] == 3
    assert lab.keys() == []
