"""Consent rules, checked by `submit` before the source is read or anything is written.

A consent record covers a speaker of a job only when all of these hold:

- it validates against `consent-record.schema.json` and carries the consent id it is stored under;
- `status` is `active`;
- `expires_at` is absent or in the future;
- `permitted_uses` includes `generation`;
- `subject.speaker_id` is one of the job's speakers.

Every consent id given to `submit` must pass these checks, and every speaker of the job must be
covered by at least one of them. Records live at `protected/consents/<consent_id>.json`. The rules
follow the consent check of `voice-dataset-ingest`, with `generation` as the required use.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from botocore.exceptions import ClientError

from . import contracts, storage
from .errors import ConfigError, ConsentError

REQUIRED_USE = "generation"


def parse_time(value: str) -> datetime:
    """Parse an RFC 3339 date-time (a trailing Z is accepted on every supported Python)."""
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check_consent(record: Any, speakers: Sequence[str], now: datetime) -> None:
    """Raise ConsentError unless the record permits generation for one of the job's speakers."""
    try:
        contracts.validate(contracts.CONSENT_SCHEMA, record, "the consent record")
    except ConfigError as error:
        raise ConsentError("invalid_record", str(error)) from error
    if record["status"] != "active":
        raise ConsentError(record["status"], f"consent {record['consent_id']} is {record['status']}")
    expires = record.get("expires_at")
    try:
        if expires and parse_time(expires) <= now:
            raise ConsentError("expired", f"consent {record['consent_id']} expired at {expires}")
    except ValueError as error:
        raise ConsentError("invalid_record", f"expires_at is not a date-time: {expires!r}") from error
    if REQUIRED_USE not in record["permitted_uses"]:
        raise ConsentError("no_generation_use", f"consent {record['consent_id']} does not permit {REQUIRED_USE}")
    if record["subject"]["speaker_id"] not in speakers:
        raise ConsentError("speaker_mismatch", f"consent {record['consent_id']} covers none of the job's speakers")


def read_consent(client: Any, bucket: str, consent_id: str) -> Any:
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
    return record


def require_consents(client: Any, bucket: str, consent_ids: Sequence[str], speakers: Sequence[str],
                     now: datetime | None = None) -> list[dict[str, Any]]:
    """Read every consent record and return them if together they cover every speaker for generation."""
    if not consent_ids:
        raise ConsentError("missing", "give a --consent-id for every speaker; consent is checked before generation")
    current = now or contracts.utc_now()
    records = []
    for consent_id in consent_ids:
        record = read_consent(client, bucket, consent_id)
        check_consent(record, speakers, current)
        records.append(record)
    covered = {record["subject"]["speaker_id"] for record in records}
    uncovered = [speaker for speaker in speakers if speaker not in covered]
    if uncovered:
        raise ConsentError("speaker_not_covered", f"no consent covers speaker {', '.join(uncovered)}")
    return records
