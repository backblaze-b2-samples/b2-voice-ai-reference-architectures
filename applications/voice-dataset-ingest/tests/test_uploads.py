"""Presigned PUT upload and resumable multipart upload, both behind the consent check."""
from __future__ import annotations

import json
import urllib.request

import pytest

from voice_dataset_ingest import cli, storage, synth, uploads
from voice_dataset_ingest.shared import multipart

MIB = 1024 * 1024


def test_presigned_put_upload(lab, tmp_path, capsys):
    lab.consent("spk_t", "consent_t")
    clip = tmp_path / "utt_t.wav"
    synth.write_clip(clip, "valid", 5)
    code = cli.main(["presign-upload", "--batch", "b1", "--clip-id", "utt_t", "--speaker", "spk_t",
                     "--consent-id", "consent_t", "--text", "  hello there ", "--expires", "600"],
                    endpoint_url=lab.server_url)
    assert code == 0
    signed = json.loads(capsys.readouterr().out)
    assert signed["key"] == "incoming/contributions/b1/utt_t.wav"
    assert signed["expires_in"] == 600
    assert "X-Amz-Expires=600" in signed["url"]

    request = urllib.request.Request(signed["url"], data=clip.read_bytes(), method="PUT", headers=signed["headers"])
    assert urllib.request.urlopen(request).status == 200
    assert lab.read(signed["key"]) == clip.read_bytes()
    sidecar = lab.read_json("incoming/contributions/b1/utt_t.json")
    assert sidecar["speaker"] == "spk_t"
    assert sidecar["consent_id"] == "consent_t"
    assert sidecar["text"] == "hello there"


def test_presign_rejects_bad_inputs(lab):
    lab.consent("spk_t", "consent_t")
    base = ["presign-upload", "--batch", "b1", "--clip-id", "utt_t", "--speaker", "spk_t",
            "--consent-id", "consent_t", "--text", "hello"]
    assert cli.main(base + ["--expires", "999999"], endpoint_url=lab.server_url) == 2
    assert cli.main(["presign-upload", "--batch", "../evil", "--clip-id", "utt_t", "--speaker", "spk_t",
                     "--consent-id", "consent_t", "--text", "hello"], endpoint_url=lab.server_url) == 2
    assert cli.main(base[:-1] + ["   "], endpoint_url=lab.server_url) == 2
    assert lab.keys("incoming/") == []


class Interrupted(Exception):
    pass


def test_multipart_upload_resumes(lab, tmp_path):
    lab.consent("spk_t", "consent_t")
    path = tmp_path / "long.wav"
    path.write_bytes(bytes(range(256)) * (11 * MIB // 256))  # 11 MiB: parts of 5, 5 and 1 MiB
    sidecar = uploads.build_sidecar("b1", "long", "spk_t", "consent_t", "a long clip")
    key = storage.clip_key("b1", "long")

    seen: list[int] = []

    def interrupt_on_part_two(params, **kwargs):
        number = int(params["query_string"]["partNumber"])
        seen.append(number)
        if number == 2:
            raise Interrupted()

    lab.client.meta.events.register("before-call.s3.UploadPart", interrupt_on_part_two)
    with pytest.raises(Interrupted):
        uploads.upload_clip(lab.client, lab.bucket, sidecar, path)
    assert seen == [1, 2]
    assert key not in lab.keys("incoming/")  # the upload is unfinished

    lab.client.meta.events.unregister("before-call.s3.UploadPart", interrupt_on_part_two)
    resumed: list[int] = []
    lab.client.meta.events.register(
        "before-call.s3.UploadPart", lambda params, **kwargs: resumed.append(int(params["query_string"]["partNumber"])))
    result = uploads.upload_clip(lab.client, lab.bucket, sidecar, path)
    assert resumed == [2, 3]  # part 1 was already stored
    assert result["bytes"] == path.stat().st_size
    assert lab.read(key) == path.read_bytes()


def test_upload_through_the_command_line(lab, tmp_path, capsys):
    lab.consent("spk_t", "consent_t")
    clip = tmp_path / "utt_t.wav"
    synth.write_clip(clip, "valid", 6)
    code = cli.main(["upload", "--batch", "b1", "--speaker", "spk_t", "--consent-id", "consent_t",
                     "--text", "hello there", "--file", str(clip)], endpoint_url=lab.server_url)
    assert code == 0
    assert json.loads(capsys.readouterr().out)["key"] == "incoming/contributions/b1/utt_t.wav"
    assert lab.read("incoming/contributions/b1/utt_t.wav") == clip.read_bytes()


def test_part_size_below_the_minimum_is_rejected(lab, tmp_path):
    lab.consent("spk_t", "consent_t")
    clip = tmp_path / "utt_t.wav"
    synth.write_clip(clip, "valid", 6)
    sidecar = uploads.build_sidecar("b1", "utt_t", "spk_t", "consent_t", "hello")
    from voice_dataset_ingest.errors import Rejected

    with pytest.raises(Rejected):
        uploads.upload_clip(lab.client, lab.bucket, sidecar, clip, part_size=multipart.MIN_PART_SIZE - 1)
