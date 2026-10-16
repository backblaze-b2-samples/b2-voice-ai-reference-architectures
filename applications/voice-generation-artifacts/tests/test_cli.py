"""The command line as a whole: no bucket configuration, exit codes, and where the env file is read from."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import FAKE_ENV, all_calls
from voice_generation_artifacts import cli, config, storage

FORBIDDEN = ("PutBucket", "CreateBucket", "DeleteBucket", "PutObjectLockConfiguration")


def call(argv: list[str], lab, capsys, expected: int = 0) -> dict:
    code = cli.main(argv, endpoint_url=lab.server_url)
    captured = capsys.readouterr()
    assert code == expected, captured.err
    return json.loads(captured.out) if expected == 0 else {"stderr": captured.err}


def test_app_never_configures_bucket(lab, recorded, capsys):
    """Run every command of the workflow; no request may configure or create a bucket."""
    data = Path.cwd() / "data"
    call(["synth-source", "--out", str(data), "--put", "--seconds", "2"], lab, capsys)
    job = call(["submit", "--source-id", "demo-source", "--job-id", "job-1", "--target-language", "es",
                "--target-language", "fr"], lab, capsys)
    call(["promote", "--job-id", "job-1", "--attempt", "a1", "--version", "v1", "--approve"], lab, capsys)
    delivered = call(["deliver", "--job-id", "job-1", "--version", "v1", "--out", str(data / "delivery.json")], lab, capsys)
    assert job["job_id"] == "job-1" and delivered["urls"] > 0

    calls = all_calls(recorded)
    operations = {name for name, _ in calls}
    assert {"PutObject", "CopyObject", "HeadObject", "GetObject"} <= operations
    banned = sorted(name for name in operations if name.startswith(FORBIDDEN) or name.startswith(("PutBucket", "DeleteBucket")))
    assert banned == []
    zones = ("incoming/", "work/", "releases/", "protected/")
    for name, path in calls:
        if name in {"PutObject", "CopyObject"}:
            assert path.lstrip("/").startswith(zones), path


def test_exit_codes(lab, recorded, capsys):
    data = Path.cwd() / "data"
    call(["synth-source", "--out", str(data), "--put", "--seconds", "2"], lab, capsys)
    # 2: an input that did not pass its check, before anything is written.
    assert "target-language" in call(["submit", "--source-id", "demo-source"], lab, capsys, expected=2)["stderr"]
    call(["promote", "--job-id", "job-1", "--attempt", "a1", "--version", "v1"], lab, capsys, expected=2)  # no --approve
    call(["deliver", "--job-id", "job-1", "--version", "v1", "--expires", "604801"], lab, capsys, expected=2)
    call(["submit", "--source-id", "../x", "--target-language", "es"], lab, capsys, expected=2)
    assert lab.keys("work/") == [] and lab.keys("releases/") == []
    # 1: another error, here a version that already exists.
    args = ["submit", "--source-id", "demo-source", "--job-id", "job-1", "--target-language", "es"]
    call(args, lab, capsys)
    call(args, lab, capsys, expected=1)


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
    assert cli.main(["promote", "--job-id", "j", "--attempt", "a1", "--version", "v1", "--approve"]) == 1
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
    code = "import os, voice_generation_artifacts.cli; print('B2_BUCKET_NAME' in os.environ)"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source)}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), env=env, capture_output=True, text=True)
    assert done.stdout.strip() == "False", done.stderr


def test_endpoint_is_derived_from_the_region():
    assert config.endpoint_for_region("us-west-004") == "https://s3.us-west-004.backblazeb2.com"
    with pytest.raises(config.ConfigError):
        config.endpoint_for_region("https://evil.example.com/")
    settings = config.Settings.from_mapping({**FAKE_ENV, "B2_BUCKET_NAME": "some-bucket"})
    assert storage.make_client(settings).meta.endpoint_url == "https://s3.us-west-004.backblazeb2.com"


def test_user_agent_is_sent(lab):
    seen = []
    lab.client.meta.events.register("before-send.s3.*", lambda request, **kw: seen.append(request.headers["User-Agent"]))
    lab.client.put_object(Bucket=lab.bucket, Key="incoming/x", Body=b"x")
    assert seen
    for agent in seen:
        text = agent.decode() if isinstance(agent, bytes) else agent
        assert "b2-voice-ai-reference-architectures-voice-generation-artifacts" in text
