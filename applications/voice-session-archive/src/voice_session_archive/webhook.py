"""Handle one LiveKit webhook delivery.

Order of work, so that a forged or malformed request never reaches B2:

1. The body is at most 1 MiB.
2. The `Authorization` token is verified against the raw body (`webhook_token.verify`).
3. The body is parsed and shape-checked. Events other than `egress_ended`, and an `egress_ended`
   whose status is not `EGRESS_COMPLETE`, are acknowledged and ignored: no manifest is written.
4. Every file result must be under `incoming/sessions/<room>/<session_id>/`; a key outside
   `incoming/sessions/` or outside the room's prefix is rejected.

Only then is the event id claimed in the persistent `EventStore` (from `shared/notifications.py`),
so a repeated delivery is acknowledged without being processed again. The handler reads the
session's registration, confirms with `head_object` that each recording exists with the reported
size, computes its SHA-256 and writes the session manifest last. If any of that fails, the claim
is released so that LiveKit's retry can process the event.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import sessions, storage, webhook_token
from .errors import ArchiveError, ConfigError, Conflict, Rejected
from .shared import notifications

MAX_BODY_BYTES = 1024 * 1024  # 1 MiB
COMPLETE = ("EGRESS_COMPLETE", 3)
MAX_EVENT_ID_LENGTH = 200


class MissingRecording(ArchiveError):
    """The webhook reports a recording that is not in the bucket, or not with the reported size."""


@dataclass(frozen=True)
class Recording:
    key: str
    path: str
    size: int
    duration_seconds: float | None


@dataclass(frozen=True)
class Archive:
    event_id: str
    room: str
    session_id: str
    recordings: tuple[Recording, ...]
    started: datetime
    ended: datetime


def read_body(path: Path) -> bytes:
    """Read the body file, refusing one larger than 1 MiB without reading it."""
    try:
        size = path.stat().st_size
    except OSError as error:
        raise Rejected(f"cannot read the webhook body: {error}") from error
    if size > MAX_BODY_BYTES:
        raise Rejected(f"webhook body is larger than {MAX_BODY_BYTES} bytes")
    return path.read_bytes()


def verify_and_parse(body: bytes, authorization: str | None, api_key: str, api_secret: str,
                     now: float | None = None) -> dict[str, Any]:
    """Verify the token on the raw body, then parse it. Raises Rejected; no call to B2."""
    if len(body) > MAX_BODY_BYTES:
        raise Rejected(f"webhook body is larger than {MAX_BODY_BYTES} bytes")
    webhook_token.verify(authorization, body, api_key, api_secret, now)
    try:
        event = json.loads(body)
    except (UnicodeDecodeError, ValueError) as error:
        raise Rejected("webhook body is not JSON") from error
    if not isinstance(event, dict):
        raise Rejected("webhook body is not a JSON object")
    if not isinstance(event.get("event"), str) or not event["event"]:
        raise Rejected("webhook body has no event name")
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id or len(event_id) > MAX_EVENT_ID_LENGTH:
        raise Rejected("webhook body has no usable id")
    return event


def _integer(value: Any) -> int | None:
    """LiveKit's JSON may carry 64-bit integers as numbers or as decimal strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def to_datetime(value: Any) -> datetime | None:
    """A LiveKit timestamp as a UTC date-time.

    The unit is told apart by magnitude: nanoseconds, microseconds, milliseconds or seconds since
    the Unix epoch. Zero or a missing value gives None.
    """
    number = _integer(value)
    if not number:
        return None
    for limit, divisor in ((10**17, 10**9), (10**14, 10**6), (10**11, 10**3)):
        if number >= limit:
            return datetime.fromtimestamp(number / divisor, timezone.utc)
    return datetime.fromtimestamp(number, timezone.utc)


def _check_key(key: Any, room: str) -> str:
    if not isinstance(key, str) or not key:
        raise Rejected("a file result has no filename")
    if ".." in key.split("/") or key.startswith("/") or "//" in key or "\\" in key or len(key) > 1024:
        raise Rejected(f"recording key {key!r} is not a plain object key")
    if not key.startswith(storage.SESSIONS):
        raise Rejected(f"recording key {key!r} is outside {storage.SESSIONS}")
    if not key.startswith(storage.room_prefix(room)):
        raise Rejected(f"recording key {key!r} is outside the room's prefix {storage.room_prefix(room)}")
    return key


def plan(event: dict[str, Any]) -> Archive | str:
    """What to do with a verified event: an Archive, or the reason it is ignored."""
    if event["event"] != "egress_ended":
        return f"event {event['event']} is not egress_ended"
    info = event.get("egressInfo")
    if not isinstance(info, dict):
        raise Rejected("egress_ended has no egressInfo object")
    status = info.get("status")
    if status not in COMPLETE:
        return f"egress status {status!r} is not EGRESS_COMPLETE; no manifest is written"
    room = storage.check_id(info.get("roomName"), "egressInfo.roomName", storage.ROOM_PATTERN)
    results = info.get("fileResults")
    if not isinstance(results, list) or not results:
        raise Rejected("egress_ended has no fileResults")

    recordings: list[Recording] = []
    session_ids: set[str] = set()
    starts: list[datetime] = []
    ends: list[datetime] = []
    for result in results:
        if not isinstance(result, dict):
            raise Rejected("a file result is not an object")
        key = _check_key(result.get("filename"), room)
        size = _integer(result.get("size"))
        if size is None or size < 1:
            raise Rejected(f"file result {key!r} has no positive size")
        rest = key[len(storage.room_prefix(room)):]
        session_id, _, path = rest.partition("/")
        if not path:
            raise ConfigError(f"recording {key!r} is not inside a session folder "
                              f"{storage.room_prefix(room)}<session_id>/; build the Egress output with egress-request")
        storage.check_id(session_id, "session id in the recording key")
        if Path(path).name in (storage.REGISTRATION_NAME, storage.MANIFEST_NAME):
            raise Rejected(f"recording key {key!r} uses a name reserved for the session's own documents")
        session_ids.add(session_id)
        duration = _integer(result.get("duration"))
        recordings.append(Recording(key, path, size, round(duration / 10**9, 3) if duration else None))
        for value, bucket in ((result.get("startedAt"), starts), (result.get("endedAt"), ends)):
            moment = to_datetime(value)
            if moment:
                bucket.append(moment)
    if len(session_ids) != 1:
        raise Rejected("the file results belong to more than one session folder")

    started = to_datetime(info.get("startedAt")) or (min(starts) if starts else None)
    ended = to_datetime(info.get("endedAt")) or (max(ends) if ends else None)
    if started is None or ended is None or ended <= started:
        longest = max((item.duration_seconds or 0 for item in recordings), default=0)
        created = to_datetime(event.get("createdAt"))
        if created and longest > 0:
            ended = created
            started = datetime.fromtimestamp(created.timestamp() - longest, timezone.utc)
        else:
            raise Rejected("egress_ended carries no usable start and end time")
    return Archive(event["id"], room, session_ids.pop(), tuple(recordings), started, ended)


def archive(client: Any, bucket: str, store: notifications.EventStore, job: Archive) -> dict[str, Any]:
    """Confirm the recordings in B2 and write the session manifest last, once per event id."""
    if not store.claim(job.event_id):
        return {"status": "duplicate", "event_id": job.event_id}
    try:
        registration = sessions.read_registration(client, bucket, job.room, job.session_id)
        manifest_key = storage.manifest_key(job.room, job.session_id)
        if storage.head(client, bucket, manifest_key) is not None:
            raise Conflict(f"{manifest_key} already exists; a session manifest is written once")
        entries = []
        for item in job.recordings:
            meta = storage.head(client, bucket, item.key)
            if meta is None:
                raise MissingRecording(f"recording {item.key} is not in the bucket; no manifest was written")
            if int(meta.get("ContentLength", -1)) != item.size:
                raise MissingRecording(f"recording {item.key} has {meta.get('ContentLength')} bytes, "
                                       f"the webhook reports {item.size}; no manifest was written")
            entry: dict[str, Any] = {"kind": "mixed", "path": item.path, "size": item.size,
                                     "sha256": storage.sha256_of(client, bucket, item.key),
                                     "encoding_format": sessions.encoding_for(item.key)}
            if item.duration_seconds:
                entry["duration_seconds"] = item.duration_seconds
            entries.append(entry)
        manifest = sessions.build_manifest(registration, job.started, job.ended, entries)
        storage.put_json(client, bucket, manifest_key, manifest)  # last
    except Exception:
        store.release(job.event_id)
        raise
    return {"status": "archived", "event_id": job.event_id, "manifest": manifest_key,
            "recordings": [item.key for item in job.recordings]}


class Receiver:
    """Verifies and handles webhook bodies; shared by `handle-webhook` and `serve`.

    The B2 client is built only when an event passed every check, so a rejected request never
    leads to a call to B2.
    """

    def __init__(self, api_key: str, api_secret: str, bucket: str, store: notifications.EventStore,
                 client_factory: Callable[[], Any]):
        self._api_key = api_key
        self._api_secret = api_secret
        self._bucket = bucket
        self._store = store
        self._client_factory = client_factory
        self._client: Any = None

    def handle(self, body: bytes, authorization: str | None) -> dict[str, Any]:
        event = verify_and_parse(body, authorization, self._api_key, self._api_secret)
        job = plan(event)
        if isinstance(job, str):
            return {"status": "ignored", "event_id": event["id"], "reason": job}
        if self._client is None:
            self._client = self._client_factory()
        return archive(self._client, self._bucket, self._store, job)


def open_store(state_dir: Path) -> notifications.EventStore:
    state_dir.mkdir(parents=True, exist_ok=True)
    return notifications.EventStore(str(state_dir / "events.sqlite3"))
