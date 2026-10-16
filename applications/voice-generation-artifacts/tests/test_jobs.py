"""Submit: the job manifest, the candidates prefix, per-speaker stems and the checks before any write."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import jsonschema
import pytest

from voice_generation_artifacts import contracts, jobs, storage
from voice_generation_artifacts.errors import Conflict, Rejected


def schema_errors(name: str, document) -> list[str]:
    schema = json.loads((contracts.SCHEMA_DIR / name).read_text())
    return [error.message for error in jsonschema.Draft202012Validator(schema).iter_errors(document)]


def test_job_manifest_validates_against_schema(lab):
    lab.source(seconds=2.0)
    summary = lab.submit(languages=("es", "fr"), consent_ids=("consent_spk_a",))
    manifest = lab.read_json(summary["manifest"])

    assert schema_errors(contracts.JOB_SCHEMA, manifest) == []
    assert manifest["provider"] == {"name": "placeholder-tone-v1", "mode": "placeholder"}
    assert manifest["status"] == "succeeded" and manifest["review"] == {"status": "pending"}
    assert manifest["input"]["key"] == "incoming/source/src-1.wav"
    assert manifest["input"]["sha256"] == hashlib.sha256(lab.read("incoming/source/src-1.wav")).hexdigest()
    assert manifest["input"]["delivery"] == {"method": "presigned-get", "expires_in_seconds": jobs.DEFAULT_SOURCE_EXPIRES}
    assert manifest["consent_ids"] == ["consent_spk_a"]
    for output in manifest["outputs"]:
        data = lab.read(output["key"])
        assert output["size"] == len(data) and output["sha256"] == hashlib.sha256(data).hexdigest()
    # The schema rejects a manifest that is missing a required part or promoted without approval.
    broken = dict(manifest)
    del broken["outputs"]
    assert schema_errors(contracts.JOB_SCHEMA, broken)
    promoted = dict(manifest, promotion={"release_id": "a@b", "version": "b", "lineage": {
        "job_id": "job-1", "input_sha256": "0" * 64, "output_sha256": ["1" * 64]}})
    assert schema_errors(contracts.JOB_SCHEMA, promoted)


def test_manifest_holds_no_presigned_url(lab):
    lab.source()
    summary = lab.submit()
    text = lab.read(summary["manifest"]).decode()
    assert "X-Amz" not in text and "http" not in text


def test_candidates_written_under_candidates_prefix(lab):
    lab.source()
    lab.recorder.clear()
    summary = lab.submit(job_id="job-7", attempt="a2", languages=("es", "fr"))
    prefix = "work/candidates/job-7/a2/"

    written = lab.recorder.paths("PutObject")
    assert written and all(path.startswith(prefix) for path in written), written
    assert written[-1] == prefix + "job-manifest.json"  # the manifest is written last
    assert sorted(lab.keys(prefix)) == sorted(written)
    assert summary["manifest"] == prefix + "job-manifest.json"
    # Nothing but the source exists elsewhere in the bucket.
    assert lab.keys("releases/") == [] and lab.keys("protected/") == []
    # Expiry is recorded per output, from the candidate period.
    manifest = lab.read_json(summary["manifest"])
    created = datetime.strptime(manifest["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    for output in manifest["outputs"]:
        expires = datetime.strptime(output["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert (expires - created).days == jobs.DEFAULT_CANDIDATE_DAYS


def test_stems_per_speaker(lab):
    lab.source()
    speakers = ("spk_a", "spk_b", "spk_c")
    summary = lab.submit(languages=("es", "fr"), speakers=speakers)
    manifest = lab.read_json(summary["manifest"])
    prefix = "work/candidates/job-1/a1/"

    stems = {(item["language"], item["speaker_id"]): item for item in manifest["outputs"] if item["kind"] == "stem"}
    assert set(stems) == {(language, speaker) for language in ("es", "fr") for speaker in speakers}
    for (language, speaker), item in stems.items():
        assert item["key"] == f"{prefix}{language}/{speaker}.wav"
        assert lab.read(item["key"])
    mixes = [item for item in manifest["outputs"] if item["kind"] == "candidate"]
    assert sorted(item["key"] for item in mixes) == [f"{prefix}es/mix.wav", f"{prefix}fr/mix.wav"]
    assert len({item["sha256"] for item in manifest["outputs"]}) == len(manifest["outputs"])
    assert manifest["parameters"]["speakers"] == list(speakers)


def test_an_attempt_is_written_once(lab):
    lab.source()
    lab.submit()
    before = lab.read("work/candidates/job-1/a1/job-manifest.json")
    with pytest.raises(Conflict):
        lab.submit()
    assert lab.read("work/candidates/job-1/a1/job-manifest.json") == before
    lab.submit(attempt="a2")  # a new attempt is a new attempt id
    assert lab.keys("work/candidates/job-1/a2/")


@pytest.mark.parametrize("fields", [
    {"job_id": "../escape"}, {"job_id": "UPPER"}, {"attempt": "a/1"}, {"languages": ()}, {"languages": ("es", "es")},
    {"languages": ("not a language",)}, {"speakers": ()}, {"speakers": ("Spk A",)}, {"kind": "transcription"},
    {"source_expires": 0}, {"source_expires": 604801}, {"candidate_days": 0}, {"consent_ids": ("bad id",)},
])
def test_invalid_input_is_rejected_before_any_request(lab, fields):
    lab.source()
    lab.recorder.clear()
    with pytest.raises(Rejected):
        lab.submit(**fields)
    assert lab.recorder.calls == []


def test_missing_or_unusable_source_is_rejected_without_writing(lab):
    with pytest.raises(Rejected):
        lab.submit(source_id="no-such-source")
    storage.put_bytes(lab.admin, lab.bucket, storage.source_key("bad"), b"not audio", "audio/wav")
    with pytest.raises(Rejected):
        lab.submit(source_id="bad")
    assert lab.keys("work/") == []
