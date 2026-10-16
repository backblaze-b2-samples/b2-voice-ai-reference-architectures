"""The session evaluation step."""
from __future__ import annotations

import json

from voice_session_archive import sessions, storage

SCHEMA = "session-manifest.schema.json"


def archive_session(lab, consent_ids=("consent_caller",)) -> None:
    lab.register(consent_ids)
    key, size = lab.recording()
    assert lab.deliver(lab.body([(key, size)])) == 0


def evaluate(lab, capsys) -> dict:
    capsys.readouterr()
    code = lab.run(["evaluate", "--room", lab.room, "--session-id", lab.session_id])
    assert code == 0
    return json.loads(capsys.readouterr().out)


def test_evaluation_passes_with_active_consent(lab, capsys):
    lab.consent(permitted_uses=("evaluation", "training"))
    archive_session(lab)
    result = evaluate(lab, capsys)
    assert result["status"] == "passed"
    manifest = lab.read_json(lab.manifest_key)
    assert sessions.schema_problems(manifest, SCHEMA) == []
    assert manifest["evaluation"]["eligible_for"] == ["human-review", "model-improvement"]
    assert manifest["evaluation"]["quality"]["status"] == "pass"
    assert lab.keys(f"{storage.EVALUATIONS}{lab.room}/{lab.session_id}/") == [result["result"]]


def test_evaluation_fails_without_consent_record(lab, capsys):
    archive_session(lab)
    result = evaluate(lab, capsys)
    assert result["status"] == "failed"
    manifest = lab.read_json(lab.manifest_key)
    assert manifest["evaluation"]["eligible_for"] == []
    assert manifest["evaluation"]["safety"]["status"] == "fail"
    assert sessions.schema_problems(manifest, SCHEMA) == []


def test_evaluation_fails_with_revoked_consent(lab, capsys):
    lab.consent(status="revoked")
    archive_session(lab)
    assert evaluate(lab, capsys)["status"] == "failed"


def test_evaluation_fails_without_consent_ids(lab, capsys):
    archive_session(lab, consent_ids=())
    assert evaluate(lab, capsys)["status"] == "failed"


def test_evaluation_detects_a_changed_recording(lab, capsys):
    lab.consent()
    archive_session(lab)
    lab.recording(seconds=3.0)  # replaced after the manifest was written
    result = evaluate(lab, capsys)
    assert result["status"] == "failed"
    checks = lab.read_json(lab.manifest_key)["evaluation"]["quality"]["checks"]
    assert checks[0]["name"] == "integrity" and checks[0]["result"] == "fail"


def test_evaluation_needs_a_manifest(lab):
    assert lab.run(["evaluate", "--room", lab.room, "--session-id", lab.session_id]) == 1
