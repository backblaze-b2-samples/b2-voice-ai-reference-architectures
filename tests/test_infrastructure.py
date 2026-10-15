"""Tests for infrastructure/: the LiveKit Egress request template and the provisioning script.

No B2 account, credentials or network access are used. The provisioning tests never
run the `b2` tool: they check the commands the script prints and that a default run
executes nothing.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EGRESS = REPO / "infrastructure" / "livekit-egress"
PROVISIONING = REPO / "infrastructure" / "b2-provisioning"
sys.path.insert(0, str(REPO / "examples" / "python"))
sys.path.insert(0, str(EGRESS))
sys.path.insert(0, str(PROVISIONING))

import b2_client  # noqa: E402
import provision  # noqa: E402
import render_request  # noqa: E402

SETTINGS = {
    "B2_REGION": "us-west-004",
    "B2_INCOMING_BUCKET": "example-voice-sessions-incoming",
    "B2_APPLICATION_KEY_ID": "test-key-id",
    "B2_APPLICATION_KEY": "test-key-value",
}
ZONE_ENV_NAMES = {"B2_INCOMING_BUCKET", "B2_WORK_BUCKET", "B2_RELEASES_BUCKET", "B2_PROTECTED_BUCKET"}
ZONE_PREFIXES = {
    "incoming": {"contributions/", "sessions/", "source/"},
    "work": {"candidates/", "scratch/", "evaluations/"},
    "releases": {"datasets/", "deliveries/"},
    "protected": {"consents/", "sessions/"},
}


# LiveKit Egress request template


def template_text() -> str:
    return (EGRESS / "egress-request.template.json").read_text()


def s3_block(request: dict) -> dict:
    return request["file_outputs"][0]["s3"]


def test_template_endpoint_is_derived_from_the_region_placeholder():
    text = template_text()
    assert "https://s3.${B2_REGION}.backblazeb2.com" in text
    assert not re.search(r"s3\.[a-z]{2}-[a-z]+-[0-9]{3}\.", text), "no region may be hard-coded"


def test_template_uses_path_style_and_placeholders_only():
    text = template_text()
    for placeholder in ("${B2_REGION}", "${B2_INCOMING_BUCKET}", "${B2_APPLICATION_KEY_ID}", "${B2_APPLICATION_KEY}"):
        assert placeholder in text
    rendered = render_request.render("room-1", SETTINGS)
    s3 = s3_block(rendered)
    assert s3["force_path_style"] is True
    assert s3["region"] == "us-west-004"
    assert s3["bucket"] == SETTINGS["B2_INCOMING_BUCKET"]
    assert s3["access_key"] == "test-key-id"
    assert s3["secret"] == "test-key-value"


@pytest.mark.parametrize("region", ["us-west-004", "us-east-005", "eu-central-003"])
def test_rendered_endpoint_matches_the_example_client(region):
    rendered = render_request.render("room-1", {**SETTINGS, "B2_REGION": region})
    assert s3_block(rendered)["endpoint"] == b2_client.endpoint_for_region(region)
    assert s3_block(rendered)["endpoint"] == f"https://s3.{region}.backblazeb2.com"


def test_template_is_valid_json_once_filled_and_keeps_livekit_markers():
    rendered = render_request.render("room-1", SETTINGS)
    assert rendered["room_name"] == "room-1"
    assert rendered["audio_only"] is True
    output = rendered["file_outputs"][0]
    assert output["filepath"] == "sessions/{room_name}/{time}"
    assert output["filepath"].startswith("sessions/"), "must stay inside the key's prefix"


def test_values_with_quotes_cannot_break_the_request():
    rendered = render_request.render('room "1"', {**SETTINGS, "B2_APPLICATION_KEY": 'a"b'})
    assert rendered["room_name"] == 'room "1"'
    assert s3_block(rendered)["secret"] == 'a"b'


def test_render_requires_every_setting_and_a_valid_region():
    for name in SETTINGS:
        with pytest.raises(RuntimeError, match=name):
            render_request.render("room-1", {k: v for k, v in SETTINGS.items() if k != name})
    with pytest.raises(ValueError):
        render_request.render("room-1", {**SETTINGS, "B2_REGION": "https://example.com"})
    with pytest.raises(ValueError):
        render_request.render("", SETTINGS)


def test_template_holds_no_credential_values():
    for line in template_text().splitlines():
        if '"secret"' in line or '"access_key"' in line:
            assert "${" in line


def test_render_cli_prints_the_request(monkeypatch, capsys):
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)
    assert render_request.main(["--room", "room-9"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["room_name"] == "room-9"
    assert s3_block(printed)["force_path_style"] is True


# Provisioning script


def create_commands(steps, kind):
    return [s for s in steps if s.kind == kind]


def test_default_run_prints_commands_and_executes_nothing(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise AssertionError("a default run must not execute anything")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(subprocess, "Popen", fail)
    monkeypatch.setattr(provision.subprocess, "run", fail)
    assert provision.main(["--bucket-prefix", "example"]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "b2 bucket create" in out
    assert "b2 key create" in out


def test_explicit_dry_run_also_executes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(provision.subprocess, "run", lambda *a, **k: pytest.fail("executed"))
    assert provision.main(["--dry-run", "--bucket-prefix", "example"]) == 0
    capsys.readouterr()


def test_apply_runs_each_command_only_when_asked(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(provision.shutil, "which", lambda name: "/usr/bin/b2")
    monkeypatch.setattr(provision.subprocess, "run", lambda argv, check: calls.append(list(argv)))
    assert provision.main(["--bucket-prefix", "example", "--apply"]) == 0
    capsys.readouterr()
    planned = provision.plan("example")
    assert calls == [list(step.argv) for step in planned]


def test_apply_refuses_the_placeholder_prefix(monkeypatch):
    monkeypatch.setattr(provision.subprocess, "run", lambda *a, **k: pytest.fail("executed"))
    with pytest.raises(SystemExit):
        provision.main(["--apply"])


def test_apply_and_dry_run_cannot_be_combined():
    with pytest.raises(SystemExit):
        provision.main(["--apply", "--dry-run"])


def test_cleanup_prints_only_and_refuses_apply(monkeypatch, capsys):
    monkeypatch.setattr(provision.subprocess, "run", lambda *a, **k: pytest.fail("executed"))
    assert provision.main(["--cleanup", "--bucket-prefix", "example"]) == 0
    out = capsys.readouterr().out
    assert "b2 bucket delete" in out and "b2 key delete" in out
    with pytest.raises(SystemExit):
        provision.main(["--cleanup", "--apply", "--bucket-prefix", "example"])


@pytest.mark.parametrize("architecture", sorted(provision.ARCHITECTURES))
def test_each_architecture_has_at_least_two_zone_buckets(architecture):
    steps = provision.plan("example", [architecture])
    bucket_names = {s.argv[-2] for s in create_commands(steps, "bucket")}
    assert len(bucket_names) >= 2
    assert len(bucket_names) == len(create_commands(steps, "bucket")), "one bucket per zone, no duplicates"


@pytest.mark.parametrize("architecture", sorted(provision.ARCHITECTURES))
def test_one_scoped_key_per_prefix(architecture):
    steps = provision.plan("example", [architecture])
    expected = sum(len(p) for p in provision.ARCHITECTURES[architecture]["zones"].values())
    keys = create_commands(steps, "key")
    assert len(keys) == expected
    seen = set()
    for step in keys:
        argv = list(step.argv)
        bucket = argv[argv.index("--bucket") + 1]
        prefix = argv[argv.index("--name-prefix") + 1]
        assert argv.count("--bucket") == 1 and argv.count("--name-prefix") == 1
        assert prefix.endswith("/")
        assert (bucket, prefix) not in seen
        seen.add((bucket, prefix))
        capabilities = argv[-1].split(",")
        assert "deleteFiles" not in capabilities and "writeKeys" not in capabilities
        assert "bypassGovernance" not in capabilities
    bucket_names = {s.argv[-2] for s in create_commands(steps, "bucket")}
    assert {bucket for bucket, _ in seen} <= bucket_names


def test_key_names_are_unique_and_valid():
    names = [s.argv[-2] for s in create_commands(provision.plan("example"), "key")]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[A-Za-z0-9-]{1,100}", n) for n in names)


def test_prefixes_and_env_names_match_the_shared_layout():
    assert {z["env"] for z in provision.ZONES.values()} == ZONE_ENV_NAMES
    for architecture, spec in provision.ARCHITECTURES.items():
        for zone, prefixes in spec["zones"].items():
            assert set(prefixes) <= ZONE_PREFIXES[zone], (architecture, zone)


def test_no_tenant_prefix_anywhere():
    text = provision.render(provision.plan("example")) + provision.render(provision.cleanup_plan("example"))
    assert not re.search(r"tenant", text, re.IGNORECASE)
    assert "{" not in text
    for spec in provision.ARCHITECTURES.values():
        for prefixes in spec["zones"].values():
            for prefix in prefixes:
                assert prefix.count("/") == 1 and prefix.endswith("/"), "single-level purpose prefix only"


def test_protected_zone_enables_object_lock_and_every_bucket_is_private_and_encrypted():
    for step in create_commands(provision.plan("example"), "bucket"):
        argv = list(step.argv)
        assert argv[-1] == "allPrivate"
        assert argv[argv.index("--default-server-side-encryption") + 1] == "SSE-B2"
        assert ("--file-lock-enabled" in argv) == argv[-2].endswith("-protected")


def test_invalid_bucket_prefix_is_rejected():
    with pytest.raises(ValueError):
        provision.plan("Bad_Prefix")
    with pytest.raises(ValueError):
        provision.plan("x" * 45)


def test_bucket_prefix_of_the_dry_run_appears_in_names():
    out = subprocess.run(
        [sys.executable, str(PROVISIONING / "provision.py"), "--bucket-prefix", "acme"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "acme-voice-sessions-incoming" in out
    assert "B2_INCOMING_BUCKET=acme-voice-sessions-incoming" in out


def test_session_archive_template_prefix_has_a_matching_key():
    steps = provision.plan("example", ["voice-agent-session-archive"])
    key_scopes = [
        (s.argv[list(s.argv).index("--bucket") + 1], s.argv[list(s.argv).index("--name-prefix") + 1])
        for s in create_commands(steps, "key")
    ]
    assert ("example-voice-sessions-incoming", "sessions/") in key_scopes
