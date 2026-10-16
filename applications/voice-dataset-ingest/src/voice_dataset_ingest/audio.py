"""Validate a clip and normalize it to 16 kHz, mono, 16-bit PCM, with the standard library only.

The checks are the ones in `shared/audio_checks.py`. A clip that is readable 16-bit PCM but has
another sample rate or channel count is normalized and then checked again; every other defect
(unreadable, truncated, too short, silent, clipped, not 16-bit PCM) rejects the clip.

The resampler averages source samples when it lowers the rate and interpolates linearly when it
raises it. It is meant for ingest checks and fixtures, not for production-grade audio conversion.
"""
from __future__ import annotations

import array
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

from .shared import audio_checks

TARGET_RATE = audio_checks.SAMPLE_RATE
# Defects that normalization can remove. Every other defect rejects the clip.
FIXABLE = {"sample_rate", "channels"}


@dataclass
class ClipResult:
    accepted: bool
    defects: list[str] = field(default_factory=list)
    duration: float = 0.0
    normalized: bool = False


def _mono(samples: array.array, channels: int) -> array.array:
    if channels == 1:
        return samples
    frames = len(samples) // channels
    mixed = array.array("h")
    for frame in range(frames):
        total = sum(samples[frame * channels + channel] for channel in range(channels))
        mixed.append(total // channels)
    return mixed


def _resample(samples: array.array, source_rate: int, target_rate: int) -> array.array:
    if source_rate == target_rate or not samples:
        return samples
    ratio = source_rate / target_rate
    count = int(len(samples) / ratio)
    out = array.array("h")
    if ratio > 1:
        for index in range(count):
            start = int(index * ratio)
            end = max(start + 1, min(len(samples), int((index + 1) * ratio)))
            window = samples[start:end]
            out.append(sum(window) // len(window))
        return out
    last = len(samples) - 1
    for index in range(count):
        position = index * ratio
        left = int(position)
        right = min(left + 1, last)
        fraction = position - left
        out.append(int(round(samples[left] * (1 - fraction) + samples[right] * fraction)))
    return out


def write_wav(path: Path, samples: array.array, rate: int = TARGET_RATE, channels: int = 1) -> None:
    data = array.array("h", samples)
    if sys.byteorder == "big":
        data.byteswap()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())


def normalize_file(source: Path, destination: Path) -> None:
    """Write a 16 kHz mono 16-bit copy of a readable 16-bit PCM file."""
    info = audio_checks.read_wav_info(source)
    samples = audio_checks.read_samples(source, info)
    samples = samples[: len(samples) - (len(samples) % max(1, info.channels))]
    write_wav(destination, _resample(_mono(samples, info.channels), info.sample_rate, TARGET_RATE))


def validate_and_normalize(source: Path, destination: Path) -> ClipResult:
    """Check `source`; when it is usable, write the normalized clip to `destination`."""
    defects = audio_checks.clip_defects(source)
    fatal = [item for item in defects if item not in FIXABLE]
    if fatal:
        return ClipResult(False, fatal)
    normalized = bool(defects)
    if normalized:
        normalize_file(source, destination)
        remaining = audio_checks.clip_defects(destination)
        if remaining:
            return ClipResult(False, remaining, normalized=True)
    else:
        destination.write_bytes(source.read_bytes())
    return ClipResult(True, [], audio_checks.read_wav_info(destination).duration_seconds, normalized)
