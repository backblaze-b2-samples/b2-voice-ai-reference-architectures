"""Consent gate: audio is accepted only under an active consent that permits training."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_dataset_ingest import cli, processing, synth

UPLOAD = ["upload", "--batch", "b1", "--speaker", "spk_t", "--consent-id", "consent_t", "--text", "hello there"]


def upload_args(path: Path) -> list[str]:
    return UPLOAD + ["--file", str(path)]


def assert_consent_gate(lab, tmp_path, reason: str) -> None:
    """The upload is refused (exit 2, nothing written) and, if the clip is already in B2, process rejects it."""
    clip = tmp_path / "utt_t.wav"
    synth.write_clip(clip, "valid", 3)
    assert cli.main(upload_args(clip), endpoint_url=lab.server_url) == 2
    assert lab.keys("incoming/") == []

    lab.clip("b1", "utt_t", "spk_t", "consent_t")
    result = processing.process_batch(lab.client, lab.bucket, "b1")
    assert [item["status"] for item in result["clips"]] == ["rejected"]
    assert result["clips"][0]["reasons"] == [reason]
    assert lab.read_json("work/candidates/b1/rejected/utt_t.json")["reasons"] == [reason]
    assert lab.read("work/candidates/b1/manifest.jsonl") == b""
    assert lab.keys("work/candidates/b1/audio/") == []


def test_rejects_audio_without_consent(lab, tmp_path):
    assert_consent_gate(lab, tmp_path, "consent_missing")


def test_rejects_revoked_consent(lab, tmp_path):
    lab.consent("spk_t", "consent_t", status="revoked", revoked_at="2026-02-01T00:00:00Z")
    assert_consent_gate(lab, tmp_path, "consent_revoked")


@pytest.mark.parametrize("how", ["past-date", "expired-status"])
def test_rejects_expired_consent(lab, tmp_path, how):
    if how == "past-date":
        lab.consent("spk_t", "consent_t", expires_at=synth.PAST)
    else:
        lab.consent("spk_t", "consent_t", status="expired", expires_at=synth.PAST)
    assert_consent_gate(lab, tmp_path, "consent_expired")


def test_rejects_consent_without_training_use(lab, tmp_path):
    lab.consent("spk_t", "consent_t", permitted_uses=("evaluation", "analytics"))
    assert_consent_gate(lab, tmp_path, "consent_no_training_use")


def test_rejects_consent_of_another_speaker(lab, tmp_path):
    lab.consent("spk_other", "consent_t")
    assert_consent_gate(lab, tmp_path, "consent_speaker_mismatch")


def test_rejects_consent_that_fails_its_schema(lab, tmp_path):
    lab.admin.put_object(Bucket=lab.bucket, Key="protected/consents/consent_t.json",
                         Body=json.dumps({"consent_id": "consent_t", "status": "active"}).encode())
    assert_consent_gate(lab, tmp_path, "consent_invalid_record")


@pytest.mark.parametrize("setup", ["missing", "revoked", "expired", "no-training"])
def test_presign_refused_without_active_consent(lab, capsys, setup):
    if setup == "revoked":
        lab.consent("spk_t", "consent_t", status="revoked", revoked_at="2026-02-01T00:00:00Z")
    elif setup == "expired":
        lab.consent("spk_t", "consent_t", expires_at=synth.PAST)
    elif setup == "no-training":
        lab.consent("spk_t", "consent_t", permitted_uses=("evaluation",))
    code = cli.main(["presign-upload", "--batch", "b1", "--clip-id", "utt_t", "--speaker", "spk_t",
                     "--consent-id", "consent_t", "--text", "hello there"], endpoint_url=lab.server_url)
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""  # no URL was printed
    assert lab.keys("incoming/") == []  # and no sidecar was written
