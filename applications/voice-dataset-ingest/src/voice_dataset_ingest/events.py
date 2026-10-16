"""Handle one Event Notifications delivery.

Whoever operates the application delivers the webhook: this module receives the raw body and the
`X-Bz-Event-Notification-Signature` value. The signature is verified on the raw body before
anything is parsed and before any call to B2. Delivery is at least once, so each `eventId` is
claimed once in a persistent SQLite store under the state directory; a repeated event is
acknowledged without processing it again. If processing fails, the claim is released so that the
retry can run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import processing, storage
from .errors import Rejected
from .shared import notifications

MAX_BODY_BYTES = 1024 * 1024  # 1 MiB
CREATED = "b2:ObjectCreated:"


def read_body(path: Path) -> bytes:
    """Read the body file, refusing one larger than 1 MiB without reading it."""
    try:
        size = path.stat().st_size
    except OSError as error:
        raise Rejected(f"cannot read the event body: {error}") from error
    if size > MAX_BODY_BYTES:
        raise Rejected(f"event body is larger than {MAX_BODY_BYTES} bytes")
    return path.read_bytes()


def parse_events(secret: str, body: bytes, signature: str | None, bucket: str) -> list[dict[str, Any]]:
    """Verify the signature, then parse and shape-check the events. Raises Rejected, touching no B2 call."""
    if len(body) > MAX_BODY_BYTES:
        raise Rejected(f"event body is larger than {MAX_BODY_BYTES} bytes")
    if not notifications.verify_signature(secret, body, signature):
        raise Rejected("invalid event signature")
    try:
        events = json.loads(body)["events"]
    except (ValueError, KeyError, TypeError) as error:
        raise Rejected("body is not an event notification") from error
    if not isinstance(events, list) or not events:
        raise Rejected("body carries no events")
    for event in events:
        if not isinstance(event, dict):
            raise Rejected("an event is not an object")
        for field in ("eventId", "eventType", "objectName"):
            if not isinstance(event.get(field), str) or not event[field]:
                raise Rejected(f"an event has no usable {field}")
        if event.get("bucketName") not in (None, bucket):
            raise Rejected("an event belongs to another bucket")
        if event["objectName"].startswith(storage.CONTRIBUTIONS) and event["eventType"].startswith(CREATED) \
                and event["objectName"].endswith(".wav") and not processing.CLIP_KEY.match(event["objectName"]):
            raise Rejected("an event names a clip outside the contributions layout")
    return events


def _release(store: notifications.EventStore, event_ids: list[str]) -> None:
    """Give the claims of a failed delivery back, so B2's retry processes the events again."""
    for event_id in event_ids:
        store.release(event_id)


def handle_events(client: Any, bucket: str, store: notifications.EventStore,
                  events: list[dict[str, Any]]) -> dict[str, Any]:
    """Process each new clip event once. Returns counts of processed, duplicate and ignored events."""
    processed = duplicates = ignored = 0
    results: list[dict[str, Any]] = []
    batches: list[str] = []
    claimed: list[str] = []
    for event in events:
        match = processing.CLIP_KEY.match(event["objectName"]) if event["eventType"].startswith(CREATED) else None
        if not match:
            ignored += 1
            continue
        if not store.claim(event["eventId"]):
            duplicates += 1
            continue
        batch, clip_id = match.group(1), match.group(2)
        claimed.append(event["eventId"])
        try:
            results.append(processing.process_clip(client, bucket, batch, clip_id))
        except Exception:
            _release(store, claimed)
            raise
        processed += 1
        if batch not in batches:
            batches.append(batch)
    try:
        for batch in batches:  # manifest last, once per batch touched by this delivery
            processing.write_manifest(client, bucket, batch)
    except Exception:
        _release(store, claimed)
        raise
    return {"processed": processed, "duplicates": duplicates, "ignored": ignored, "clips": results}
