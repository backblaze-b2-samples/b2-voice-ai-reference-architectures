"""Session registration and the session manifest.

`register-session` runs before the call. It writes
`incoming/sessions/<room>/<session_id>/session.json` with the participants, the consent ids and the
retention class, which LiveKit does not know about. When the `egress_ended` webhook arrives, the
recording is confirmed in B2 and `session-manifest.json` is written next to it, last, validated
against `schemas/session-manifest.schema.json`, with the evaluation `pending`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema
from botocore.exceptions import ClientError

from . import __version__, storage
from .errors import ArchiveError, ConfigError, Conflict, Rejected

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
ROLES = ("caller", "agent", "observer")
RETENTION_CLASSES = ("short", "standard", "extended", "legal-hold")
EVALUATOR_NAME = "voice-session-archive"
ENCODINGS = {".ogg": "audio/ogg", ".wav": "audio/wav", ".mp4": "audio/mp4", ".m4a": "audio/mp4",
             ".webm": "audio/webm", ".mp3": "audio/mpeg"}
DURATION_SLACK_SECONDS = 1.0


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text())


def schema_problems(document: Any, name: str) -> list[str]:
    validator = jsonschema.Draft202012Validator(load_schema(name), format_checker=jsonschema.FormatChecker())
    return sorted(f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
                  for error in validator.iter_errors(document))


def utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    """Parse an RFC 3339 date-time (a trailing Z is accepted on every supported Python)."""
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_participant(value: str) -> dict[str, str]:
    """`<participant_id>:<role>`, for example `caller_1:caller`."""
    participant_id, _, role = value.partition(":")
    storage.check_id(participant_id, "participant id", storage.PARTICIPANT_PATTERN)
    if role not in ROLES:
        raise Rejected(f"participant role must be one of {', '.join(ROLES)}, got {role!r}")
    return {"participant_id": participant_id, "role": role}


def build_registration(room: str, session_id: str, participants: list[str], consent_ids: list[str],
                       retention_class: str, now: datetime | None = None) -> dict[str, Any]:
    """Check the inputs and return the registration document. Raises Rejected; no call to B2."""
    storage.check_id(room, "room", storage.ROOM_PATTERN)
    storage.check_id(session_id, "session id")
    if not participants:
        raise Rejected("register at least one participant with --participant <id>:<role>")
    parsed = [parse_participant(item) for item in participants]
    if len({item["participant_id"] for item in parsed}) != len(parsed):
        raise Rejected("participant ids must be unique")
    for consent_id in consent_ids:
        storage.check_id(consent_id, "consent id")
    if len(set(consent_ids)) != len(consent_ids):
        raise Rejected("consent ids must be unique")
    if retention_class not in RETENTION_CLASSES:
        raise Rejected(f"retention class must be one of {', '.join(RETENTION_CLASSES)}")
    return {
        "room": room,
        "session_id": session_id,
        "participants": parsed,
        "consent_ids": list(consent_ids),
        "retention": {"class": retention_class},
        "registered_at": utc_text(now or now_utc()),
    }


def register_session(client: Any, bucket: str, registration: dict[str, Any]) -> dict[str, Any]:
    """Write the registration, refusing to replace an existing one or a written manifest."""
    room, session_id = registration["room"], registration["session_id"]
    key = storage.registration_key(room, session_id)
    if storage.head(client, bucket, key) is not None:
        raise Conflict(f"session {session_id} is already registered at {key}")
    if storage.head(client, bucket, storage.manifest_key(room, session_id)) is not None:
        raise Conflict(f"session {session_id} already has a manifest")
    storage.put_json(client, bucket, key, registration)
    return {"registration": key, "session_prefix": storage.session_prefix(room, session_id)}


def read_registration(client: Any, bucket: str, room: str, session_id: str) -> dict[str, Any]:
    key = storage.registration_key(room, session_id)
    try:
        document = storage.get_json(client, bucket, key)
    except ClientError as error:
        if storage.is_missing(error):
            raise ConfigError(f"session {session_id} in room {room} is not registered; "
                              "run register-session before the call") from error
        raise
    except ValueError as error:
        raise ArchiveError(f"{key} is not valid JSON") from error
    if not isinstance(document, dict) or document.get("session_id") != session_id:
        raise ArchiveError(f"{key} does not describe session {session_id}")
    return document


def pending_evaluation() -> dict[str, Any]:
    return {"status": "pending", "evaluator": {"name": EVALUATOR_NAME, "version": __version__}, "eligible_for": []}


def encoding_for(key: str) -> str:
    return ENCODINGS.get(Path(key).suffix.lower(), "application/octet-stream")


def check_manifest(manifest: dict[str, Any]) -> None:
    """Schema plus the rules a schema cannot express. Raises ArchiveError; nothing is written."""
    problems = schema_problems(manifest, "session-manifest.schema.json")
    elapsed = (parse_time(manifest["ended_at"]) - parse_time(manifest["started_at"])).total_seconds()
    if abs(elapsed - manifest["duration_seconds"]) > DURATION_SLACK_SECONDS:
        problems.append("duration_seconds differs from ended_at minus started_at")
    if problems:
        raise ArchiveError("session manifest does not match its schema: " + "; ".join(problems[:3]))


def build_manifest(registration: dict[str, Any], started: datetime, ended: datetime,
                   recordings: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = {
        "schema_version": "1.0.0",
        "session_id": registration["session_id"],
        "started_at": utc_text(started),
        "ended_at": utc_text(ended),
        "duration_seconds": round((ended - started).total_seconds(), 3),
        "source": {"platform": "livekit", "capture": "native-s3-output"},
        "participants": registration["participants"],
        "recordings": recordings,
        "consent_ids": registration["consent_ids"],
        "retention": registration["retention"],
        "evaluation": pending_evaluation(),
    }
    check_manifest(manifest)
    return manifest
