"""The worker that turns a source into candidate outputs.

`Worker` is the interface a generation backend implements. This repository ships one
implementation, `PlaceholderWorker`: it uses no model, no network service and no provider key, and
it produces synthetic placeholder audio, tones that are audible but are not speech and do not
translate or dub anything. There is no adapter for a hosted provider or a self-hosted model.

Like a hosted provider, the worker receives a presigned GET URL for the source, never a key. It
checks that what it downloaded is the object the job recorded, reads the duration from the WAV
header and writes one stem per speaker and language, plus a mix of the stems for each language,
all as 16 kHz mono 16-bit WAV files of the source's duration.
"""
from __future__ import annotations

import array
import hashlib
import io
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from typing import Protocol

from . import synth
from .errors import ConfigError, Rejected
from .shared import audio_checks

WORKER_ID = "placeholder-tone-v1"
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_SECONDS = 120.0


@dataclass(frozen=True)
class WorkerRequest:
    job_id: str
    kind: str
    source_url: str  # presigned GET URL: a bearer credential, never stored or logged
    source_sha256: str
    source_language: str
    target_languages: tuple[str, ...]
    speakers: tuple[str, ...]


@dataclass(frozen=True)
class WorkerOutput:
    kind: str  # "candidate" (the mix of a language) or "stem" (one speaker in one language)
    language: str
    speaker_id: str | None
    name: str  # path under the attempt prefix, for example "es/spk_a.wav"
    data: bytes


class Worker(Protocol):
    worker_id: str
    mode: str  # a value of provider.mode in the job manifest schema

    def run(self, request: WorkerRequest) -> list[WorkerOutput]:
        ...


def fetch_source(url: str, expected_sha256: str) -> bytes:
    """Download the source through its presigned URL and check it is the object the job recorded."""
    if urllib.parse.urlparse(url).scheme not in {"http", "https"}:
        raise Rejected("the source URL must be http or https")
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (scheme checked above)
            data = response.read(MAX_SOURCE_BYTES + 1)
    except urllib.error.URLError as error:
        raise ConfigError(f"the source could not be downloaded through its presigned URL: {error.reason}") from error
    if len(data) > MAX_SOURCE_BYTES:
        raise Rejected(f"the placeholder worker accepts sources up to {MAX_SOURCE_BYTES} bytes")
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ConfigError("the downloaded source does not match the sha256 recorded for the job")
    return data


def source_duration(data: bytes) -> float:
    """Duration in seconds of a PCM WAV file, from its header."""
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            frames, rate = handle.getnframes(), handle.getframerate()
    except (wave.Error, EOFError) as error:
        raise Rejected(f"the source is not a readable PCM WAV file: {error}") from error
    duration = frames / rate if rate else 0.0
    if duration < audio_checks.MIN_DURATION_SECONDS:
        raise Rejected(f"the source must be at least {audio_checks.MIN_DURATION_SECONDS} seconds long")
    if duration > MAX_SOURCE_SECONDS:
        raise Rejected(f"the placeholder worker accepts sources up to {MAX_SOURCE_SECONDS:.0f} seconds")
    return duration


class PlaceholderWorker:
    """Synthetic placeholder audio from a seed. No model, no network service, no provider key."""

    worker_id = WORKER_ID
    mode = "placeholder"

    def __init__(self, seed: int = 7):
        self.seed = seed

    def stem(self, seconds: float, language_index: int, speaker_index: int, language: str, speaker: str) -> array.array:
        """One speaker in one language: a tone whose pitch depends on both, repeatable for a given seed."""
        base_hz = (140.0 + 45.0 * speaker_index) * (1.0 + 0.12 * language_index)
        return synth.tone(seconds, base_hz, f"{self.seed}:{language}:{speaker}")

    def run(self, request: WorkerRequest) -> list[WorkerOutput]:
        seconds = source_duration(fetch_source(request.source_url, request.source_sha256))
        outputs: list[WorkerOutput] = []
        for language_index, language in enumerate(request.target_languages):
            stems = [self.stem(seconds, language_index, speaker_index, language, speaker)
                     for speaker_index, speaker in enumerate(request.speakers)]
            for speaker, samples in zip(request.speakers, stems):
                outputs.append(WorkerOutput("stem", language, speaker, f"{language}/{speaker}.wav", synth.wav_bytes(samples)))
            count = len(stems)
            mix = array.array("h", (sum(values) // count for values in zip(*stems)))
            outputs.append(WorkerOutput("candidate", language, None, f"{language}/mix.wav", synth.wav_bytes(mix)))
        return outputs
