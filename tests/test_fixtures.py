"""Tests for the committed synthetic fixtures. None of them imports a speech model."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
import wave
from pathlib import Path

import jsonschema
import numpy as np
import pytest

import audio_checks
import make_fixtures
import validate_contracts

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "testdata" / "synthetic-speech"
MAX_FIXTURE_BYTES = 4 * 1024 * 1024


def lines(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text().splitlines()]


def line_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads((REPO / "schemas" / "dataset-line.schema.json").read_text())
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


# --- the committed fixtures --------------------------------------------------------

def test_validator_accepts_the_committed_fixtures():
    assert validate_contracts.check_fixtures(REPO) == []


def test_fixtures_are_small():
    total = sum(path.stat().st_size for path in FIXTURES.rglob("*") if path.is_file())
    assert total < MAX_FIXTURE_BYTES


def test_valid_clips_are_16khz_mono_16bit_and_match_their_manifest():
    manifest = lines(FIXTURES / "manifest.jsonl")
    assert len(manifest) >= 6
    for line in manifest:
        assert line_validator().is_valid(line), line
        info = audio_checks.read_wav_info(FIXTURES / line["audio_filepath"])
        assert (info.sample_rate, info.channels, info.bits_per_sample) == (16000, 1, 16)
        assert math.isclose(info.duration_seconds, line["duration"], abs_tol=0.001)
        with wave.open(str(FIXTURES / line["audio_filepath"])) as handle:
            assert math.isclose(handle.getnframes() / handle.getframerate(), line["duration"], abs_tol=0.001)
        assert audio_checks.clip_defects(FIXTURES / line["audio_filepath"], line["duration"]) == []


def test_two_neutral_speakers_with_every_split():
    manifest = lines(FIXTURES / "manifest.jsonl")
    assert {line["speaker"] for line in manifest} == {"spk_a", "spk_b"}
    assert {line["split"] for line in manifest} == {"train", "dev", "test"}


def test_every_clip_file_is_listed_in_the_manifest():
    listed = {line["audio_filepath"] for line in lines(FIXTURES / "manifest.jsonl")}
    present = {path.relative_to(FIXTURES).as_posix() for path in (FIXTURES / "audio").glob("*.wav")}
    assert listed == present


def test_every_invalid_clip_triggers_exactly_its_expected_defect():
    expected = json.loads((FIXTURES / "invalid-expected.json").read_text())
    invalid = lines(FIXTURES / "invalid-manifest.jsonl")
    assert {line["audio_filepath"] for line in invalid} == set(expected)
    assert len(invalid) >= 7
    for line in invalid:
        wanted = expected[line["audio_filepath"]]
        if wanted == ["schema"]:
            assert not line_validator().is_valid(line)
            continue
        assert line_validator().is_valid(line), line
        assert audio_checks.clip_defects(FIXTURES / line["audio_filepath"], line["duration"]) == wanted


def test_invalid_clips_cover_each_defect_code():
    expected = json.loads((FIXTURES / "invalid-expected.json").read_text())
    covered = {code for codes in expected.values() for code in codes}
    assert covered >= {"sample_rate", "channels", "silent", "clipped", "too_short", "truncated", "duration_mismatch", "schema"}


def test_session_has_two_speakers_and_a_passed_evaluation():
    session = json.loads((FIXTURES / "session" / "session-manifest.json").read_text())
    assert {item["participant_id"] for item in session["participants"]} == {"spk_a", "spk_b"}
    assert {item["role"] for item in session["participants"]} == {"agent", "caller"}
    kinds = [item["kind"] for item in session["recordings"]]
    assert kinds.count("mixed") == 1 and kinds.count("participant-track") == 2
    assert session["evaluation"]["status"] == "passed"
    assert session["evaluation"]["eligible_for"]


def test_session_tracks_do_not_overlap_and_sum_to_the_mix():
    folder = FIXTURES / "session"

    def samples(name: str) -> np.ndarray:
        path = folder / name
        return np.array(audio_checks.read_samples(path, audio_checks.read_wav_info(path)), dtype=np.int64)

    first, second, mix = samples("spk_a.wav"), samples("spk_b.wav"), samples("session_mix.wav")
    assert not np.any((first != 0) & (second != 0))
    assert np.array_equal(first + second, mix)


def test_consents_are_synthetic_and_cover_every_speaker():
    records = [json.loads(path.read_text()) for path in sorted((FIXTURES / "consents").glob("*.json"))]
    assert {record["subject"]["speaker_id"] for record in records} == {"spk_a", "spk_b"}
    assert {record["subject"]["type"] for record in records} == {"synthetic"}
    assert {record["status"] for record in records} == {"active"}


def test_croissant_describes_the_dataset_and_every_file_hash_matches():
    document = json.loads((FIXTURES / "croissant.json").read_text())
    assert document["@type"] == "sc:Dataset"
    assert document["conformsTo"].startswith("http://mlcommons.org/croissant/")
    assert document["license"] and document["name"]
    assert document["recordSet"][0]["field"]
    described = {item["contentUrl"]: item["sha256"] for item in document["distribution"]}
    for line in lines(FIXTURES / "manifest.jsonl"):
        assert described[line["audio_filepath"]] == line["sha256"]
    assert described["manifest.jsonl"] == hashlib.sha256((FIXTURES / "manifest.jsonl").read_bytes()).hexdigest()


def test_readme_states_provenance_and_license():
    text = (FIXTURES / "README.md").read_text()
    for needle in ("Kokoro-82M", "Apache-2.0", "MIT", "tools/make_fixtures.py", "No recording of a person"):
        assert needle in text


# --- the generator does not need a speech model --------------------------------------

def test_generator_imports_without_a_speech_model():
    assert "kokoro_onnx" not in sys.modules and "kokoro" not in sys.modules


def test_generator_reports_a_missing_backend(monkeypatch, tmp_path: Path):
    monkeypatch.setitem(sys.modules, "kokoro_onnx", None)
    monkeypatch.setitem(sys.modules, "kokoro", None)
    with pytest.raises(make_fixtures.BackendError, match="no speech backend available"):
        make_fixtures.load_synthesizer(tmp_path)
    assert make_fixtures.main(["--models-dir", str(tmp_path), "--output", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()


def test_requirements_keep_the_speech_model_out_of_ci():
    fixtures = (REPO / "requirements-fixtures.txt").read_text()
    assert "kokoro" in fixtures
    for name in ("requirements-dev.txt", "requirements-catalog.txt", ".github/workflows/ci.yml"):
        assert "kokoro" not in (REPO / name).read_text().lower()


# --- the generator and the validator against a stand-in voice --------------------------

def tone(text: str, voice: str) -> tuple[np.ndarray, int]:
    rate = 24000
    seconds = 0.8 + 0.03 * len(text)
    t = np.arange(int(rate * seconds)) / rate
    pitch = 180 if voice == "af_heart" else 110
    return 0.5 * np.sin(2 * np.pi * (pitch + len(text)) * t), rate


@pytest.fixture
def built(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    make_fixtures.build_fixtures(tmp_path / "testdata" / "synthetic-speech", tone)
    return tmp_path


def fixture_dir(root: Path) -> Path:
    return root / "testdata" / "synthetic-speech"


def test_generated_fixtures_pass_the_validator(built: Path):
    assert validate_contracts.check_fixtures(built) == []


def test_generated_invalid_clips_fail_as_declared(built: Path):
    expected = json.loads((fixture_dir(built) / "invalid-expected.json").read_text())
    for line in lines(fixture_dir(built) / "invalid-manifest.jsonl"):
        wanted = expected[line["audio_filepath"]]
        if wanted != ["schema"]:
            assert audio_checks.clip_defects(fixture_dir(built) / line["audio_filepath"], line["duration"]) == wanted


def test_generation_is_deterministic(built: Path, tmp_path_factory):
    other = tmp_path_factory.mktemp("again")
    make_fixtures.build_fixtures(other / "synthetic-speech", tone)
    first = {p.relative_to(fixture_dir(built)).as_posix(): p.read_bytes() for p in fixture_dir(built).rglob("*") if p.is_file()}
    second = {p.relative_to(other / "synthetic-speech").as_posix(): p.read_bytes() for p in (other / "synthetic-speech").rglob("*") if p.is_file()}
    assert first == second


def test_generation_keeps_the_readme(built: Path):
    readme = fixture_dir(built) / "README.md"
    readme.write_text("kept")
    make_fixtures.build_fixtures(fixture_dir(built), tone)
    assert readme.read_text() == "kept"


def rewrite_first_line(root: Path, **changes) -> None:
    path = fixture_dir(root) / "manifest.jsonl"
    rows = lines(path)
    rows[0].update(changes)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_validator_catches_a_wrong_declared_duration(built: Path):
    rewrite_first_line(built, duration=lines(fixture_dir(built) / "manifest.jsonl")[0]["duration"] + 0.5)
    assert any("duration_mismatch" in error for error in validate_contracts.check_fixtures(built))


def test_validator_catches_a_wrong_hash(built: Path):
    rewrite_first_line(built, sha256="0" * 64)
    assert any("sha256 does not match" in error for error in validate_contracts.check_fixtures(built))


def test_validator_catches_a_line_that_breaks_the_schema(built: Path):
    rewrite_first_line(built, text="")
    assert any("manifest.jsonl:1" in error for error in validate_contracts.check_fixtures(built))


def test_validator_catches_a_missing_clip(built: Path):
    (fixture_dir(built) / "audio" / "utt_0001.wav").unlink()
    assert any("does not exist" in error for error in validate_contracts.check_fixtures(built))


def test_validator_catches_an_unknown_consent(built: Path):
    rewrite_first_line(built, consent_id="consent_missing")
    assert any("has no record" in error for error in validate_contracts.check_fixtures(built))


def test_validator_catches_a_revoked_consent(built: Path):
    path = fixture_dir(built) / "consents" / "consent_spk_a.json"
    record = json.loads(path.read_text())
    record.update(status="revoked", revoked_at="2026-02-01T00:00:00Z")
    path.write_text(json.dumps(record))
    assert any("is revoked" in error for error in validate_contracts.check_fixtures(built))


def test_validator_catches_a_consent_that_does_not_permit_training(built: Path):
    path = fixture_dir(built) / "consents" / "consent_spk_a.json"
    record = json.loads(path.read_text())
    record["permitted_uses"] = ["analytics"]
    path.write_text(json.dumps(record))
    assert any("does not permit training" in error for error in validate_contracts.check_fixtures(built))


def test_validator_catches_a_consent_of_another_speaker(built: Path):
    rewrite_first_line(built, consent_id="consent_spk_b")
    assert any("belongs to spk_b" in error for error in validate_contracts.check_fixtures(built))


def test_validator_catches_a_session_recording_that_changed(built: Path):
    path = fixture_dir(built) / "session" / "spk_a.wav"
    path.write_bytes(path.read_bytes()[:-2] + b"\x01\x01")
    errors = validate_contracts.check_fixtures(built)
    assert any("sha256 does not match" in error for error in errors)


def test_validator_catches_a_session_duration_that_differs_from_the_header(built: Path):
    path = fixture_dir(built) / "session" / "session-manifest.json"
    session = json.loads(path.read_text())
    session["recordings"][0]["duration_seconds"] += 2
    path.write_text(json.dumps(session))
    assert any("duration_mismatch" in error for error in validate_contracts.check_fixtures(built))


def test_validator_catches_a_stale_croissant_hash(built: Path):
    path = fixture_dir(built) / "croissant.json"
    document = json.loads(path.read_text())
    document["distribution"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(document))
    assert any("croissant.json" in error for error in validate_contracts.check_fixtures(built))


def test_repository_without_fixtures_has_nothing_to_check(tmp_path: Path):
    assert validate_contracts.check_fixtures(tmp_path) == []


# --- audio checks on hand-made files ------------------------------------------------------

def write(path: Path, samples, rate=16000, channels=1) -> Path:
    make_fixtures.write_wav(path, np.asarray(samples, dtype="<i2"), rate=rate, channels=channels)
    return path


def speechlike(seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(16000 * seconds)) / 16000
    return (8000 * np.sin(2 * np.pi * 220 * t)).astype("<i2")


def test_audio_checks_accept_a_good_clip(tmp_path: Path):
    assert audio_checks.clip_defects(write(tmp_path / "ok.wav", speechlike()), 1.0) == []


def test_audio_checks_flag_each_defect(tmp_path: Path):
    assert audio_checks.clip_defects(write(tmp_path / "rate.wav", speechlike(), rate=8000)) == ["sample_rate"]
    assert audio_checks.clip_defects(write(tmp_path / "stereo.wav", np.repeat(speechlike(), 2), channels=2)) == ["channels"]
    assert audio_checks.clip_defects(write(tmp_path / "silent.wav", np.zeros(16000))) == ["silent"]
    assert audio_checks.clip_defects(write(tmp_path / "short.wav", speechlike(0.2))) == ["too_short"]
    assert audio_checks.clip_defects(write(tmp_path / "mismatch.wav", speechlike()), 2.0) == ["duration_mismatch"]
    loud = np.full(16000, 32767, dtype="<i2")
    assert audio_checks.clip_defects(write(tmp_path / "loud.wav", loud)) == ["clipped"]


def test_audio_checks_flag_a_truncated_file(tmp_path: Path):
    path = write(tmp_path / "cut.wav", speechlike())
    path.write_bytes(path.read_bytes()[:10000])
    assert audio_checks.clip_defects(path) == ["truncated"]


def test_audio_checks_flag_unreadable_files(tmp_path: Path):
    (tmp_path / "text.wav").write_text("not audio")
    assert audio_checks.clip_defects(tmp_path / "text.wav") == ["unreadable"]
    assert audio_checks.clip_defects(tmp_path / "missing.wav") == ["unreadable"]


def test_audio_checks_command_line(tmp_path: Path, capsys):
    good, bad = write(tmp_path / "ok.wav", speechlike()), write(tmp_path / "bad.wav", np.zeros(16000))
    assert audio_checks.main([str(good)]) == 0
    assert audio_checks.main([str(bad)]) == 1
    assert "silent" in capsys.readouterr().out
    assert audio_checks.main([]) == 2
