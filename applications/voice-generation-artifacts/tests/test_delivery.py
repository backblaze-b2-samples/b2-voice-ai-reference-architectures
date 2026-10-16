"""Promote and deliver: server-side copy with lineage, a version written once, and bounded presigned URLs."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import jsonschema
import pytest
from botocore.exceptions import ClientError

from voice_generation_artifacts import contracts, delivery
from voice_generation_artifacts.errors import Conflict, Rejected

PREFIX = "releases/deliveries/job-1/v1/"


def schema_errors(name: str, document) -> list[str]:
    schema = json.loads((contracts.SCHEMA_DIR / name).read_text())
    return [error.message for error in jsonschema.Draft202012Validator(schema).iter_errors(document)]


def promote(lab, version: str = "v1", **fields):
    return delivery.promote_attempt(lab.client, lab.bucket, "job-1", "a1", version, **{"approved": True, **fields})


def deliver(lab, **fields):
    return delivery.deliver_version(lab.client, lab.bucket, "job-1", "v1", **fields)


def parsed(url: str) -> dict[str, str]:
    return {name: values[0] for name, values in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as response:
        return response.read()


def stored_delivery_manifest(lab, summary) -> dict:
    return lab.read_json(summary["delivery_manifest"])


def test_promote_copies_server_side_with_lineage(lab):
    source_id = lab.source(seconds=2.0)
    submitted = lab.submit(source_id=source_id, languages=("es", "fr"))
    candidate = lab.read_json(submitted["manifest"])
    lab.recorder.clear()

    summary = promote(lab)

    # Every output was copied inside B2: copy requests, no download of the audio, one manifest write.
    calls = lab.recorder.calls
    assert [name for name, _ in calls].count("CopyObject") == len(candidate["outputs"])
    assert all(path.lstrip("/") != item["key"] for name, path in calls if name == "GetObject" for item in candidate["outputs"])
    assert lab.recorder.paths("PutObject") == [PREFIX + "job-manifest.json"]
    assert summary["release"] == "job-1@v1" and summary["objects"] == len(candidate["outputs"])

    for item in candidate["outputs"]:
        name = item["key"].split("/a1/", 1)[1]
        assert lab.read(PREFIX + name) == lab.read(item["key"])  # identical bytes

    published = lab.read_json(PREFIX + "job-manifest.json")
    assert schema_errors(contracts.JOB_SCHEMA, published) == []
    assert published["review"]["status"] == "approved" and published["review"]["decided_at"]
    # Lineage: the source key and version, each candidate key, each SHA-256 and the worker that made them.
    assert published["input"]["key"] == "incoming/source/src-1.wav"
    assert published["input"]["sha256"] == hashlib.sha256(lab.read("incoming/source/src-1.wav")).hexdigest()
    assert [item["key"] for item in published["outputs"]] == [item["key"] for item in candidate["outputs"]]
    assert published["provider"] == {"name": "placeholder-tone-v1", "mode": "placeholder"}
    assert published["promotion"] == {
        "release_id": "job-1@v1", "version": "v1",
        "lineage": {"job_id": "job-1", "input_sha256": candidate["input"]["sha256"],
                    "output_sha256": sorted({item["sha256"] for item in candidate["outputs"]})}}
    # The candidates are still where they were: promotion copies, it does not move.
    assert lab.read(candidate["outputs"][0]["key"])


def test_promote_with_one_speaker_keeps_the_lineage_hashes_unique(lab):
    lab.source()
    lab.submit(speakers=("spk_a",))  # the mix equals the only stem, so two outputs share one SHA-256
    promote(lab)
    published = lab.read_json(PREFIX + "job-manifest.json")
    assert schema_errors(contracts.JOB_SCHEMA, published) == []
    assert len(published["promotion"]["lineage"]["output_sha256"]) == 1
    assert len(published["outputs"]) == 2


def test_promote_needs_an_explicit_approval(lab):
    lab.source()
    lab.submit()
    lab.recorder.clear()
    with pytest.raises(Rejected):
        promote(lab, approved=False)
    assert lab.recorder.calls == [] and lab.keys("releases/") == []
    with pytest.raises(Rejected):
        delivery.promote_attempt(lab.client, lab.bucket, "job-1", "a9", "v1", approved=True)  # no such attempt
    assert lab.keys("releases/") == []


def test_promote_refuses_existing_version(lab):
    lab.source()
    lab.submit()
    promote(lab)
    before = {key: lab.read(key) for key in lab.keys(PREFIX)}
    with pytest.raises(Conflict):
        promote(lab)
    assert {key: lab.read(key) for key in lab.keys(PREFIX)} == before

    # A second attempt does not overwrite the published version either.
    lab.submit(attempt="a2")
    with pytest.raises(Conflict):
        delivery.promote_attempt(lab.client, lab.bucket, "job-1", "a2", "v1", approved=True)
    assert {key: lab.read(key) for key in lab.keys(PREFIX)} == before
    delivery.promote_attempt(lab.client, lab.bucket, "job-1", "a2", "v2", approved=True)  # a change is a new version
    assert lab.keys("releases/deliveries/job-1/v2/")


def test_promote_refuses_a_candidate_that_changed_after_submission(lab):
    from voice_generation_artifacts import storage

    lab.source()
    summary = lab.submit()
    storage.put_bytes(lab.admin, lab.bucket, summary["outputs"][0]["key"], b"tampered", "audio/wav")
    with pytest.raises(Exception, match="size"):
        promote(lab)
    assert lab.keys("releases/") == []


def test_delivery_manifest_written_last(lab):
    lab.source()
    lab.submit()
    promote(lab)
    lab.recorder.clear()

    summary = deliver(lab)

    operations = lab.recorder.operations()
    assert operations.count("PutObject") == 1 and operations[-1] == "PutObject"  # the manifest, after every check
    assert operations.count("HeadObject") == len(lab.keys(PREFIX))
    manifest = stored_delivery_manifest(lab, summary)
    assert summary["delivery_manifest"].startswith("releases/delivery-manifests/job-1/v1/")
    assert schema_errors(contracts.DELIVERY_SCHEMA, manifest) == []
    assert manifest["release_id"] == "job-1@v1" and manifest["dataset"] == "job-1" and manifest["version"] == "v1"
    assert sorted(manifest["urls"]) == sorted(key[len(PREFIX):] for key in lab.keys(PREFIX))
    # The version itself is untouched by delivery.
    assert all(not key.startswith("releases/delivery-manifests/") for key in lab.keys(PREFIX))

    # If one object of the version is missing, nothing is signed or stored.
    lab.admin.delete_object(Bucket=lab.bucket, Key=PREFIX + "es/mix.wav")
    before = lab.keys("releases/delivery-manifests/")
    with pytest.raises(ClientError):
        deliver(lab)
    assert lab.keys("releases/delivery-manifests/") == before


def test_delivery_manifest_has_presigned_urls_with_bounded_ttl(lab, tmp_path):
    lab.source()
    lab.submit()
    promote(lab)

    summary = deliver(lab, expires=600, out=tmp_path / "delivery.json")
    manifest = stored_delivery_manifest(lab, summary)
    assert manifest["expires_in_seconds"] == 600
    issued = datetime.strptime(manifest["issued_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    expires = datetime.strptime(manifest["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert (expires - issued).total_seconds() == 600
    assert manifest["access"] == {"method": "GET", "credential_type": "presigned-url"}
    for name, url in manifest["urls"].items():
        query = parsed(url)
        assert query["X-Amz-Expires"] == "600" and "X-Amz-Signature" in query
        assert fetch(url) == lab.read(PREFIX + name)  # the URL works without credentials

    # The local copy is the same manifest, readable only by its owner; the summary shows no URL.
    assert json.loads((tmp_path / "delivery.json").read_text()) == manifest
    assert stat.S_IMODE(os.stat(tmp_path / "delivery.json").st_mode) == 0o600
    assert "X-Amz" not in json.dumps(summary)

    # The longest lifetime the schema and B2 allow is accepted; anything else is rejected before any request.
    longest = stored_delivery_manifest(lab, deliver(lab, expires=contracts.MAX_EXPIRES_SECONDS))
    assert parsed(next(iter(longest["urls"].values())))["X-Amz-Expires"] == str(contracts.MAX_EXPIRES_SECONDS)
    lab.recorder.clear()
    for bad in (0, -5, contracts.MAX_EXPIRES_SECONDS + 1, True, "3600", 3600.5):
        with pytest.raises(Rejected):
            deliver(lab, expires=bad)
    assert lab.recorder.calls == []


def test_delivery_default_ttl_is_one_hour_or_less(lab):
    from voice_generation_artifacts import cli

    assert contracts.DEFAULT_EXPIRES_SECONDS <= 3600
    arguments = cli.build_parser().parse_args(["deliver", "--job-id", "job-1", "--version", "v1"])
    assert arguments.expires <= 3600

    lab.source()
    lab.submit()
    promote(lab)
    manifest = stored_delivery_manifest(lab, deliver(lab))
    assert manifest["expires_in_seconds"] <= 3600
    assert {parsed(url)["X-Amz-Expires"] for url in manifest["urls"].values()} == {str(manifest["expires_in_seconds"])}


def test_deliver_needs_a_promoted_version(lab):
    lab.source()
    lab.submit()
    with pytest.raises(Rejected):
        deliver(lab)  # nothing promoted yet
    assert lab.keys("releases/") == []
    # A candidate manifest copied under a delivery prefix, without a promotion block, is not deliverable.
    lab.admin.copy_object(Bucket=lab.bucket, Key=PREFIX + "job-manifest.json",
                          CopySource={"Bucket": lab.bucket, "Key": "work/candidates/job-1/a1/job-manifest.json"})
    with pytest.raises(Rejected):
        deliver(lab)
