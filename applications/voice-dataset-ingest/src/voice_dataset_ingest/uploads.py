"""Accept audio into the incoming zone, only with an active consent that permits training.

Both entry points check the consent record before anything is written. They then write a small
sidecar next to the clip (speaker, consent id, transcript, language, split) and either sign a
presigned PUT URL for the clip or upload it with multipart upload (retry and resume).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import consent, storage
from .errors import Rejected
from .shared import multipart

CONTENT_TYPE = "audio/wav"
DEFAULT_EXPIRES = 900
MAX_EXPIRES = 3600


def build_sidecar(batch: str, clip_id: str, speaker: str, consent_id: str, text: str,
                  lang: str = "en", split: str = "train") -> dict[str, Any]:
    """Validate the clip description and return the sidecar document."""
    storage.check_id(batch, "batch")
    storage.check_id(clip_id, "clip id")
    storage.check_id(speaker, "speaker", storage.SPEAKER_PATTERN)
    storage.check_id(consent_id, "consent id")
    storage.check_id(lang, "lang", storage.LANG_PATTERN)
    if not isinstance(text, str) or not text.strip():
        raise Rejected("the transcript text must not be empty")
    if split not in storage.SPLITS:
        raise Rejected(f"split must be one of {', '.join(storage.SPLITS)}")
    return {"batch": batch, "clip_id": clip_id, "speaker": speaker, "consent_id": consent_id,
            "text": text.strip(), "lang": lang, "split": split}


def _register(client: Any, bucket: str, sidecar: dict[str, Any]) -> str:
    """Check consent, then write the sidecar. Returns the key of the clip."""
    consent.require_consent(client, bucket, sidecar["consent_id"], sidecar["speaker"])
    storage.put_json(client, bucket, storage.sidecar_key(sidecar["batch"], sidecar["clip_id"]), sidecar)
    return storage.clip_key(sidecar["batch"], sidecar["clip_id"])


def presign_upload(client: Any, bucket: str, sidecar: dict[str, Any], expires: int = DEFAULT_EXPIRES) -> dict[str, Any]:
    """Sign a PUT URL for one clip. Refused unless an active consent permits training."""
    if not 1 <= expires <= MAX_EXPIRES:
        raise Rejected(f"expires must be between 1 and {MAX_EXPIRES} seconds")
    key = _register(client, bucket, sidecar)
    url = storage.presign_put(client, bucket, key, CONTENT_TYPE, expires)
    return {"key": key, "url": url, "method": "PUT", "headers": {"Content-Type": CONTENT_TYPE}, "expires_in": expires}


def upload_clip(client: Any, bucket: str, sidecar: dict[str, Any], path: Path,
                part_size: int = multipart.MIN_PART_SIZE, **retry: Any) -> dict[str, Any]:
    """Upload a clip with multipart upload (resuming an unfinished upload). Refused without consent."""
    path = Path(path)
    if not path.is_file():
        raise Rejected(f"{path} is not a file")
    if part_size < multipart.MIN_PART_SIZE:
        raise Rejected(f"part size must be at least {multipart.MIN_PART_SIZE} bytes")
    key = _register(client, bucket, sidecar)
    response = multipart.upload_file(client, bucket, key, path, part_size=part_size, **retry)
    return {"key": key, "etag": response.get("ETag"), "bytes": path.stat().st_size}
