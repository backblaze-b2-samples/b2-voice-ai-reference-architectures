"""The session evaluation step.

`evaluate` reads a session manifest whose evaluation is `pending` (or a previous result), runs the
checks this tool can run on its own, writes the detailed result to
`work/evaluations/<room>/<session_id>/<time>.json` and then writes the manifest again with the
completed `evaluation` block, last.

Quality checks:

- `integrity`: every listed file exists with the size and SHA-256 the manifest records;
- `duration`: each recording's reported duration agrees with the session's within 2 seconds
  (a disagreement is a warning).

Safety checks:

- `consent-recorded`: the session names at least one consent id;
- `consent-active`: every consent record exists under `protected/consents/`, matches its schema,
  is `active` and has not expired.

The session `passed` only when both groups pass; then `eligible_for` lists the uses that every
consent permits (`human-review` needs `evaluation`, `analytics` needs `analytics`,
`model-improvement` needs `training`). A failed check gives `failed`, a warning gives `flagged`,
and both leave `eligible_for` empty. Speech detection, transcript checks and content policy belong
to the operator's own evaluator and are not run here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from . import __version__, sessions, storage
from .errors import ConfigError

DURATION_TOLERANCE_SECONDS = 2.0
USE_FOR_PURPOSE = {"human-review": "evaluation", "analytics": "analytics", "model-improvement": "training"}


def _group(checks: list[dict[str, str]]) -> dict[str, Any]:
    results = {check["result"] for check in checks}
    status = "fail" if "fail" in results else "warn" if "warn" in results else "pass"
    return {"status": status, "checks": checks}


def _check(name: str, result: str, detail: str | None = None) -> dict[str, str]:
    item = {"name": name, "result": result}
    if detail:
        item["detail"] = detail
    return item


def check_integrity(client: Any, bucket: str, prefix: str, manifest: dict[str, Any]) -> dict[str, str]:
    problems = []
    for recording in manifest["recordings"]:
        key = prefix + recording["path"]
        meta = storage.head(client, bucket, key)
        if meta is None:
            problems.append(f"{recording['path']} is missing")
        elif int(meta.get("ContentLength", -1)) != recording["size"]:
            problems.append(f"{recording['path']} has a different size")
        elif storage.sha256_of(client, bucket, key) != recording["sha256"]:
            problems.append(f"{recording['path']} has a different SHA-256")
    if problems:
        return _check("integrity", "fail", "; ".join(problems))
    return _check("integrity", "pass", f"{len(manifest['recordings'])} files match size and SHA-256")


def check_duration(manifest: dict[str, Any]) -> dict[str, str]:
    session = manifest["duration_seconds"]
    off = [item["path"] for item in manifest["recordings"]
           if "duration_seconds" in item and abs(item["duration_seconds"] - session) > DURATION_TOLERANCE_SECONDS]
    if off:
        return _check("duration", "warn", "duration differs from the session: " + ", ".join(off))
    return _check("duration", "pass")


def read_consent(client: Any, bucket: str, consent_id: str, now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """The consent record if it is usable now, otherwise None and the reason."""
    try:
        record = storage.get_json(client, bucket, storage.consent_key(consent_id))
    except ClientError as error:
        if storage.is_missing(error):
            return None, f"{consent_id} has no record"
        raise
    except ValueError:
        return None, f"{consent_id} is not valid JSON"
    if sessions.schema_problems(record, "consent-record.schema.json") or record.get("consent_id") != consent_id:
        return None, f"{consent_id} does not match the consent schema"
    if record["status"] != "active":
        return None, f"{consent_id} is {record['status']}"
    expires = record.get("expires_at")
    try:
        if expires and sessions.parse_time(expires) <= now:
            return None, f"{consent_id} expired"
    except ValueError:
        return None, f"{consent_id} has an invalid expires_at"
    return record, None


def evaluate(client: Any, bucket: str, room: str, session_id: str, now: datetime | None = None) -> dict[str, Any]:
    storage.check_id(room, "room", storage.ROOM_PATTERN)
    storage.check_id(session_id, "session id")
    current = now or sessions.now_utc()
    key = storage.manifest_key(room, session_id)
    try:
        manifest = storage.get_json(client, bucket, key)
    except ClientError as error:
        if storage.is_missing(error):
            raise ConfigError(f"{key} does not exist; the session has no manifest yet") from error
        raise
    sessions.check_manifest(manifest)

    quality = _group([check_integrity(client, bucket, storage.session_prefix(room, session_id), manifest),
                      check_duration(manifest)])
    consent_ids = manifest["consent_ids"]
    records, reasons = [], []
    for consent_id in consent_ids:
        record, reason = read_consent(client, bucket, consent_id, current)
        if record is None:
            reasons.append(reason)
        else:
            records.append(record)
    safety = _group([
        _check("consent-recorded", "pass" if consent_ids else "fail",
               None if consent_ids else "the session names no consent id"),
        _check("consent-active", "pass" if consent_ids and not reasons else "fail",
               "; ".join(reasons) if reasons else None if consent_ids else "no consent record to check"),
    ])

    if quality["status"] == "pass" and safety["status"] == "pass":
        status = "passed"
        eligible = [use for use, purpose in USE_FOR_PURPOSE.items()
                    if all(purpose in record["permitted_uses"] for record in records)]
    else:
        status = "failed" if "fail" in (quality["status"], safety["status"]) else "flagged"
        eligible = []
    evaluated_at = sessions.utc_text(current)
    evaluation = {
        "status": status,
        "evaluated_at": evaluated_at,
        "evaluator": {"name": sessions.EVALUATOR_NAME, "version": __version__},
        "quality": quality,
        "safety": safety,
        "eligible_for": eligible,
    }
    updated = dict(manifest, evaluation=evaluation)
    sessions.check_manifest(updated)
    stamp = evaluated_at.replace(":", "").replace("-", "").replace(".", "")
    result_key = f"{storage.EVALUATIONS}{room}/{session_id}/{stamp}.json"
    storage.put_json(client, bucket, result_key, {"session_id": session_id, "room": room,
                                                  "manifest": key, "evaluation": evaluation})
    storage.put_json(client, bucket, key, updated)  # last
    return {"status": status, "eligible_for": eligible, "result": result_key, "manifest": key}
