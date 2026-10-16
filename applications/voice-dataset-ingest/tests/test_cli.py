"""The command line as a whole: no bucket configuration, and where the env file is read from."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from helpers import FAKE_ENV, all_calls, sign
from voice_dataset_ingest import cli, config

FORBIDDEN = ("PutBucket", "CreateBucket", "DeleteBucket", "PutObjectLockConfiguration")


def call(argv: list[str], lab, capsys) -> dict:
    code = cli.main(argv, endpoint_url=lab.server_url)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return json.loads(captured.out)


def test_app_never_configures_bucket(lab, recorded, capsys):
    """Run every command of the workflow; no request may configure or create a bucket."""
    data = Path.cwd() / "data"
    call(["synth", "--out", str(data), "--put-consents"], lab, capsys)

    clip = lambda name: data / "clips" / f"{name}.wav"  # noqa: E731
    common = ["--batch", "b1", "--speaker", "spk_a", "--consent-id", "consent_synth_spk_a", "--text", "hello there"]
    signed = call(["presign-upload", *common, "--clip-id", "utt_0001"], lab, capsys)
    request = urllib.request.Request(signed["url"], data=clip("utt_0001").read_bytes(), method="PUT",
                                     headers=signed["headers"])
    assert urllib.request.urlopen(request).status == 200
    call(["upload", *common, "--file", str(clip("utt_0002"))], lab, capsys)

    body = lab.event_body("incoming/contributions/b1/utt_0001.wav")
    (data / "event.json").write_bytes(body)
    call(["handle-event", "--body", str(data / "event.json"), "--signature", sign(body)], lab, capsys)
    call(["process", "--batch", "b1"], lab, capsys)
    summary = call(["release", "--batch", "b1", "--dataset", "demo", "--dataset-version", "v1",
                    "--retain-days", "7"], lab, capsys)
    assert summary["clips"] == 2

    calls = all_calls(recorded)
    operations = {name for name, _ in calls}
    assert {"PutObject", "CreateMultipartUpload", "GetObjectLockConfiguration"} <= operations
    banned = sorted(name for name in operations if name.startswith(FORBIDDEN))
    assert banned == []
    zones = ("incoming/", "work/", "releases/", "protected/")
    for name, path in calls:
        if name == "PutObject":
            assert path.lstrip("/").startswith(zones), path


def test_env_file_read_only_from_working_directory(monkeypatch, capsys):
    names = list(FAKE_ENV) + ["B2_BUCKET_NAME"]
    for name in names:
        monkeypatch.delenv(name)
    parent = Path.cwd() / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / ".env").write_text(
        "B2_APPLICATION_KEY_ID=from-parent\nB2_APPLICATION_KEY=from-parent\n"
        "B2_BUCKET_NAME=parent-bucket\nB2_REGION=us-west-004\n")
    monkeypatch.chdir(child)

    # A file in the parent directory is not read.
    assert config.load_env_file() == []
    assert all(name not in os.environ for name in names)
    assert cli.main(["process", "--batch", "b1"]) == 1
    assert "B2_" in capsys.readouterr().err
    assert all(name not in os.environ for name in names)

    # A file in the current directory is read; variables already in the environment win.
    (child / ".env").write_text("B2_BUCKET_NAME=child-bucket\nB2_REGION=eu-central-003\n")
    monkeypatch.setenv("B2_REGION", "us-west-004")
    added = config.load_env_file()
    assert added == ["B2_BUCKET_NAME"]
    assert os.environ["B2_BUCKET_NAME"] == "child-bucket"
    assert os.environ["B2_REGION"] == "us-west-004"
    monkeypatch.delenv("B2_BUCKET_NAME")


def test_importing_the_package_reads_no_env_file():
    (Path.cwd() / ".env").write_text("B2_BUCKET_NAME=read-on-import\n")
    source = Path(config.__file__).resolve().parents[1]
    code = "import os, voice_dataset_ingest.cli; print('B2_BUCKET_NAME' in os.environ)"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source)}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), env=env, capture_output=True, text=True)
    assert done.stdout.strip() == "False", done.stderr


def test_endpoint_is_derived_from_the_region():
    assert config.endpoint_for_region("us-west-004") == "https://s3.us-west-004.backblazeb2.com"
    with pytest.raises(config.ConfigError):
        config.endpoint_for_region("https://evil.example.com/")
    from voice_dataset_ingest import storage

    settings = config.Settings.from_mapping({**FAKE_ENV, "B2_BUCKET_NAME": "some-bucket"})
    assert storage.make_client(settings).meta.endpoint_url == "https://s3.us-west-004.backblazeb2.com"


def test_user_agent_is_sent(lab):
    seen = []
    lab.client.meta.events.register("before-send.s3.*", lambda request, **kw: seen.append(request.headers["User-Agent"]))
    lab.client.put_object(Bucket=lab.bucket, Key="incoming/x", Body=b"x")
    assert seen
    for agent in seen:
        text = agent.decode() if isinstance(agent, bytes) else agent
        assert "b2-voice-ai-reference-architectures-voice-dataset-ingest" in text
