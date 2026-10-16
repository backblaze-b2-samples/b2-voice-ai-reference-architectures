"""Validation, normalization and the batch manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from helpers import Recorder
from voice_dataset_ingest import consent, processing, storage, synth
from voice_dataset_ingest.shared import audio_checks

MANIFEST = "work/candidates/b1/manifest.jsonl"


@pytest.mark.parametrize("kind,defect", [
    ("silent", "silent"),
    ("clipped", "clipped"),
    ("too_short", "too_short"),
    ("truncated", "truncated"),
    ("not_audio", "unreadable"),
])
def test_invalid_clip_rejected_with_reason(lab, kind, defect):
    lab.consent("spk_t", "consent_t")
    lab.clip("b1", "utt_bad", "spk_t", "consent_t", kind=kind)
    result = processing.process_batch(lab.client, lab.bucket, "b1")

    assert result["clips"][0]["status"] == "rejected"
    reasons = lab.read_json("work/candidates/b1/rejected/utt_bad.json")["reasons"]
    assert defect in reasons
    assert lab.read("work/candidates/b1/rejected/utt_bad.wav") == lab.read("incoming/contributions/b1/utt_bad.wav")
    assert lab.keys("work/candidates/b1/audio/") == []
    assert lab.read(MANIFEST) == b""
    report = lab.read_json("work/evaluations/validation/b1/report.json")
    assert (report["accepted"], report["rejected"]) == (0, 1)


def test_missing_sidecar_is_rejected(lab):
    lab.admin.put_object(Bucket=lab.bucket, Key="incoming/contributions/b1/utt_x.wav", Body=b"RIFF")
    result = processing.process_batch(lab.client, lab.bucket, "b1")
    assert result["clips"][0]["reasons"] == ["missing_sidecar"]


def test_normalizes_to_16k_mono(lab):
    lab.consent("spk_t", "consent_t")
    lab.clip("b1", "utt_hi", "spk_t", "consent_t", kind="stereo_48k")
    lab.clip("b1", "utt_ok", "spk_t", "consent_t", kind="valid")
    source = Path.cwd() / "utt_hi.wav"
    before = audio_checks.read_wav_info(source)
    assert (before.sample_rate, before.channels) == (48000, 2)

    result = processing.process_batch(lab.client, lab.bucket, "b1")
    by_clip = {item["clip_id"]: item for item in result["clips"]}
    assert by_clip["utt_hi"]["status"] == "accepted" and by_clip["utt_hi"]["normalized"] is True
    assert by_clip["utt_ok"]["status"] == "accepted" and by_clip["utt_ok"]["normalized"] is False

    for clip_id in ("utt_hi", "utt_ok"):
        out = Path.cwd() / f"out_{clip_id}.wav"
        out.write_bytes(lab.read(f"work/candidates/b1/audio/{clip_id}.wav"))
        info = audio_checks.read_wav_info(out)
        assert (info.sample_rate, info.channels, info.bits_per_sample) == (16000, 1, 16)
        assert audio_checks.clip_defects(out) == []
        assert info.duration_seconds == pytest.approx(1.5, abs=0.01)


def load_synthetic_batch(lab, workdir: Path) -> dict:
    """Put the deterministic synthetic dataset into the incoming zone, as earlier uploads would have."""
    plan = synth.build_dataset(workdir / "data")
    for item in plan["consents"]:
        document = json.loads((workdir / "data" / item["file"]).read_text())
        storage.put_json(lab.admin, lab.bucket, storage.consent_key(item["consent_id"]), document)
    for item in plan["clips"]:
        data = (workdir / "data" / item["file"]).read_bytes()
        sidecar = {"batch": "b1", "clip_id": item["clip_id"], "speaker": item["speaker"],
                   "consent_id": item["consent_id"], "text": item["text"], "lang": "en", "split": "train"}
        storage.put_json(lab.admin, lab.bucket, storage.sidecar_key("b1", item["clip_id"]), sidecar)
        storage.put_bytes(lab.admin, lab.bucket, storage.clip_key("b1", item["clip_id"]), data, "audio/wav")
    return plan


def test_manifest_lines_validate_against_schema(lab):
    plan = load_synthetic_batch(lab, Path.cwd())
    result = processing.process_batch(lab.client, lab.bucket, "b1")
    validator = consent.schema_validator("dataset-line.schema.json")

    lines = [json.loads(item) for item in lab.read(MANIFEST).decode().splitlines()]
    expected = [item["clip_id"] for item in plan["clips"] if item["expected"] == "accepted"]
    assert [Path(line["audio_filepath"]).stem for line in lines] == expected
    assert result["accepted"] == len(expected)
    for line in lines:
        assert list(validator.iter_errors(line)) == []
        audio = lab.read(f"work/candidates/b1/{line['audio_filepath']}")
        assert hashlib.sha256(audio).hexdigest() == line["sha256"]
        assert line["sample_rate"] == 16000 and line["consent_id"] == "consent_synth_spk_a"

    for item in plan["clips"]:
        if item["expected"] != "accepted":
            assert lab.read_json(f"work/candidates/b1/rejected/{item['clip_id']}.json")["reasons"], item["clip_id"]


def test_manifest_written_last(lab):
    load_synthetic_batch(lab, Path.cwd())
    recorder = Recorder(lab.client)
    processing.process_batch(lab.client, lab.bucket, "b1")
    keys = [path.lstrip("/") for path in recorder.put_keys()]  # the path starts with "/"
    assert keys[-1] == MANIFEST
    assert keys.count(MANIFEST) == 1
    assert any(key.startswith("work/candidates/b1/audio/") for key in keys[:-1])
    assert any(key.startswith("work/evaluations/validation/b1/") for key in keys[:-1])


def test_synthetic_data_is_deterministic(tmp_path):
    first = synth.build_dataset(tmp_path / "one", seed=7)
    second = synth.build_dataset(tmp_path / "two", seed=7)
    other = synth.build_dataset(tmp_path / "three", seed=8)
    assert first == second
    for item in first["clips"]:
        assert (tmp_path / "one" / item["file"]).read_bytes() == (tmp_path / "two" / item["file"]).read_bytes()
    assert (tmp_path / "one/clips/utt_0001.wav").read_bytes() != (tmp_path / "three/clips/utt_0001.wav").read_bytes()
    assert other["clips"][0]["expected"] == "accepted"


def test_reprocessing_a_clip_whose_consent_was_revoked_removes_it_from_the_manifest(lab):
    consent_id = lab.consent("spk_t", "consent_t")
    lab.clip("b1", "utt_one", "spk_t", consent_id)
    processing.process_batch(lab.client, lab.bucket, "b1")
    assert len(lab.read(MANIFEST).splitlines()) == 1
    lab.consent("spk_t", "consent_t", status="revoked", revoked_at="2026-03-01T00:00:00Z")
    processing.process_batch(lab.client, lab.bucket, "b1")
    assert lab.read(MANIFEST) == b""
