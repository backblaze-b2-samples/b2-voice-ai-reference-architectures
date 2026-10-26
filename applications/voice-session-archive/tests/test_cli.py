"""The command line: egress request, env file, the whole workflow and registration."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import FAKE_ENV, FORBIDDEN, all_calls, sign
from voice_session_archive import cli, config, storage


def egress_request(capsys, *extra: str) -> dict:
    capsys.readouterr()
    assert cli.main(["egress-request", "--room", "support-room", "--session-id", "session-0001", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def test_egress_request_uses_region_endpoint_and_path_style(monkeypatch, capsys):
    monkeypatch.setenv("B2_REGION", "eu-central-003")
    monkeypatch.setenv("B2_BUCKET_NAME", "my-sessions-bucket")
    request = egress_request(capsys)
    output = request["file_outputs"][0]
    s3 = output["s3"]
    assert s3["endpoint"] == "https://s3.eu-central-003.backblazeb2.com"
    assert s3["region"] == "eu-central-003"
    assert s3["force_path_style"] is True
    assert s3["bucket"] == "my-sessions-bucket"
    assert output["filepath"].startswith("incoming/sessions/support-room/session-0001/")
    assert request["room_name"] == "support-room"


def test_egress_request_never_prints_app_secret(monkeypatch, capsys):
    monkeypatch.setenv("B2_APPLICATION_KEY_ID", "app-key-id-that-must-not-appear")
    monkeypatch.setenv("B2_APPLICATION_KEY", "app-key-that-must-not-appear")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "livekit-secret-that-must-not-appear")
    capsys.readouterr()
    assert cli.main(["egress-request", "--room", "support-room", "--session-id", "session-0001"]) == 0
    captured = capsys.readouterr()
    text = captured.out + captured.err
    for value in ("app-key-id-that-must-not-appear", "app-key-that-must-not-appear",
                  "livekit-secret-that-must-not-appear"):
        assert value not in text
    s3 = json.loads(captured.out)["file_outputs"][0]["s3"]
    assert s3["access_key"].startswith("<") and s3["secret"].startswith("<")
    # Without any key in the environment the request is still printed: it needs none.
    for name in ("B2_APPLICATION_KEY_ID", "B2_APPLICATION_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(name)
    assert cli.main(["egress-request", "--room", "support-room", "--session-id", "session-0001"]) == 0


def test_app_never_configures_bucket(lab, recorded, capsys):
    """Run every command of the workflow; no request may create or configure a bucket."""
    demo = Path.cwd() / "demo"
    assert lab.run(["synth", "--out", str(demo), "--room", lab.room, "--session-id", lab.session_id,
                    "--seconds", "2", "--put"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert lab.run(["egress-request", "--room", lab.room, "--session-id", lab.session_id]) == 0
    assert lab.run(["register-session", "--room", lab.room, "--session-id", lab.session_id,
                    "--participant", "caller_1:caller", "--participant", "agent_1:agent",
                    "--consent-id", plan["consent_id"]]) == 0
    body = Path(plan["event_body"]).read_bytes()
    assert lab.run(["handle-webhook", "--body", plan["event_body"], "--authorization", sign(body)]) == 0
    capsys.readouterr()
    assert lab.run(["evaluate", "--room", lab.room, "--session-id", lab.session_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"

    calls = all_calls(recorded)
    operations = {name for name, _ in calls}
    assert {"PutObject", "HeadObject", "GetObject"} <= operations
    assert sorted(name for name in operations if name.startswith(FORBIDDEN)) == []
    zones = ("incoming/", "work/", "releases/", "protected/")
    for name, path in calls:
        if name == "PutObject":
            assert path.lstrip("/").startswith(zones), path


def test_synth_derives_a_consent_id_per_session(capsys, tmp_path):
    def synth(*extra: str) -> dict:
        capsys.readouterr()
        assert cli.main(["synth", "--out", str(tmp_path / "demo"), "--room", "support-room", *extra]) == 0
        return json.loads(capsys.readouterr().out)

    first = synth("--session-id", "session-0001")
    assert first["consent_id"] == "consent-session-0001-caller"
    assert json.loads(Path(first["consent"]).read_text())["consent_id"] == first["consent_id"]
    assert synth("--session-id", "session-0001") == first  # deterministic
    assert synth("--session-id", "session-0002")["consent_id"] == "consent-session-0002-caller"
    chosen = synth("--session-id", "session-0001", "--consent-id", "consent-team-a")
    assert chosen["consent_id"] == "consent-team-a"
    assert cli.main(["synth", "--out", str(tmp_path / "demo"), "--room", "support-room",
                     "--session-id", "session-0001", "--consent-id", "Bad Id"]) == 2


def test_register_session_rejects_bad_input_before_b2(lab, recorded):
    before = len(all_calls(recorded))
    base = ["register-session", "--room", lab.room, "--session-id", lab.session_id]
    assert lab.run(base) == 2  # no participant
    assert lab.run(base + ["--participant", "caller_1:boss"]) == 2
    assert lab.run(["register-session", "--room", lab.room, "--session-id", "Bad Id",
                    "--participant", "caller_1:caller"]) == 2
    assert len(all_calls(recorded)) == before


def test_register_session_refuses_to_replace(lab):
    lab.register()
    registration = lab.read_json(storage.registration_key(lab.room, lab.session_id))
    assert registration["participants"][0] == {"participant_id": "caller_1", "role": "caller"}
    argv = ["register-session", "--room", lab.room, "--session-id", lab.session_id, "--participant", "x_1:caller"]
    assert lab.run(argv) == 1


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
    assert cli.main(["evaluate", "--room", "support-room", "--session-id", "session-0001"]) == 1
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
    code = "import os, voice_session_archive.cli; print('B2_BUCKET_NAME' in os.environ)"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source)}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), env=env, capture_output=True, text=True)
    assert done.stdout.strip() == "False", done.stderr


def test_endpoint_is_derived_from_the_region():
    assert config.endpoint_for_region("us-west-004") == "https://s3.us-west-004.backblazeb2.com"
    with pytest.raises(config.ConfigError):
        config.endpoint_for_region("https://example.com")


def test_missing_livekit_secret_is_a_configuration_error(lab, monkeypatch):
    monkeypatch.delenv("LIVEKIT_API_SECRET")
    key, size = lab.recording()
    assert lab.deliver(lab.body([(key, size)])) == 1
