#!/usr/bin/env python3
"""Generate testdata/synthetic-speech: a small synthetic speech dataset and session.

Every voice in the output is produced by a text-to-speech model from the sentences in
this file. No recording of a person is used. The generated files are committed, so
tests and CI never run this script and never need the speech model.

Speech backends, tried in this order:

1. `kokoro-onnx` with the Kokoro-82M model files (Apache-2.0) in --models-dir.
2. `kokoro` with `espeak-ng` installed on the system.

Install what the first backend needs with `pip install -r requirements-fixtures.txt`
(see testdata/synthetic-speech/README.md for the model files). With no backend the
script prints what is missing and exits with status 2.

Usage: python tools/make_fixtures.py [--models-dir DIR] [--output DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_checks  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "testdata" / "synthetic-speech"
DEFAULT_MODELS_DIR = ROOT / ".kokoro-models"
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"

GENERATED_DIRS = ("audio", "audio-invalid", "session", "consents")
GENERATED_FILES = ("manifest.jsonl", "invalid-manifest.jsonl", "invalid-expected.json", "croissant.json")

PEAK = 0.8
GAP_SECONDS = 0.35
FIXTURE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
LICENSE_ID = "MIT"

# speaker id -> TTS voice and the role the speaker plays in the session
SPEAKERS = {
    "spk_a": {"voice": "af_heart", "role": "agent"},
    "spk_b": {"voice": "am_michael", "role": "caller"},
}

# (clip id, speaker, split, text)
DATASET_UTTERANCES = [
    ("utt_0001", "spk_a", "train", "The delivery window opens at nine in the morning."),
    ("utt_0002", "spk_a", "train", "Please confirm the account number before we continue."),
    ("utt_0003", "spk_a", "train", "The report is ready and has been saved to the shared folder."),
    ("utt_0004", "spk_b", "train", "I would like to change the date of my appointment."),
    ("utt_0005", "spk_b", "train", "Could you repeat the last step more slowly?"),
    ("utt_0006", "spk_b", "train", "The package arrived yesterday and everything looks fine."),
    ("utt_0007", "spk_a", "dev", "Our next maintenance window is scheduled for Thursday evening."),
    ("utt_0008", "spk_b", "test", "Thank you for your help, that solves the problem."),
]

SESSION_TURNS = [
    ("spk_a", "Hello, thanks for calling. How can I help you today?"),
    ("spk_b", "Hi, I need to reschedule a delivery for next week."),
    ("spk_a", "Of course. Which day works best for you?"),
    ("spk_b", "Tuesday afternoon would be perfect, thank you."),
]

SESSION_ID = "synthetic-session-0001"

# A synthesizer maps (text, voice) to (float samples in [-1, 1], sample rate in Hz).
Synthesizer = Callable[[str, str], "tuple[np.ndarray, int]"]


class BackendError(RuntimeError):
    """No speech backend is available."""


# --- audio helpers ----------------------------------------------------------------

def resample(samples: np.ndarray, source_rate: int, target_rate: int = audio_checks.SAMPLE_RATE) -> np.ndarray:
    """Band-limited rational resampling with a Kaiser-windowed sinc filter."""
    samples = np.asarray(samples, dtype=np.float64)
    if source_rate == target_rate:
        return samples
    divisor = int(np.gcd(source_rate, target_rate))
    up, down = target_rate // divisor, source_rate // divisor
    stuffed = np.zeros(len(samples) * up)
    stuffed[::up] = samples * up
    cutoff = 0.45 / max(up, down)
    taps = 16 * max(up, down) + 1
    centre = (taps - 1) / 2
    index = np.arange(taps) - centre
    kernel = 2 * cutoff * np.sinc(2 * cutoff * index) * np.kaiser(taps, 8.0)
    kernel /= kernel.sum()
    return np.convolve(stuffed, kernel, mode="same")[::down]


def to_int16(samples: np.ndarray, peak: float = PEAK) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float64)
    top = float(np.max(np.abs(samples))) or 1.0
    return np.round(samples / top * peak * 32767).astype("<i2")


def write_wav(path: Path, samples: np.ndarray, rate: int = audio_checks.SAMPLE_RATE, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def header_duration(path: Path) -> float:
    return round(audio_checks.read_wav_info(path).duration_seconds, 3)


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def dump_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))


def timestamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- backends ---------------------------------------------------------------------

def load_synthesizer(models_dir: Path) -> tuple[Synthesizer, str]:
    """Return a synthesizer and the name of the backend, or raise BackendError."""
    problems: list[str] = []
    try:
        from kokoro_onnx import Kokoro
    except ImportError:
        problems.append("kokoro-onnx is not installed (pip install -r requirements-fixtures.txt)")
    else:
        model, voices = models_dir / MODEL_FILE, models_dir / VOICES_FILE
        if model.is_file() and voices.is_file():
            engine = Kokoro(str(model), str(voices))

            def synthesize_onnx(text: str, voice: str) -> tuple[np.ndarray, int]:
                samples, rate = engine.create(text, voice=voice, speed=1.0, lang="en-us")
                return np.asarray(samples, dtype=np.float32), int(rate)

            return synthesize_onnx, "kokoro-onnx"
        problems.append(f"{MODEL_FILE} and {VOICES_FILE} not found in {models_dir}")
    try:
        from kokoro import KPipeline
    except ImportError:
        problems.append("the kokoro package is not installed")
    else:
        pipeline = KPipeline(lang_code="a")

        def synthesize_pipeline(text: str, voice: str) -> tuple[np.ndarray, int]:
            parts = []
            for _, _, audio in pipeline(text, voice=voice):
                parts.append(np.asarray(audio.numpy() if hasattr(audio, "numpy") else audio, dtype=np.float32))
            return np.concatenate(parts), 24000

        return synthesize_pipeline, "kokoro"
    raise BackendError("no speech backend available: " + "; ".join(problems))


# --- fixture construction ---------------------------------------------------------

def consent_records() -> dict[str, dict]:
    records = {}
    for speaker in SPEAKERS:
        consent_id = f"consent_{speaker}"
        records[consent_id] = {
            "schema_version": "1.0.0",
            "consent_id": consent_id,
            "subject": {
                "type": "synthetic",
                "speaker_id": speaker,
                "description": "Voice generated by a text-to-speech model. No person is recorded.",
            },
            "status": "active",
            "granted_at": timestamp(FIXTURE_TIME),
            "permitted_uses": ["training", "evaluation", "analytics", "generation", "redistribution"],
            "license": {"identifier": LICENSE_ID},
            "evidence": {"kind": "not-applicable-synthetic", "reference": "testdata/synthetic-speech/README.md"},
        }
    return records


def build_dataset(out: Path, clips: dict[str, np.ndarray]) -> list[dict]:
    lines = []
    for clip_id, speaker, split, text in DATASET_UTTERANCES:
        path = out / "audio" / f"{clip_id}.wav"
        write_wav(path, clips[clip_id])
        lines.append({
            "audio_filepath": f"audio/{clip_id}.wav",
            "duration": header_duration(path),
            "text": text,
            "speaker": speaker,
            "lang": "en",
            "sample_rate": audio_checks.SAMPLE_RATE,
            "split": split,
            "consent_id": f"consent_{speaker}",
            "sha256": sha256(path),
        })
    dump_jsonl(out / "manifest.jsonl", lines)
    return lines


def build_invalid(out: Path, clips: dict[str, np.ndarray]) -> None:
    """Write clips that each break exactly one rule, with the defect each one must trigger."""
    source = clips["utt_0001"]
    source_line = {
        "text": DATASET_UTTERANCES[0][3], "speaker": "spk_a", "lang": "en", "split": "train", "consent_id": "consent_spk_a",
    }
    folder = out / "audio-invalid"
    lines: list[dict] = []
    expected: dict[str, list[str]] = {}

    def add(name: str, defect: str, duration: float | None = None, rate: int = audio_checks.SAMPLE_RATE) -> None:
        path = folder / name
        lines.append({
            "audio_filepath": f"audio-invalid/{name}",
            "duration": header_duration(path) if duration is None else duration,
            **source_line,
            "sample_rate": rate,
        })
        expected[f"audio-invalid/{name}"] = [defect]

    write_wav(folder / "bad_sample_rate_8k.wav", source[: len(source) // 2 * 2].reshape(-1, 2).mean(axis=1).astype("<i2"), rate=8000)
    add("bad_sample_rate_8k.wav", "sample_rate", rate=8000)
    write_wav(folder / "bad_stereo.wav", np.repeat(source[: int(1.5 * audio_checks.SAMPLE_RATE)], 2), channels=2)
    add("bad_stereo.wav", "channels")
    write_wav(folder / "bad_silent.wav", np.zeros(audio_checks.SAMPLE_RATE, dtype="<i2"))
    add("bad_silent.wav", "silent")
    write_wav(folder / "bad_clipped.wav", np.clip(source.astype(np.int64) * 6, -32768, 32767).astype("<i2"))
    add("bad_clipped.wav", "clipped")
    write_wav(folder / "bad_too_short.wav", source[: int(0.2 * audio_checks.SAMPLE_RATE)])
    add("bad_too_short.wav", "too_short")
    whole = folder / "bad_truncated.wav"
    write_wav(whole, source)
    data = whole.read_bytes()
    whole.write_bytes(data[: 44 + (len(data) - 44) * 6 // 10 // 2 * 2])
    add("bad_truncated.wav", "truncated")
    write_wav(folder / "bad_duration_mismatch.wav", source)
    add("bad_duration_mismatch.wav", "duration_mismatch", duration=header_duration(folder / "bad_duration_mismatch.wav") + 1.5)
    lines.append({
        "audio_filepath": "audio/utt_0001.wav",
        "duration": header_duration(out / "audio" / "utt_0001.wav"),
        **{**source_line, "text": ""},
        "sample_rate": audio_checks.SAMPLE_RATE,
    })
    expected["audio/utt_0001.wav"] = ["schema"]
    dump_jsonl(out / "invalid-manifest.jsonl", lines)
    dump_json(out / "invalid-expected.json", expected)


def build_session(out: Path, voices: dict[str, np.ndarray], consents: dict[str, dict]) -> None:
    folder = out / "session"
    gap = np.zeros(int(GAP_SECONDS * audio_checks.SAMPLE_RATE), dtype=np.int64)
    pieces = [(speaker, voices[f"turn_{index}"]) for index, (speaker, _) in enumerate(SESSION_TURNS)]
    tracks = {speaker: [] for speaker in SPEAKERS}
    for speaker, samples in pieces:
        for name in SPEAKERS:
            tracks[name].append(samples.astype(np.int64) if name == speaker else np.zeros(len(samples), dtype=np.int64))
            tracks[name].append(gap)
    full = {name: np.concatenate(parts) for name, parts in tracks.items()}
    mix = np.clip(sum(full.values()), -32768, 32767)
    files = {"session_mix.wav": ("mixed", None, mix)}
    for name in SPEAKERS:
        files[f"{name}.wav"] = ("participant-track", name, full[name])
    recordings = []
    for filename, (kind, participant, samples) in files.items():
        path = folder / filename
        write_wav(path, samples.astype("<i2"))
        item = {
            "kind": kind,
            "path": filename,
            "size": path.stat().st_size,
            "sha256": sha256(path),
            "encoding_format": "audio/wav",
            "sample_rate_hz": audio_checks.SAMPLE_RATE,
            "channels": audio_checks.CHANNELS,
            "duration_seconds": header_duration(path),
        }
        if participant:
            item["participant_id"] = participant
        recordings.append(item)
    defects = {item["path"]: audio_checks.clip_defects(folder / item["path"], item["duration_seconds"]) for item in recordings}
    flat = {code for codes in defects.values() for code in codes}
    quality = [
        ("audio-format", not flat & {"unreadable", "bit_depth", "sample_rate", "channels", "truncated", "duration_mismatch"}),
        ("not-silent", "silent" not in flat),
        ("not-clipped", "clipped" not in flat),
    ]
    safety = [
        ("consent-active", all(record["status"] == "active" for record in consents.values())),
        ("synthetic-speakers-only", all(record["subject"]["type"] == "synthetic" for record in consents.values())),
    ]

    def group(checks: list[tuple[str, bool]]) -> dict:
        return {
            "status": "pass" if all(ok for _, ok in checks) else "fail",
            "checks": [{"name": name, "result": "pass" if ok else "fail"} for name, ok in checks],
        }

    duration = recordings[0]["duration_seconds"]
    ended = FIXTURE_TIME + timedelta(seconds=round(duration))
    quality_group, safety_group = group(quality), group(safety)
    passed = quality_group["status"] == "pass" and safety_group["status"] == "pass"
    manifest = {
        "schema_version": "1.0.0",
        "session_id": SESSION_ID,
        "started_at": timestamp(FIXTURE_TIME),
        "ended_at": timestamp(ended),
        "duration_seconds": duration,
        "source": {"platform": "other", "capture": "application-copy"},
        "participants": [{"participant_id": name, "role": details["role"]} for name, details in SPEAKERS.items()],
        "recordings": recordings,
        "consent_ids": sorted(consents),
        "retention": {"class": "short", "delete_after": timestamp(FIXTURE_TIME + timedelta(days=30))},
        "evaluation": {
            "status": "passed" if passed else "failed",
            "evaluated_at": timestamp(ended),
            "evaluator": {"name": "fixture-checks", "version": "1"},
            "quality": quality_group,
            "safety": safety_group,
            "eligible_for": ["human-review", "analytics", "model-improvement"] if passed else [],
        },
    }
    dump_json(folder / "session-manifest.json", manifest)


def build_croissant(out: Path, lines: list[dict]) -> None:
    context = {
        "@language": "en",
        "@vocab": "https://schema.org/",
        "citeAs": "cr:citeAs",
        "column": "cr:column",
        "conformsTo": "dct:conformsTo",
        "cr": "http://mlcommons.org/croissant/",
        "data": {"@id": "cr:data", "@type": "@json"},
        "dataType": {"@id": "cr:dataType", "@type": "@vocab"},
        "dct": "http://purl.org/dc/terms/",
        "extract": "cr:extract",
        "field": "cr:field",
        "fileProperty": "cr:fileProperty",
        "fileObject": "cr:fileObject",
        "fileSet": "cr:fileSet",
        "format": "cr:format",
        "includes": "cr:includes",
        "jsonPath": "cr:jsonPath",
        "key": "cr:key",
        "md5": "cr:md5",
        "recordSet": "cr:recordSet",
        "references": "cr:references",
        "regex": "cr:regex",
        "repeated": "cr:repeated",
        "samplingRate": "cr:samplingRate",
        "sc": "https://schema.org/",
        "separator": "cr:separator",
        "source": "cr:source",
        "subField": "cr:subField",
        "transform": "cr:transform",
    }

    def field(name: str, data_type: str, path: str, description: str) -> dict:
        return {
            "@type": "cr:Field",
            "@id": f"utterances/{name}",
            "name": f"utterances/{name}",
            "description": description,
            "dataType": data_type,
            "source": {"fileObject": {"@id": "manifest.jsonl"}, "extract": {"jsonPath": path}},
        }

    document = {
        "@context": context,
        "@type": "sc:Dataset",
        "conformsTo": "http://mlcommons.org/croissant/1.0",
        "name": "synthetic-speech-fixtures",
        "description": "Small synthetic English speech dataset for testing voice data pipelines. Every clip is generated speech; no recording of a person is included. It contains two pseudonymous speakers, a NeMo-style manifest, one two-speaker session and deliberately invalid clips for validator tests.",
        "license": "https://opensource.org/license/mit",
        "version": "1.0.0",
        "inLanguage": "en",
        "creator": {"@type": "sc:Organization", "name": "Backblaze, Inc."},
        "distribution": [
            {
                "@type": "cr:FileObject",
                "@id": "manifest.jsonl",
                "name": "manifest.jsonl",
                "contentUrl": "manifest.jsonl",
                "encodingFormat": "application/jsonlines",
                "sha256": sha256(out / "manifest.jsonl"),
            },
            *[
                {
                    "@type": "cr:FileObject",
                    "@id": line["audio_filepath"],
                    "name": line["audio_filepath"],
                    "contentUrl": line["audio_filepath"],
                    "encodingFormat": "audio/wav",
                    "sha256": line["sha256"],
                }
                for line in lines
            ],
        ],
        "recordSet": [
            {
                "@type": "cr:RecordSet",
                "@id": "utterances",
                "name": "utterances",
                "description": f"One record per clip in manifest.jsonl ({len(lines)} clips).",
                "field": [
                    field("audio_filepath", "sc:Text", "$.audio_filepath", "Path of the clip relative to the dataset root."),
                    field("duration", "sc:Float", "$.duration", "Duration in seconds, equal to the WAV header duration."),
                    field("text", "sc:Text", "$.text", "Transcript of the clip."),
                    field("speaker", "sc:Text", "$.speaker", "Pseudonymous speaker id."),
                    field("split", "sc:Text", "$.split", "train, dev or test."),
                ],
            }
        ],
    }
    dump_json(out / "croissant.json", document)


def build_fixtures(out: Path, synthesize: Synthesizer) -> None:
    """Generate every fixture file under `out` with the given synthesizer."""
    for name in GENERATED_DIRS:
        shutil.rmtree(out / name, ignore_errors=True)
    for name in GENERATED_FILES:
        (out / name).unlink(missing_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    cache: dict[tuple[str, str], np.ndarray] = {}

    def speak(text: str, speaker: str) -> np.ndarray:
        key = (text, speaker)
        if key not in cache:
            samples, rate = synthesize(text, SPEAKERS[speaker]["voice"])
            cache[key] = to_int16(resample(samples, rate))
        return cache[key]

    clips = {clip_id: speak(text, speaker) for clip_id, speaker, _, text in DATASET_UTTERANCES}
    turns = {f"turn_{index}": speak(text, speaker) for index, (speaker, text) in enumerate(SESSION_TURNS)}
    consents = consent_records()
    for consent_id, record in consents.items():
        dump_json(out / "consents" / f"{consent_id}.json", record)
    lines = build_dataset(out, clips)
    build_invalid(out, clips)
    build_session(out, turns, consents)
    build_croissant(out, lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR, help="folder with the Kokoro-82M ONNX model files")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="fixture folder to write")
    args = parser.parse_args(argv)
    try:
        synthesize, backend = load_synthesizer(args.models_dir)
    except BackendError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    build_fixtures(args.output, synthesize)
    print(f"wrote fixtures to {args.output} with the {backend} backend")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
