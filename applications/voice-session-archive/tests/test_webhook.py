"""The egress_ended webhook: token verification, shape checks, idempotency and the session manifest."""
from __future__ import annotations

import hashlib
import json
import time

import jsonschema
import pytest

from helpers import LIVEKIT_KEY, LIVEKIT_SECRET, all_calls, sign, unsigned_token
from voice_session_archive import sessions, storage, webhook, webhook_token
from voice_session_archive.errors import TokenRejected

SCHEMA = "session-manifest.schema.json"


def archived(lab) -> tuple[bytes, str]:
    """Register a session, put its recording and return a valid egress_ended body and the key."""
    lab.register()
    key, size = lab.recording()
    return lab.body([(key, size)]), key


def assert_rejected_without_b2(lab, recorded, body: bytes, token: str) -> None:
    before = len(all_calls(recorded))
    assert lab.deliver(body, token) == 2
    assert len(all_calls(recorded)) == before, "a rejected webhook must not reach B2"
    assert lab.manifest_key not in lab.keys()


def test_valid_egress_ended_writes_session_manifest(lab):
    body, key = archived(lab)
    assert lab.deliver(body) == 0
    manifest = lab.read_json(lab.manifest_key)
    assert manifest["session_id"] == lab.session_id
    assert manifest["source"] == {"platform": "livekit", "capture": "native-s3-output"}
    assert [item["participant_id"] for item in manifest["participants"]] == ["caller_1", "agent_1"]
    assert manifest["consent_ids"] == ["consent_caller"]
    assert manifest["retention"] == {"class": "standard"}
    recording = manifest["recordings"][0]
    data = lab.admin.get_object(Bucket=lab.bucket, Key=key)["Body"].read()
    assert recording["path"] == "recording.wav"
    assert recording["size"] == len(data)
    assert recording["sha256"] == hashlib.sha256(data).hexdigest()
    assert recording["encoding_format"] == "audio/wav"
    assert manifest["evaluation"]["status"] == "pending"
    assert manifest["evaluation"]["eligible_for"] == []
    assert manifest["duration_seconds"] == pytest.approx(2.0)


def test_session_manifest_validates_against_schema(lab):
    body, _ = archived(lab)
    assert lab.deliver(body) == 0
    manifest = lab.read_json(lab.manifest_key)
    schema = sessions.load_schema(SCHEMA)
    jsonschema.Draft202012Validator(schema).validate(manifest)
    assert sessions.schema_problems(manifest, SCHEMA) == []


def test_manifest_written_last(lab, recorded):
    body, _ = archived(lab)
    assert lab.deliver(body) == 0
    puts = [path for name, path in all_calls(recorded) if name == "PutObject"]
    assert puts[-1].endswith(lab.manifest_key)


def test_invalid_signature_rejected(lab, recorded):
    body, _ = archived(lab)
    assert_rejected_without_b2(lab, recorded, body, sign(body, secret="not-the-livekit-secret"))


def test_wrong_issuer_rejected(lab, recorded):
    body, _ = archived(lab)
    assert_rejected_without_b2(lab, recorded, body, sign(body, iss="another-api-key"))


def test_tampered_body_rejected(lab, recorded):
    body, _ = archived(lab)
    token = sign(body)
    tampered = body.replace(b"session-0001", b"session-0002")
    assert tampered != body
    assert_rejected_without_b2(lab, recorded, tampered, token)


def test_expired_token_rejected(lab, recorded):
    body, _ = archived(lab)
    now = int(time.time())
    assert_rejected_without_b2(lab, recorded, body, sign(body, exp=now - 31 - 5, nbf=now - 600))


def test_expiry_allows_thirty_seconds_of_leeway():
    body = b'{"event": "room_started", "id": "EV_leeway"}'
    now = time.time()
    token = sign(body, exp=int(now) - 20)
    assert webhook_token.verify(token, body, LIVEKIT_KEY, LIVEKIT_SECRET, now=now)["iss"] == LIVEKIT_KEY
    with pytest.raises(TokenRejected):
        webhook_token.verify(token, body, LIVEKIT_KEY, LIVEKIT_SECRET, now=now + 15)


def test_token_not_valid_before_nbf():
    body = b'{"event": "room_started", "id": "EV_nbf"}'
    now = time.time()
    with pytest.raises(TokenRejected):
        webhook_token.verify(sign(body, nbf=int(now) + 120), body, LIVEKIT_KEY, LIVEKIT_SECRET, now=now)


def test_alg_none_rejected(lab, recorded):
    body, _ = archived(lab)
    assert_rejected_without_b2(lab, recorded, body, unsigned_token(body))


def test_other_hmac_algorithms_rejected():
    body = b'{"event": "room_started", "id": "EV_alg"}'
    for algorithm in ("HS384", "HS512"):
        with pytest.raises(TokenRejected):
            webhook_token.verify(sign(body, algorithm=algorithm), body, LIVEKIT_KEY, LIVEKIT_SECRET)


def test_token_without_exp_rejected(lab, recorded):
    body, _ = archived(lab)
    assert_rejected_without_b2(lab, recorded, body, sign(body, exp=None))


def test_token_without_sha256_rejected():
    body = b'{"event": "room_started", "id": "EV_hash"}'
    with pytest.raises(TokenRejected):
        webhook_token.verify(sign(body, sha256=None), body, LIVEKIT_KEY, LIVEKIT_SECRET)


def test_bearer_prefix_is_accepted():
    body = b'{"event": "room_started", "id": "EV_bearer"}'
    assert webhook_token.verify("Bearer " + sign(body), body, LIVEKIT_KEY, LIVEKIT_SECRET)


def test_missing_authorization_rejected(lab, recorded):
    body, _ = archived(lab)
    assert_rejected_without_b2(lab, recorded, body, "")


def test_oversized_body_rejected(lab, recorded):
    lab.register()
    key, size = lab.recording()
    event = json.loads(lab.body([(key, size)]))
    event["padding"] = "x" * (webhook.MAX_BODY_BYTES + 1)
    body = json.dumps(event).encode()
    assert_rejected_without_b2(lab, recorded, body, sign(body))


def test_recording_key_outside_prefix_rejected(lab, recorded):
    lab.register()
    _, size = lab.recording()
    for key in ("work/candidates/recording.wav", "incoming/contributions/b1/clip.wav",
                "incoming/sessions/other-room/session-0001/recording.wav",
                "incoming/sessions/support-room/../../work/x.wav"):
        body = lab.body([(key, size)])
        assert_rejected_without_b2(lab, recorded, body, sign(body))


def test_repeated_event_id_processed_once(lab, recorded):
    body, _ = archived(lab)
    assert lab.deliver(body) == 0
    before = len(all_calls(recorded))
    assert lab.deliver(body) == 0
    assert len(all_calls(recorded)) == before, "a repeated event id must not be processed again"
    assert (lab.read_json(lab.manifest_key))["evaluation"]["status"] == "pending"


def test_other_events_ignored(lab, recorded, capsys):
    lab.register()
    before = len(all_calls(recorded))
    for name in ("room_started", "egress_started", "egress_updated", "participant_joined"):
        body = json.dumps({"event": name, "id": f"EV_{name}", "createdAt": int(time.time())}).encode()
        capsys.readouterr()
        assert lab.deliver(body) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "ignored"
    assert len(all_calls(recorded)) == before, "ignored events must not reach B2"
    assert lab.manifest_key not in lab.keys()


def test_failed_egress_writes_no_manifest(lab, capsys):
    lab.register()
    key, size = lab.recording()
    for status in ("EGRESS_FAILED", "EGRESS_ABORTED", "EGRESS_LIMIT_REACHED"):
        capsys.readouterr()
        assert lab.deliver(lab.body([(key, size)], status=status)) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "ignored"
    assert lab.manifest_key not in lab.keys()


def test_missing_recording_writes_no_manifest(lab):
    lab.register()
    key, size = lab.recording(upload=False)
    body = lab.body([(key, size)])
    assert lab.deliver(body) == 1
    assert lab.manifest_key not in lab.keys()
    # The claim was released: once the recording is there, LiveKit's retry archives it.
    lab.recording()
    assert lab.deliver(body) == 0
    assert lab.manifest_key in lab.keys()


def test_size_mismatch_writes_no_manifest(lab):
    lab.register()
    key, size = lab.recording()
    assert lab.deliver(lab.body([(key, size + 1)])) == 1
    assert lab.manifest_key not in lab.keys()


def test_unregistered_session_writes_no_manifest(lab):
    key, size = lab.recording()
    assert lab.deliver(lab.body([(key, size)])) == 1
    assert lab.manifest_key not in lab.keys()


def test_manifest_is_never_replaced(lab):
    body, key = archived(lab)
    assert lab.deliver(body) == 0
    first = lab.read_json(lab.manifest_key)
    assert lab.deliver(lab.body([(key, lab.recording()[1])])) == 1
    assert lab.read_json(lab.manifest_key) == first


def test_numeric_fields_and_status_number_are_accepted(lab):
    lab.register()
    key, size = lab.recording()
    event = json.loads(lab.body([(key, size)]))
    info = event["egressInfo"]
    info["status"] = 3
    for item in [info, *info["fileResults"]]:
        for name in ("startedAt", "endedAt", "duration", "size"):
            if name in item:
                item[name] = int(item[name])
    body = json.dumps(event).encode()
    assert lab.deliver(body) == 0
    assert storage.manifest_key(lab.room, lab.session_id) in lab.keys()
