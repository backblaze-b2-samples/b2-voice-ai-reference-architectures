#!/usr/bin/env python3
"""Inspect WAV files without any audio dependency.

The checks read the RIFF header and the samples with the standard library only, so
they run anywhere the tests run. They define what a usable clip is in this repository:
16 kHz, mono, 16-bit PCM, at least half a second long, not silent, not clipped,
complete, and with the duration its manifest line declares.

Usage: python tools/audio_checks.py FILE.wav [DECLARED_DURATION_SECONDS]
"""
from __future__ import annotations

import array
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000
CHANNELS = 1
BITS_PER_SAMPLE = 16
MIN_DURATION_SECONDS = 0.5
DURATION_TOLERANCE_SECONDS = 0.001
SILENCE_PEAK = 32  # a peak this low or lower, out of 32768, counts as silence
CLIP_LEVEL = 32000
CLIPPED_FRACTION = 0.005


class WavError(ValueError):
    """The file is not a readable RIFF/WAVE file."""


@dataclass(frozen=True)
class WavInfo:
    format_tag: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    declared_data_bytes: int
    actual_data_bytes: int
    data_offset: int

    @property
    def block_align(self) -> int:
        return max(1, self.channels * self.bits_per_sample // 8)

    @property
    def duration_seconds(self) -> float:
        """Duration according to the header, which is what a manifest line declares."""
        return self.declared_data_bytes / self.block_align / self.sample_rate


def read_wav_info(path: Path) -> WavInfo:
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise WavError(f"{path.name}: not a RIFF/WAVE file")
        fmt = None
        while True:
            chunk = handle.read(8)
            if len(chunk) < 8:
                raise WavError(f"{path.name}: no data chunk")
            name, length = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            if name == b"fmt ":
                body = handle.read(16)
                if len(body) < 16:
                    raise WavError(f"{path.name}: fmt chunk is truncated")
                fmt = struct.unpack("<HHIIHH", body)
                handle.seek(length - 16 + (length & 1), 1)
            elif name == b"data":
                if fmt is None:
                    raise WavError(f"{path.name}: data chunk before fmt chunk")
                offset = handle.tell()
                return WavInfo(
                    format_tag=fmt[0],
                    channels=fmt[1],
                    sample_rate=fmt[2],
                    bits_per_sample=fmt[5],
                    declared_data_bytes=length,
                    actual_data_bytes=max(0, min(length, size - offset)),
                    data_offset=offset,
                )
            else:
                handle.seek(length + (length & 1), 1)


def read_samples(path: Path, info: WavInfo) -> array.array:
    """Return the 16-bit samples that are actually present in the file."""
    with Path(path).open("rb") as handle:
        handle.seek(info.data_offset)
        raw = handle.read(info.actual_data_bytes)
    raw = raw[: len(raw) - (len(raw) % 2)]
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def clip_defects(path: Path, declared_duration: float | None = None) -> list[str]:
    """Return the defect codes of a clip; an empty list means the clip is usable.

    Codes: unreadable, bit_depth, sample_rate, channels, truncated, too_short, silent,
    clipped, duration_mismatch.
    """
    try:
        info = read_wav_info(path)
    except (WavError, OSError):
        return ["unreadable"]
    defects: list[str] = []
    if info.format_tag != 1 or info.bits_per_sample != BITS_PER_SAMPLE:
        defects.append("bit_depth")
    if info.sample_rate != SAMPLE_RATE:
        defects.append("sample_rate")
    if info.channels != CHANNELS:
        defects.append("channels")
    if info.actual_data_bytes < info.declared_data_bytes:
        defects.append("truncated")
    if info.duration_seconds < MIN_DURATION_SECONDS:
        defects.append("too_short")
    if info.format_tag == 1 and info.bits_per_sample == BITS_PER_SAMPLE:
        samples = read_samples(path, info)
        if samples:
            peak = max(max(samples), -min(samples))
            if peak <= SILENCE_PEAK:
                defects.append("silent")
            loud = sum(1 for value in samples if abs(value) >= CLIP_LEVEL)
            if loud / len(samples) >= CLIPPED_FRACTION:
                defects.append("clipped")
    if declared_duration is not None and abs(info.duration_seconds - declared_duration) > DURATION_TOLERANCE_SECONDS:
        defects.append("duration_mismatch")
    return defects


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not 1 <= len(args) <= 2:
        print(__doc__, file=sys.stderr)
        return 2
    declared = float(args[1]) if len(args) == 2 else None
    defects = clip_defects(Path(args[0]), declared)
    print(", ".join(defects) if defects else "ok")
    return 1 if defects else 0


if __name__ == "__main__":
    raise SystemExit(main())
