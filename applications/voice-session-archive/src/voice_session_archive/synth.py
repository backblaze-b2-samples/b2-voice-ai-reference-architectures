"""Deterministic test and demonstration data.

- `write_recording`: a 16 kHz mono 16-bit WAV of audible tones that stands in for an Egress
  recording. It is not speech and no person is recorded.
- `consent_record`: a consent record that follows `schemas/consent-record.schema.json`.
- `egress_ended_event` and `event`: sample LiveKit webhook bodies, unsigned. A token is never stored:
  whoever sends the body signs it at run time.

Everything comes from a fixed seed, so the same call always writes the same bytes.
"""
from __future__ import annotations

import array
import json
import math
import random
import sys
import wave
from io import BytesIO
from pathlib import Path
from typing import Any

from . import storage

SAMPLE_RATE = 16000
FUTURE = "2099-01-01T00:00:00Z"
GRANTED = "2026-01-01T00:00:00Z"
NANOSECONDS = 10**9


def recording_bytes(seconds: float = 2.0, seed: int = 7) -> bytes:
    """WAV bytes: two alternating tones with a little seeded noise, at about -12 dBFS."""
    rng = random.Random(seed)
    frames = int(seconds * SAMPLE_RATE)
    tones = (220.0 + rng.randrange(0, 60), 330.0 + rng.randrange(0, 60))
    samples = array.array("h")
    for index in range(frames):
        tone = tones[(index // (SAMPLE_RATE // 2)) % 2]
        value = 0.25 * math.sin(2 * math.pi * tone * index / SAMPLE_RATE) + rng.uniform(-0.01, 0.01)
        samples.append(int(max(-1.0, min(1.0, value)) * 32767))
    if sys.byteorder == "big":
        samples.byteswap()
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()


def write_recording(path: Path, seconds: float = 2.0, seed: int = 7) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(recording_bytes(seconds, seed))
    return path


def consent_record(consent_id: str, speaker_id: str, status: str = "active",
                   permitted_uses: tuple[str, ...] = ("evaluation", "analytics", "training"),
                   expires_at: str | None = FUTURE) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "1.0.0",
        "consent_id": consent_id,
        "subject": {"type": "synthetic", "speaker_id": speaker_id,
                    "description": "Synthetic participant used by the tests and the demo."},
        "status": status,
        "granted_at": GRANTED,
        "permitted_uses": list(permitted_uses),
        "license": {"identifier": "LicenseRef-synthetic-test-data"},
        "evidence": {"kind": "not-applicable-synthetic", "reference": "synthetic"},
    }
    if expires_at:
        record["expires_at"] = expires_at
    return record


def event(name: str, event_id: str, created_at: int, **fields: Any) -> dict[str, Any]:
    """A LiveKit webhook body with the documented `event`, `id` and `createdAt` fields."""
    return {"event": name, "id": event_id, "createdAt": created_at, **fields}


def egress_ended_event(event_id: str, room: str, files: list[tuple[str, int]], started_at: int,
                       seconds: float, status: str = "EGRESS_COMPLETE", egress_id: str = "EG_synthetic") -> dict[str, Any]:
    """An `egress_ended` body. `files` holds (key, size); times are in nanoseconds, as LiveKit sends them.

    64-bit integers are written as decimal strings, the way protobuf JSON encodes them.
    """
    start_ns = started_at * NANOSECONDS
    end_ns = start_ns + int(seconds * NANOSECONDS)
    results = [{"filename": key, "startedAt": str(start_ns), "endedAt": str(end_ns),
                "duration": str(end_ns - start_ns), "size": str(size), "location": f"s3://bucket/{key}"}
               for key, size in files]
    info = {"egressId": egress_id, "roomId": "RM_synthetic", "roomName": room, "status": status,
            "startedAt": str(start_ns), "endedAt": str(end_ns), "fileResults": results}
    if status != "EGRESS_COMPLETE":
        info["error"] = "synthetic failure"
    return event("egress_ended", event_id, int(started_at + seconds), egressInfo=info)


def build_demo(out: Path, room: str, session_id: str, seconds: float = 4.0, seed: int = 7,
               started_at: int = 1767225600) -> dict[str, Any]:
    """Write a recording, a consent record and an unsigned egress_ended body into `out`."""
    out.mkdir(parents=True, exist_ok=True)
    recording = write_recording(out / "recording.wav", seconds, seed)
    key = storage.session_prefix(room, session_id) + "recording.wav"
    consent = consent_record("consent_demo_caller", "caller_1")
    (out / "consent_demo_caller.json").write_text(json.dumps(consent, indent=2) + "\n")
    body = egress_ended_event(f"EV_{session_id}", room, [(key, recording.stat().st_size)], started_at, seconds)
    (out / "egress-ended.json").write_text(json.dumps(body) + "\n")
    return {"recording": str(recording), "recording_key": key, "consent": str(out / "consent_demo_caller.json"),
            "consent_id": consent["consent_id"], "event_body": str(out / "egress-ended.json")}
