"""Verify B2 Event Notifications and process each event once.

B2 signs the raw request body with HMAC SHA-256 and sends the result in the
`X-Bz-Event-Notification-Signature` header as `v1=<hex digest>`. Delivery is at
least once, so the same `eventId` can arrive more than once; `EventProcessor`
claims each id before running the handler, so a repeated event is acknowledged
without running the handler again. If the handler raises, the claim is released
so that B2's retry can process the event.

The signing secret comes from the environment (B2_EVENT_SIGNING_SECRET); it is
never stored with the rule or in source control.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from typing import Any, Callable

SIGNATURE_HEADER = "X-Bz-Event-Notification-Signature"
SIGNATURE_VERSION = "v1"


def sign(secret: str, raw_body: bytes) -> str:
    """Header value for a body: `v1=` followed by the hex HMAC SHA-256."""
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_VERSION}={digest}"


def verify_signature(secret: str, raw_body: bytes, header_value: str | None) -> bool:
    """Constant-time check of the signature header against the raw body."""
    if not header_value:
        return False
    return hmac.compare_digest(sign(secret, raw_body), header_value.strip())


class EventStore:
    """Durable set of claimed event ids (SQLite; use a shared database when several workers run)."""

    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY)")
        self._db.commit()
        self._lock = threading.Lock()

    def claim(self, event_id: str) -> bool:
        """True for the first caller of an id, False for every later one."""
        with self._lock:
            cursor = self._db.execute("INSERT OR IGNORE INTO events (event_id) VALUES (?)", (event_id,))
            self._db.commit()
            return cursor.rowcount == 1

    def release(self, event_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
            self._db.commit()


class EventProcessor:
    def __init__(self, secret: str, store: EventStore, handler: Callable[[dict[str, Any]], None]):
        self.secret = secret
        self.store = store
        self.handler = handler

    def handle_request(self, raw_body: bytes, signature_header: str | None) -> tuple[int, dict[str, Any]]:
        """Return the HTTP status and a summary for one webhook request.

        Answer within three seconds: the handler should only enqueue work.
        """
        if not verify_signature(self.secret, raw_body, signature_header):
            return 401, {"error": "invalid signature"}
        try:
            events = json.loads(raw_body)["events"]
        except (ValueError, KeyError, TypeError):
            return 400, {"error": "body is not an event notification"}
        processed = duplicates = 0
        for event in events:
            event_id = event["eventId"]
            if not self.store.claim(event_id):
                duplicates += 1
                continue
            try:
                self.handler(event)
            except Exception:
                self.store.release(event_id)
                raise
            processed += 1
        return 200, {"processed": processed, "duplicates": duplicates}
