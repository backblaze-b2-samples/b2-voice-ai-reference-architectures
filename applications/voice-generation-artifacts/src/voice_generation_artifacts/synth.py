"""Deterministic synthetic audio for tests, demonstrations and the placeholder worker.

Everything is generated from a seed with the standard library: tones with a slow envelope stand in
for speech, so no recording of a person and no model is involved.
"""
from __future__ import annotations

import array
import io
import math
import random
import sys
import wave

RATE = 16000


def tone(seconds: float, base_hz: float, seed: str | int, amplitude: int = 9000, rate: int = RATE) -> array.array:
    """A voiced-sounding tone: two harmonics, a slow envelope and a little noise, seeded and repeatable."""
    rng = random.Random(seed)
    phase = rng.uniform(0.0, 2 * math.pi)
    out = array.array("h")
    for index in range(int(seconds * rate)):
        t = index / rate
        envelope = 0.55 + 0.45 * math.sin(2 * math.pi * 3.0 * t)
        value = math.sin(2 * math.pi * base_hz * t + phase) + 0.5 * math.sin(2 * math.pi * 2 * base_hz * t)
        out.append(int(max(-32768, min(32767, amplitude * envelope * value / 1.5 + rng.uniform(-60, 60)))))
    return out


def wav_bytes(samples: array.array, rate: int = RATE, channels: int = 1) -> bytes:
    """A 16-bit PCM WAV file holding `samples`."""
    data = array.array("h", samples)
    if sys.byteorder == "big":
        data.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())
    return buffer.getvalue()


def make_source(seconds: float = 4.0, seed: int = 7) -> bytes:
    """A synthetic source recording: 16 kHz mono 16-bit WAV, `seconds` long."""
    rng = random.Random(seed)
    return wav_bytes(tone(seconds, rng.uniform(110.0, 220.0), seed))
