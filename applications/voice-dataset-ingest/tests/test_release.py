"""Release: per-object Object Lock retention, consent checked again for every clip, a version written once."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from helpers import Recorder
from voice_dataset_ingest import cli, consent, processing, release, storage
from voice_dataset_ingest.errors import ConfigError, Conflict, Rejected

PREFIX = "releases/datasets/demo/v1/"


def processed_batch(lab, speakers=("spk_a", "spk_a", "spk_b")) -> list[str]:
    """Upload and process a batch with one clip per entry; returns the clip ids."""
    ids = []
    seen: set[str] = set()
    for position, speaker in enumerate(speakers):
        consent_id = f"consent_{speaker}"
        if speaker not in seen:
            lab.consent(speaker, consent_id)
            seen.add(speaker)
        clip_id = f"utt_{position}"
        lab.clip("b1", clip_id, speaker, consent_id, seed=position + 1)
        ids.append(clip_id)
    processing.process_batch(lab.client, lab.bucket, "b1")
    return ids


def run_release(lab, version="v1", days=30, extra=()) -> int:
    return cli.main(["release", "--batch", "b1", "--dataset", "demo", "--dataset-version", version,
                     "--retain-days", str(days), *extra], endpoint_url=lab.server_url)


def test_release_sets_per_object_retention(lab, capsys):
    processed_batch(lab)
    started = datetime.now(timezone.utc)
    assert run_release(lab, days=30) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "GOVERNANCE" and summary["clips"] == 3

    keys = lab.keys(PREFIX)
    assert sorted(key.removeprefix(PREFIX) for key in keys) == [
        "audio/utt_0.wav", "audio/utt_1.wav", "audio/utt_2.wav", "manifests/manifest.jsonl", "release-manifest.json"]
    for key in keys:
        head = lab.admin.head_object(Bucket=lab.bucket, Key=key)
        assert head["ObjectLockMode"] == "GOVERNANCE", key
        wanted = started + timedelta(days=30)
        assert abs(head["ObjectLockRetainUntilDate"] - wanted) < timedelta(minutes=5), key
    # Retention is per object: the bucket has no default retention rule.
    configuration = lab.admin.get_object_lock_configuration(Bucket=lab.bucket)["ObjectLockConfiguration"]
    assert configuration["ObjectLockEnabled"] == "Enabled" and "Rule" not in configuration
    # A locked version cannot be deleted before it expires.
    version_id = lab.admin.head_object(Bucket=lab.bucket, Key=keys[0])["VersionId"]
    with pytest.raises(Exception):
        lab.admin.delete_object(Bucket=lab.bucket, Key=keys[0], VersionId=version_id)


def test_release_manifest_written_last(lab):
    processed_batch(lab)
    recorder = Recorder(lab.client)
    release.release_version(lab.client, lab.bucket, "b1", "demo", "v1", 30)
    keys = [path.lstrip("/") for path in recorder.put_keys()]
    assert keys[-1] == PREFIX + "release-manifest.json"
    assert PREFIX + "manifests/manifest.jsonl" in keys[:-1]
    assert sum(key.startswith(PREFIX + "audio/") for key in keys[:-1]) == 3

    document = lab.read_json(PREFIX + "release-manifest.json")
    validator = consent.schema_validator("release-manifest.schema.json")
    assert list(validator.iter_errors(document)) == []
    assert document["id"] == "demo@v1"
    for item in document["files"]:
        assert hashlib.sha256(lab.read(PREFIX + item["path"])).hexdigest() == item["sha256"]
        assert len(lab.read(PREFIX + item["path"])) == item["size"]
    text = "".join(f"{item['sha256']}  {item['path']}\n" for item in sorted(document["files"], key=lambda i: i["path"]))
    assert document["integrity"]["content_fingerprint"] == hashlib.sha256(text.encode()).hexdigest()
    assert document["provenance"]["source_type"] == "synthetic"


def test_release_rechecks_consent_for_every_clip(lab, capsys):
    processed_batch(lab)  # utt_0 and utt_1 under one consent, utt_2 under another
    recorder = Recorder(lab.client)
    release.release_version(lab.client, lab.bucket, "b1", "demo", "v1", 30)
    reads = [path for name, path in recorder.calls if name == "GetObject" and "protected/consents/" in path]
    assert len(reads) == 3  # one read per clip, even for clips that share a consent record

    lab.consent("spk_b", "consent_spk_b", status="revoked", revoked_at="2026-03-01T00:00:00Z")
    assert run_release(lab, version="v2") == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["clips"] == 2
    assert summary["excluded"] == [{"audio_filepath": "audio/utt_2.wav", "reason": "consent_revoked"}]
    assert sorted(key.removeprefix("releases/datasets/demo/v2/") for key in lab.keys("releases/datasets/demo/v2/")) == [
        "audio/utt_0.wav", "audio/utt_1.wav", "manifests/manifest.jsonl", "release-manifest.json"]

    lab.consent("spk_a", "consent_spk_a", status="revoked", revoked_at="2026-03-01T00:00:00Z")
    assert run_release(lab, version="v3") == 2  # no clip is left: rejected
    assert lab.keys("releases/datasets/demo/v3/") == []


def test_release_refuses_bucket_without_object_lock(plain_lab, capsys):
    processed_batch(plain_lab)
    assert run_release(plain_lab) == 1
    assert "examples/object-lock.json" in capsys.readouterr().err
    assert plain_lab.keys("releases/") == []
    with pytest.raises(ConfigError):
        release.release_version(plain_lab.client, plain_lab.bucket, "b1", "demo", "v1", 30)


def test_release_refuses_existing_version(lab, capsys):
    processed_batch(lab)
    assert run_release(lab) == 0
    capsys.readouterr()
    before = lab.admin.list_object_versions(Bucket=lab.bucket, Prefix=PREFIX)["Versions"]
    assert run_release(lab) == 1
    assert "already exists" in capsys.readouterr().err
    with pytest.raises(Conflict):
        release.release_version(lab.client, lab.bucket, "b1", "demo", "v1", 30)
    after = lab.admin.list_object_versions(Bucket=lab.bucket, Prefix=PREFIX)["Versions"]
    assert [item["VersionId"] for item in after] == [item["VersionId"] for item in before]


def test_release_needs_a_processed_batch(lab):
    with pytest.raises(Rejected):
        release.release_version(lab.client, lab.bucket, "never-processed", "demo", "v1", 30)


def test_retain_days_is_capped(lab, capsys):
    processed_batch(lab)
    recorder = Recorder(lab.client)
    for bad in (3651, 0, -5):
        with pytest.raises(Rejected):
            release.check_retain_days(bad)
    with pytest.raises(Rejected):
        release.check_retain_days(True)
    assert release.check_retain_days(3650) == 3650
    assert release.check_retain_days(3651, allow_long=True) == 3651
    assert recorder.calls == []

    assert run_release(lab, days=3651) == 2  # rejected before anything is written
    assert lab.keys("releases/") == []
    assert run_release(lab, days=0) == 2
    capsys.readouterr()
    assert run_release(lab, version="v9", days=3651, extra=["--allow-long-retention"]) == 0
    head = lab.admin.head_object(Bucket=lab.bucket, Key="releases/datasets/demo/v9/release-manifest.json")
    wanted = datetime.now(timezone.utc) + timedelta(days=3651)
    assert abs(head["ObjectLockRetainUntilDate"] - wanted) < timedelta(minutes=5)
