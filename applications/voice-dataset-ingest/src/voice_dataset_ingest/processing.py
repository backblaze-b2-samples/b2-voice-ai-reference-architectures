"""Validate and normalize clips, and write the batch manifest last.

For each clip in `incoming/contributions/<batch>/`:

1. read its sidecar and check the consent record again;
2. validate the audio and normalize it to 16 kHz, mono, 16-bit PCM;
3. write the accepted clip to `work/candidates/<batch>/audio/` or, when it is rejected, a copy and the
   reason to `work/candidates/<batch>/rejected/`;
4. record the decision in `work/candidates/<batch>/results/<clip_id>.json`.

The NeMo manifest `work/candidates/<batch>/manifest.jsonl` is written last, from the recorded
decisions, and every line is validated against `dataset-line.schema.json` first.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from . import audio, consent, storage
from .errors import ConsentError

MAX_CLIP_BYTES = 50 * 1024 * 1024
CLIP_KEY = re.compile(r"^incoming/contributions/([a-z0-9][a-z0-9._-]*)/([a-z0-9][a-z0-9._-]*)\.wav$")


def _results_key(batch: str, clip_id: str) -> str:
    return f"{storage.candidate_prefix(batch)}results/{clip_id}.json"


def _reject(client: Any, bucket: str, batch: str, clip_id: str, reasons: list[str],
            original: bytes | None, sidecar: dict[str, Any] | None) -> dict[str, Any]:
    prefix = f"{storage.candidate_prefix(batch)}rejected/{clip_id}"
    if original is not None:
        storage.put_bytes(client, bucket, f"{prefix}.wav", original, "audio/wav")
    document = {"batch": batch, "clip_id": clip_id, "reasons": reasons}
    if sidecar:
        document["consent_id"] = sidecar.get("consent_id")
    storage.put_json(client, bucket, f"{prefix}.json", document)
    storage.put_json(client, bucket, _results_key(batch, clip_id), {"status": "rejected", **document})
    return {"clip_id": clip_id, "status": "rejected", "reasons": reasons}


def process_clip(client: Any, bucket: str, batch: str, clip_id: str,
                 now: datetime | None = None) -> dict[str, Any]:
    """Decide one clip and write the decision. Returns a summary with its status and reasons."""
    key = storage.clip_key(batch, clip_id)
    try:
        sidecar = storage.get_json(client, bucket, storage.sidecar_key(batch, clip_id))
    except ClientError as error:
        if not storage.is_missing(error):
            raise
        return _reject(client, bucket, batch, clip_id, ["missing_sidecar"], None, None)
    except ValueError:
        return _reject(client, bucket, batch, clip_id, ["invalid_sidecar"], None, None)
    needed = ("speaker", "consent_id", "text", "lang", "split")
    if not isinstance(sidecar, dict) or any(not isinstance(sidecar.get(name), str) for name in needed):
        return _reject(client, bucket, batch, clip_id, ["invalid_sidecar"], None, None)

    size = client.head_object(Bucket=bucket, Key=key)["ContentLength"]
    if size > MAX_CLIP_BYTES:
        return _reject(client, bucket, batch, clip_id, ["too_large"], None, sidecar)
    original = storage.get_bytes(client, bucket, key)

    try:
        consent.require_consent(client, bucket, sidecar["consent_id"], sidecar["speaker"], now)
    except ConsentError as error:
        return _reject(client, bucket, batch, clip_id, [f"consent_{error.code}"], original, sidecar)

    with tempfile.TemporaryDirectory(prefix="ingest-") as tmp:
        source = Path(tmp) / "source.wav"
        target = Path(tmp) / "normalized.wav"
        source.write_bytes(original)
        result = audio.validate_and_normalize(source, target)
        if not result.accepted:
            return _reject(client, bucket, batch, clip_id, result.defects, original, sidecar)
        normalized = target.read_bytes()

    line = {
        "audio_filepath": f"audio/{clip_id}.wav",
        "duration": round(result.duration, 3),
        "text": sidecar["text"],
        "speaker": sidecar["speaker"],
        "lang": sidecar["lang"],
        "sample_rate": audio.TARGET_RATE,
        "split": sidecar["split"],
        "consent_id": sidecar["consent_id"],
        "sha256": hashlib.sha256(normalized).hexdigest(),
    }
    problems = sorted(consent.schema_validator("dataset-line.schema.json").iter_errors(line), key=str)
    if problems:
        return _reject(client, bucket, batch, clip_id, [f"invalid_line: {problems[0].message}"], original, sidecar)
    storage.put_bytes(client, bucket, f"{storage.candidate_prefix(batch)}audio/{clip_id}.wav", normalized, "audio/wav")
    storage.put_json(client, bucket, _results_key(batch, clip_id),
                     {"status": "accepted", "batch": batch, "clip_id": clip_id, "normalized": result.normalized,
                      "line": line})
    return {"clip_id": clip_id, "status": "accepted", "normalized": result.normalized}


def write_manifest(client: Any, bucket: str, batch: str) -> dict[str, Any]:
    """Write `manifest.jsonl` from the recorded decisions. It is always the last write of a run."""
    prefix = f"{storage.candidate_prefix(batch)}results/"
    validator = consent.schema_validator("dataset-line.schema.json")
    lines: list[str] = []
    accepted = rejected = 0
    for key in sorted(storage.list_keys(client, bucket, prefix)):
        decision = storage.get_json(client, bucket, key)
        if decision.get("status") != "accepted":
            rejected += 1
            continue
        line = decision["line"]
        problems = sorted(validator.iter_errors(line), key=str)
        if problems:
            raise ValueError(f"{key}: manifest line does not match its schema: {problems[0].message}")
        lines.append(json.dumps(line, sort_keys=True))
        accepted += 1
    body = ("\n".join(lines) + "\n").encode() if lines else b""
    manifest_key = f"{storage.candidate_prefix(batch)}manifest.jsonl"
    storage.put_bytes(client, bucket, manifest_key, body, "application/jsonl")
    return {"manifest": manifest_key, "accepted": accepted, "rejected": rejected}


def process_batch(client: Any, bucket: str, batch: str, now: datetime | None = None) -> dict[str, Any]:
    """Process every clip of a batch, write the validation report, then the manifest last."""
    clips = sorted(key for key in storage.list_keys(client, bucket, storage.contribution_prefix(batch))
                   if CLIP_KEY.match(key))
    outcomes = [process_clip(client, bucket, batch, CLIP_KEY.match(key).group(2), now) for key in clips]
    report = {"batch": batch,
              "accepted": sum(item["status"] == "accepted" for item in outcomes),
              "rejected": sum(item["status"] == "rejected" for item in outcomes),
              "rejects": [{"clip_id": item["clip_id"], "reasons": item["reasons"]}
                          for item in outcomes if item["status"] == "rejected"]}
    storage.put_json(client, bucket, f"{storage.EVALUATIONS}validation/{batch}/report.json", report)
    manifest = write_manifest(client, bucket, batch)
    return {"batch": batch, "clips": outcomes, **manifest}
