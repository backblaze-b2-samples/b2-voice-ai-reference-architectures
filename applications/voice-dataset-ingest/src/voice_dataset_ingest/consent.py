"""Consent rules.

A consent record covers a clip only when all of these hold:

- it validates against `consent-record.schema.json`;
- `status` is `active`;
- `expires_at` is absent or in the future;
- `permitted_uses` includes `training`;
- `subject.speaker_id` is the speaker of the clip.

Records live at `protected/consents/<consent_id>.json`. `release` checks them again for each clip,
so a revoked or expired consent prevents publication and not only upload.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema
from botocore.exceptions import ClientError

from . import storage
from .errors import ConsentError

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text())


def schema_validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(load_schema(name))


def parse_time(value: str) -> datetime:
    """Parse an RFC 3339 date-time (a trailing Z is accepted on every supported Python)."""
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def check_consent(record: Any, speaker_id: str, now: datetime | None = None) -> None:
    """Raise ConsentError unless the record permits training on a clip of this speaker."""
    current = now or now_utc()
    problems = sorted(schema_validator("consent-record.schema.json").iter_errors(record), key=str)
    if problems:
        raise ConsentError("invalid_record", f"consent record does not match its schema: {problems[0].message}")
    if record["status"] != "active":
        raise ConsentError(record["status"], f"consent {record['consent_id']} is {record['status']}")
    expires = record.get("expires_at")
    try:
        if expires and parse_time(expires) <= current:
            raise ConsentError("expired", f"consent {record['consent_id']} expired at {expires}")
    except ValueError as error:
        raise ConsentError("invalid_record", f"expires_at is not a date-time: {expires!r}") from error
    if "training" not in record["permitted_uses"]:
        raise ConsentError("no_training_use", f"consent {record['consent_id']} does not permit training")
    if record["subject"]["speaker_id"] != speaker_id:
        raise ConsentError("speaker_mismatch", f"consent {record['consent_id']} covers a different speaker")


def require_consent(client: Any, bucket: str, consent_id: str, speaker_id: str,
                    now: datetime | None = None) -> dict[str, Any]:
    """Read the consent record from B2 and return it if it covers the speaker for training."""
    try:
        record = storage.get_json(client, bucket, storage.consent_key(consent_id))
    except ClientError as error:
        if storage.is_missing(error):
            raise ConsentError("missing", f"no consent record {consent_id}") from error
        raise
    except ValueError as error:
        raise ConsentError("invalid_record", f"consent record {consent_id} is not valid JSON") from error
    if not isinstance(record, dict) or record.get("consent_id") != consent_id:
        raise ConsentError("invalid_record", f"consent record {consent_id} does not carry that consent_id")
    check_consent(record, speaker_id, now)
    return record
