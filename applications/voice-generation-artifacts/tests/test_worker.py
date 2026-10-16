"""The placeholder worker: synthetic audio that is audible, repeatable and checked."""
from __future__ import annotations

import array
import hashlib
from pathlib import Path

import pytest

from voice_generation_artifacts import storage, synth
from voice_generation_artifacts.errors import ConfigError, Rejected
from voice_generation_artifacts.shared import audio_checks
from voice_generation_artifacts.worker import PlaceholderWorker, WorkerRequest, fetch_source, source_duration


def request_for(lab, source_id: str = "src-1", **fields) -> WorkerRequest:
    data = storage.get_bytes(lab.admin, lab.bucket, storage.source_key(source_id))
    values = dict(job_id="job-1", kind="dubbing", source_url=storage.presign_get(lab.client, lab.bucket, storage.source_key(source_id), 300),
                  source_sha256=hashlib.sha256(data).hexdigest(), source_language="en",
                  target_languages=("es", "fr"), speakers=("spk_a", "spk_b"))
    values.update(fields)
    return WorkerRequest(**values)


def stats(data: bytes, folder: Path, name: str):
    path = folder / name
    path.write_bytes(data)
    info = audio_checks.read_wav_info(path)
    samples = audio_checks.read_samples(path, info)
    peak = max(max(samples), -min(samples))
    rms = (sum(value * value for value in samples) / len(samples)) ** 0.5
    return path, info, peak, rms


def test_placeholder_audio_is_audible_and_deterministic(lab, tmp_path):
    lab.source(seconds=2.0)
    request = request_for(lab)
    first = PlaceholderWorker(seed=7).run(request)
    second = PlaceholderWorker(seed=7).run(request)

    assert [item.name for item in first] == ["es/spk_a.wav", "es/spk_b.wav", "es/mix.wav",
                                              "fr/spk_a.wav", "fr/spk_b.wav", "fr/mix.wav"]
    # Same seed and same request: byte-identical output.
    assert [item.data for item in first] == [item.data for item in second]
    # Another seed gives other audio.
    other = PlaceholderWorker(seed=8).run(request)
    assert [item.data for item in other] != [item.data for item in first]

    for index, item in enumerate(first):
        path, info, peak, rms = stats(item.data, tmp_path, f"{index}.wav")
        # Audible: far above the silence threshold and above a normal speech floor, and not clipped.
        assert peak > 2000 and rms > 500, item.name
        assert peak < audio_checks.CLIP_LEVEL, item.name
        # The source's duration, in the repository's usable-clip format.
        assert info.duration_seconds == pytest.approx(2.0, abs=0.001), item.name
        assert (info.sample_rate, info.channels, info.bits_per_sample) == (16000, 1, 16)
        assert audio_checks.clip_defects(path) == [], item.name
    # Different speakers and languages are different audio.
    assert len({item.data for item in first}) == len(first)


def test_placeholder_worker_needs_no_model_and_no_key():
    assert PlaceholderWorker.mode == "placeholder"
    assert PlaceholderWorker.worker_id == "placeholder-tone-v1"
    # The runtime dependencies are the storage client, the schema validator and the env file reader: no model, no provider SDK.
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text().split()
    assert sorted(item.split(">")[0] for item in requirements) == ["boto3", "jsonschema", "python-dotenv"]


def test_worker_refuses_a_source_that_does_not_match_the_recorded_hash(lab):
    lab.source()
    request = request_for(lab, source_sha256="0" * 64)
    with pytest.raises(ConfigError):
        PlaceholderWorker().run(request)


def test_worker_refuses_a_url_that_is_not_http(lab):
    with pytest.raises(Rejected):
        fetch_source("file:///etc/hosts", "0" * 64)


def test_source_must_be_a_wav_file_of_usable_length():
    with pytest.raises(Rejected):
        source_duration(b"this is not a RIFF file")
    with pytest.raises(Rejected):
        source_duration(synth.wav_bytes(array.array("h", [0] * 1000)))  # 0.06 seconds
    assert source_duration(synth.make_source(1.0)) == pytest.approx(1.0)
