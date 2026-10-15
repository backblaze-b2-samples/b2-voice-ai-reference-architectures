"""Tests for the voice schemas and their example manifests."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import jsonschema
import pytest

import validate_contracts

REPO = Path(__file__).resolve().parents[1]
NEW_SCHEMAS = ["session-manifest", "generation-job-manifest", "consent-record", "dataset-line"]
TITLES = {
    "session-manifest": "Voice agent session manifest",
    "generation-job-manifest": "Voice generation job manifest",
    "consent-record": "Voice consent and license record",
    "dataset-line": "Voice dataset manifest line",
}


def load(name: str) -> dict:
    return json.loads((REPO / "schemas" / f"{name}.schema.json").read_text())


def example(name: str) -> dict:
    return json.loads((REPO / "manifests" / f"{name}.example.json").read_text())


def problems(name: str, instance: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(load(name), format_checker=jsonschema.FormatChecker())
    return [error.message for error in validator.iter_errors(instance)]


def mutated(name: str, change) -> list[str]:
    instance = copy.deepcopy(example(name))
    change(instance)
    return problems(name, instance)


# --- schema files and examples ----------------------------------------------------

@pytest.mark.parametrize("name", NEW_SCHEMAS)
def test_schema_is_valid_with_own_id_and_title(name: str):
    schema = load(name)
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$id"] == f"urn:backblaze:b2-voice-ai-reference-architectures:{name}:1"
    assert schema["title"] == TITLES[name]


def test_schema_ids_and_titles_are_unique():
    schemas = [json.loads(path.read_text()) for path in (REPO / "schemas").glob("*.schema.json")]
    assert len({schema["$id"] for schema in schemas}) == len(schemas)
    assert len({schema["title"] for schema in schemas}) == len(schemas)


@pytest.mark.parametrize("name", NEW_SCHEMAS)
def test_example_is_valid(name: str):
    assert problems(name, example(name)) == []


@pytest.mark.parametrize("name", NEW_SCHEMAS)
def test_schema_rejects_unknown_properties(name: str):
    assert mutated(name, lambda item: item.update(unexpected=True))


def test_repository_validator_accepts_examples_and_schemas():
    assert validate_contracts.check_schemas(REPO) == []
    assert validate_contracts.check_manifest_examples(REPO) == []


def test_llms_txt_lists_the_new_schemas():
    text = (REPO / "llms.txt").read_text()
    for name in NEW_SCHEMAS:
        assert f"schemas/{name}.schema.json" in text


# --- session manifest and the session evaluation ----------------------------------

def set_evaluation(status, eligible, quality="pass", safety="pass"):
    def change(item):
        item["evaluation"]["status"] = status
        item["evaluation"]["eligible_for"] = eligible
        item["evaluation"]["quality"]["status"] = quality
        item["evaluation"]["safety"]["status"] = safety
    return change


def test_session_evaluation_is_required():
    assert mutated("session-manifest", lambda item: item.pop("evaluation"))


@pytest.mark.parametrize("status", ["failed", "flagged"])
def test_session_that_did_not_pass_cannot_be_selected(status: str):
    assert mutated("session-manifest", set_evaluation(status, ["analytics"], "fail", "fail"))


def test_session_that_did_not_pass_may_have_no_selection():
    assert mutated("session-manifest", set_evaluation("failed", [], "fail", "fail")) == []


def test_pending_session_cannot_be_selected():
    def change(item):
        item["evaluation"]["status"] = "pending"
    assert mutated("session-manifest", change)


def test_pending_session_without_results_is_valid():
    def change(item):
        item["evaluation"] = {"status": "pending", "evaluator": {"name": "session-evaluator", "version": "1.0.0"}, "eligible_for": []}
    assert mutated("session-manifest", change) == []


def test_passed_session_needs_passing_quality_and_safety():
    assert mutated("session-manifest", set_evaluation("passed", ["human-review"], quality="warn"))
    assert mutated("session-manifest", set_evaluation("passed", ["human-review"], safety="fail"))


def test_passed_session_needs_results():
    assert mutated("session-manifest", lambda item: item["evaluation"].pop("safety"))


def test_model_improvement_needs_consent():
    assert mutated("session-manifest", lambda item: item.update(consent_ids=[]))


def test_analytics_without_consent_record_is_allowed_by_the_schema():
    def change(item):
        item["consent_ids"] = []
        item["evaluation"]["eligible_for"] = ["analytics"]
    assert mutated("session-manifest", change) == []


@pytest.mark.parametrize("field", ["source", "participants", "recordings", "consent_ids", "retention"])
def test_session_required_fields(field: str):
    assert mutated("session-manifest", lambda item: item.pop(field))


@pytest.mark.parametrize("path", ["../escape.wav", "/absolute.wav", "spaces in name.wav"])
def test_session_recording_path_is_a_relative_key(path: str):
    assert mutated("session-manifest", lambda item: item["recordings"][0].update(path=path))


def test_session_semantics_catch_inverted_times_and_wrong_duration():
    manifest = example("session-manifest")
    assert validate_contracts.check_session_semantics(manifest, "m") == []
    inverted = copy.deepcopy(manifest)
    inverted["ended_at"], inverted["started_at"] = manifest["started_at"], manifest["ended_at"]
    assert any("before started_at" in error for error in validate_contracts.check_session_semantics(inverted, "m"))
    wrong = copy.deepcopy(manifest)
    wrong["duration_seconds"] = 10
    assert any("duration_seconds" in error for error in validate_contracts.check_session_semantics(wrong, "m"))
    stranger = copy.deepcopy(manifest)
    stranger["recordings"][1]["participant_id"] = "participant_unknown"
    assert any("unknown participant" in error for error in validate_contracts.check_session_semantics(stranger, "m"))


# --- generation job manifest ------------------------------------------------------

def test_promotion_needs_approved_review():
    def change(item):
        item["review"] = {"status": "pending"}
    assert mutated("generation-job-manifest", change)


def test_promotion_needs_lineage():
    assert mutated("generation-job-manifest", lambda item: item["promotion"].pop("lineage"))


def test_promotion_needs_successful_job():
    assert mutated("generation-job-manifest", lambda item: item.update(status="running"))


def test_job_without_promotion_may_wait_for_review():
    def change(item):
        item.pop("promotion")
        item["review"] = {"status": "pending"}
    assert mutated("generation-job-manifest", change) == []


def test_decided_review_needs_a_date():
    assert mutated("generation-job-manifest", lambda item: item["review"].pop("decided_at"))


def test_succeeded_job_needs_outputs():
    assert mutated("generation-job-manifest", lambda item: item.update(outputs=[]))


def test_presigned_delivery_is_capped_at_seven_days():
    assert mutated("generation-job-manifest", lambda item: item["input"]["delivery"].update(expires_in_seconds=604801))
    assert mutated("generation-job-manifest", lambda item: item["input"]["delivery"].update(expires_in_seconds=0))


def test_output_needs_an_expiry():
    assert mutated("generation-job-manifest", lambda item: item["outputs"][0].pop("expires_at"))


def test_unknown_job_kind_is_rejected():
    assert mutated("generation-job-manifest", lambda item: item.update(kind="voice-cloning"))


def test_placeholder_provider_is_allowed():
    assert mutated("generation-job-manifest", lambda item: item["provider"].update(mode="placeholder")) == []


# --- consent and license record ---------------------------------------------------

def test_revoked_consent_needs_a_revocation_date():
    assert mutated("consent-record", lambda item: item.update(status="revoked"))
    assert mutated("consent-record", lambda item: item.update(status="revoked", revoked_at="2026-02-01T00:00:00Z")) == []


def test_expired_consent_needs_an_expiry_date():
    assert mutated("consent-record", lambda item: (item.update(status="expired"), item.pop("expires_at")))


def test_synthetic_subject_uses_the_not_applicable_evidence():
    def change(item):
        item["subject"]["type"] = "synthetic"
    assert mutated("consent-record", change)
    def fixed(item):
        item["subject"]["type"] = "synthetic"
        item["evidence"]["kind"] = "not-applicable-synthetic"
    assert mutated("consent-record", fixed) == []


def test_human_subject_cannot_claim_synthetic_evidence():
    assert mutated("consent-record", lambda item: item["evidence"].update(kind="not-applicable-synthetic"))


def test_consent_needs_at_least_one_permitted_use():
    assert mutated("consent-record", lambda item: item.update(permitted_uses=[]))
    assert mutated("consent-record", lambda item: item.update(permitted_uses=["resale"]))


def test_consent_speaker_id_is_pseudonymous():
    assert mutated("consent-record", lambda item: item["subject"].update(speaker_id="Jane Doe"))


# --- dataset line -----------------------------------------------------------------

def test_minimal_dataset_line_is_valid():
    assert problems("dataset-line", {"audio_filepath": "audio/a.wav", "duration": 1.5, "text": "hello"}) == []


@pytest.mark.parametrize("field", ["audio_filepath", "duration", "text"])
def test_dataset_line_required_fields(field: str):
    assert mutated("dataset-line", lambda item: item.pop(field))


@pytest.mark.parametrize("duration", [0, -1, "2.0"])
def test_dataset_line_duration_is_a_positive_number(duration):
    assert mutated("dataset-line", lambda item: item.update(duration=duration))


def test_dataset_line_text_is_not_empty():
    assert mutated("dataset-line", lambda item: item.update(text=""))


@pytest.mark.parametrize("path", ["../audio/a.wav", "/audio/a.wav", "audio/a b.wav"])
def test_dataset_line_path_is_relative(path: str):
    assert mutated("dataset-line", lambda item: item.update(audio_filepath=path))


def test_dataset_line_split_and_hash_are_checked():
    assert mutated("dataset-line", lambda item: item.update(split="holdout"))
    assert mutated("dataset-line", lambda item: item.update(sha256="abc"))


# --- the repository validator -----------------------------------------------------

@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    shutil.copytree(REPO / "manifests", tmp_path / "manifests")
    return tmp_path


def test_validator_rejects_an_invalid_example(mini_repo: Path):
    path = mini_repo / "manifests" / "consent-record.example.json"
    broken = json.loads(path.read_text())
    broken["status"] = "revoked"
    path.write_text(json.dumps(broken))
    assert any("consent-record.example.json" in error for error in validate_contracts.check_manifest_examples(mini_repo))


def test_validator_rejects_an_example_without_schema(mini_repo: Path):
    (mini_repo / "manifests" / "unknown.example.json").write_text("{}")
    assert any("no schemas/unknown.schema.json" in error for error in validate_contracts.check_manifest_examples(mini_repo))


def test_validator_rejects_a_schema_with_a_foreign_id(mini_repo: Path):
    path = mini_repo / "schemas" / "dataset-line.schema.json"
    schema = json.loads(path.read_text())
    schema["$id"] = "urn:example:other:dataset-line:1"
    path.write_text(json.dumps(schema))
    assert any("$id must start with" in error for error in validate_contracts.check_schemas(mini_repo))
