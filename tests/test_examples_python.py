"""Tests for examples/python against a local S3-compatible server (moto).

No B2 account or credentials are used: settings come from a dictionary and the
client points at the local server through `endpoint_url`.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest
from botocore.exceptions import EndpointConnectionError

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "examples" / "python"))

import b2_client  # noqa: E402
import egress_model  # noqa: E402
import multipart  # noqa: E402
import notifications  # noqa: E402

MIB = 1024 * 1024
SETTINGS = {
    "B2_APPLICATION_KEY_ID": "test-key-id",
    "B2_APPLICATION_KEY": "test-key-secret",
    "B2_REGION": "us-west-004",
}


@pytest.fixture(scope="module")
def server_url():
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture()
def client(server_url):
    return b2_client.make_client(SETTINGS, endpoint_url=server_url)


@pytest.fixture()
def bucket(client, request):
    name = "test-" + request.node.name.lower().replace("_", "-")[:50]
    client.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": SETTINGS["B2_REGION"]})
    client.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
    return name


class Interrupted(Exception):
    pass


def count_part_uploads(client, fail_on_part=None):
    """Record UploadPart calls; raise Interrupted when `fail_on_part` is uploaded."""
    uploaded = []

    def hook(params, **kwargs):
        number = int(params["query_string"]["partNumber"])
        uploaded.append(number)
        if number == fail_on_part:
            raise Interrupted()

    client.meta.events.register("before-call.s3.UploadPart", hook)
    return uploaded, hook


@pytest.fixture()
def big_file(tmp_path):
    path = tmp_path / "session.wav"
    path.write_bytes(bytes(range(256)) * (11 * MIB // 256))  # 11 MiB: parts of 5, 5 and 1 MiB
    return path


# Client configuration


def test_endpoint_is_derived_from_region():
    assert b2_client.endpoint_for_region("us-west-004") == "https://s3.us-west-004.backblazeb2.com"
    assert b2_client.make_client(SETTINGS).meta.endpoint_url == "https://s3.us-west-004.backblazeb2.com"


def test_invalid_region_is_rejected():
    with pytest.raises(ValueError):
        b2_client.endpoint_for_region("https://evil.example.com/")


def test_missing_settings_fail_with_the_variable_name():
    with pytest.raises(RuntimeError, match="B2_APPLICATION_KEY_ID"):
        b2_client.make_client({"B2_REGION": "us-west-004"})


def test_zone_bucket_reads_the_zone_variable():
    assert b2_client.zone_bucket("incoming", {"B2_INCOMING_BUCKET": "voice-incoming"}) == "voice-incoming"
    with pytest.raises(ValueError):
        b2_client.zone_bucket("unknown", {})


def test_user_agent_is_sent(client, bucket):
    seen = []
    client.meta.events.register("before-send.s3.*", lambda request, **kw: seen.append(request.headers["User-Agent"]))
    client.put_object(Bucket=bucket, Key="contributions/a.wav", Body=b"x")
    assert seen, "no request was sent"
    for agent in seen:
        text = agent.decode() if isinstance(agent, bytes) else agent
        assert "b2-voice-ai-reference-architectures" in text


# Presigned URLs and versions


def test_presigned_put_then_get(client, bucket):
    put_url = b2_client.presign_put(client, bucket, "contributions/clip.wav", content_type="audio/wav")
    request = urllib.request.Request(put_url, data=b"RIFF-data", method="PUT", headers={"Content-Type": "audio/wav"})
    assert urllib.request.urlopen(request).status == 200
    get_url = b2_client.presign_get(client, bucket, "contributions/clip.wav")
    assert urllib.request.urlopen(get_url).read() == b"RIFF-data"


def test_versions_are_listed_and_readable(client, bucket):
    client.put_object(Bucket=bucket, Key="datasets/v/manifest.json", Body=b"one")
    client.put_object(Bucket=bucket, Key="datasets/v/manifest.json", Body=b"two")
    versions = b2_client.list_versions(client, bucket, "datasets/")
    assert len(versions) == 2
    assert [v["is_latest"] for v in versions].count(True) == 1
    contents = {b2_client.read_version(client, bucket, v["key"], v["version_id"]) for v in versions}
    assert contents == {b"one", b"two"}
    older = next(v for v in versions if not v["is_latest"])
    url = b2_client.presign_get(client, bucket, older["key"], version_id=older["version_id"])
    assert urllib.request.urlopen(url).read() == b2_client.read_version(client, bucket, older["key"], older["version_id"])
    b2_client.delete_version(client, bucket, older["key"], older["version_id"])
    assert len(b2_client.list_versions(client, bucket, "datasets/")) == 1


# Multipart


def test_multipart_upload_round_trip(client, bucket, big_file):
    multipart.upload_file(client, bucket, "sessions/s1.wav", big_file)
    body = client.get_object(Bucket=bucket, Key="sessions/s1.wav")["Body"].read()
    assert body == big_file.read_bytes()


def test_multipart_resumes_after_interruption(client, bucket, big_file):
    key = "sessions/resume.wav"
    uploaded, hook = count_part_uploads(client, fail_on_part=2)
    with pytest.raises(Interrupted):
        multipart.upload_file(client, bucket, key, big_file)
    assert uploaded == [1, 2]
    upload_id = multipart.find_upload(client, bucket, key)
    assert upload_id is not None, "the unfinished upload must stay available for resume"
    assert set(multipart.stored_parts(client, bucket, key, upload_id)) == {1}

    client.meta.events.unregister("before-call.s3.UploadPart", hook)
    resumed, _ = count_part_uploads(client)
    multipart.upload_file(client, bucket, key, big_file)
    assert resumed == [2, 3], "only the missing parts are uploaded"
    assert client.get_object(Bucket=bucket, Key=key)["Body"].read() == big_file.read_bytes()
    assert multipart.find_upload(client, bucket, key) is None


def test_multipart_reuploads_a_part_that_does_not_match_the_file(client, bucket, big_file):
    key = "sessions/changed.wav"
    upload_id = client.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
    client.upload_part(Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=1, Body=b"z" * (5 * MIB))
    uploaded, _ = count_part_uploads(client)
    multipart.upload_file(client, bucket, key, big_file)
    assert uploaded == [1, 2, 3]
    assert client.get_object(Bucket=bucket, Key=key)["Body"].read() == big_file.read_bytes()


def test_multipart_retries_a_transient_failure(client, bucket, big_file):
    failures = []

    def flaky(params, **kwargs):
        if not failures:
            failures.append(1)
            raise EndpointConnectionError(endpoint_url="http://localhost")

    client.meta.events.register("before-call.s3.UploadPart", flaky)
    delays = []
    multipart.upload_file(client, bucket, "sessions/flaky.wav", big_file, sleep=delays.append)
    assert failures == [1] and len(delays) == 1
    assert client.get_object(Bucket=bucket, Key="sessions/flaky.wav")["Body"].read() == big_file.read_bytes()


def test_multipart_keeps_the_upload_when_retries_run_out(client, bucket, big_file):
    def always_down(params, **kwargs):
        raise EndpointConnectionError(endpoint_url="http://localhost")

    client.meta.events.register("before-call.s3.UploadPart", always_down)
    with pytest.raises(multipart.UploadFailed) as raised:
        multipart.upload_file(client, bucket, "sessions/down.wav", big_file, attempts=3, sleep=lambda s: None)
    assert raised.value.upload_id == multipart.find_upload(client, bucket, "sessions/down.wav")


def test_multipart_abort(client, bucket, big_file):
    key = "sessions/abort.wav"

    def always_down(params, **kwargs):
        raise EndpointConnectionError(endpoint_url="http://localhost")

    client.meta.events.register("before-call.s3.UploadPart", always_down)
    with pytest.raises(multipart.UploadFailed) as raised:
        multipart.upload_file(client, bucket, key, big_file, attempts=2, abort_on_failure=True, sleep=lambda s: None)
    assert raised.value.upload_id is None
    assert multipart.find_upload(client, bucket, key) is None

    upload_id = client.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
    multipart.abort_upload(client, bucket, key, upload_id)
    assert multipart.find_upload(client, bucket, key) is None


def test_part_size_below_minimum_is_rejected(client, bucket, big_file):
    with pytest.raises(ValueError):
        multipart.upload_file(client, bucket, "sessions/x.wav", big_file, part_size=MIB)


# Event Notifications

SECRET = "unit-test-signing-secret"


def event_body(*event_ids):
    events = [{"eventId": event_id, "eventType": "b2:ObjectCreated:Upload", "bucketName": "voice-incoming",
               "objectName": f"contributions/{event_id}.wav"} for event_id in event_ids]
    return json.dumps({"events": events}).encode()


def test_signature_header_name_and_format():
    assert notifications.SIGNATURE_HEADER == "X-Bz-Event-Notification-Signature"
    assert notifications.sign(SECRET, b"{}").startswith("v1=")


def test_signature_verification():
    body = event_body("e1")
    good = notifications.sign(SECRET, body)
    assert notifications.verify_signature(SECRET, body, good)
    assert not notifications.verify_signature(SECRET, body + b" ", good)
    assert not notifications.verify_signature("another-secret", body, good)
    assert not notifications.verify_signature(SECRET, body, None)
    assert not notifications.verify_signature(SECRET, body, good.replace("v1=", "v2="))


def test_invalid_signature_is_rejected_without_processing():
    handled = []
    processor = notifications.EventProcessor(SECRET, notifications.EventStore(), handled.append)
    status, _ = processor.handle_request(event_body("e1"), "v1=" + "0" * 64)
    assert status == 401 and handled == []


def test_repeated_event_is_processed_once():
    handled = []
    processor = notifications.EventProcessor(SECRET, notifications.EventStore(), handled.append)
    body = event_body("e1")
    header = notifications.sign(SECRET, body)
    first = processor.handle_request(body, header)
    second = processor.handle_request(body, header)
    assert first == (200, {"processed": 1, "duplicates": 0})
    assert second == (200, {"processed": 0, "duplicates": 1})
    assert [event["eventId"] for event in handled] == ["e1"]


def test_repeated_id_inside_one_delivery_and_across_batches():
    handled = []
    processor = notifications.EventProcessor(SECRET, notifications.EventStore(), handled.append)
    for body in (event_body("e1", "e2", "e1"), event_body("e2", "e3")):
        assert processor.handle_request(body, notifications.sign(SECRET, body))[0] == 200
    assert [event["eventId"] for event in handled] == ["e1", "e2", "e3"]


def test_failed_handler_releases_the_event_for_redelivery():
    calls = []

    def handler(event):
        calls.append(event["eventId"])
        if len(calls) == 1:
            raise RuntimeError("queue unavailable")

    processor = notifications.EventProcessor(SECRET, notifications.EventStore(), handler)
    body = event_body("e1")
    header = notifications.sign(SECRET, body)
    with pytest.raises(RuntimeError):
        processor.handle_request(body, header)
    assert processor.handle_request(body, header) == (200, {"processed": 1, "duplicates": 0})
    assert calls == ["e1", "e1"]


def test_malformed_body_with_valid_signature_is_a_client_error():
    processor = notifications.EventProcessor(SECRET, notifications.EventStore(), lambda event: None)
    body = b"not json"
    assert processor.handle_request(body, notifications.sign(SECRET, body))[0] == 400


# Egress model


def test_egress_model_fails_without_an_allowance():
    with pytest.raises(TypeError):
        egress_model.estimate_egress(volume_gb=100, reads=3)
    with pytest.raises(SystemExit):
        egress_model.main(["--volume-gb", "100", "--reads", "3"])


def test_egress_model_fails_without_volume_or_reads():
    with pytest.raises(TypeError):
        egress_model.estimate_egress(reads=3, allowance_gb=10)
    with pytest.raises(TypeError):
        egress_model.estimate_egress(volume_gb=100, allowance_gb=10)


def test_egress_model_uses_the_allowance_it_is_given():
    below = egress_model.estimate_egress(volume_gb=10, reads=2, allowance_gb=50)
    above = egress_model.estimate_egress(volume_gb=10, reads=8, allowance_gb=50)
    assert (below.downloaded_gb, below.billable_gb) == (20, 0)
    assert (above.downloaded_gb, above.billable_gb) == (80, 30)
    assert egress_model.estimate_cost(above, price_per_gb=2) == 60
    assert egress_model.PRICING_URL in egress_model.__doc__


def test_egress_model_rejects_negative_inputs():
    with pytest.raises(ValueError):
        egress_model.estimate_egress(volume_gb=-1, reads=1, allowance_gb=0)


def test_egress_model_file_has_no_money_amounts():
    text = (REPO / "examples" / "python" / "egress_model.py").read_text()
    assert "$" not in text
    assert egress_model.PRICING_URL in text
