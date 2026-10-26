"""The JSON schemas of the manifests this application writes, and validation against them.

The schemas are unchanged copies of `schemas/generation-job-manifest.schema.json`,
`schemas/delivery-manifest.schema.json` and `schemas/consent-record.schema.json` at the repository
root, shipped as package data.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

from .errors import ConfigError

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
JOB_SCHEMA = "generation-job-manifest.schema.json"
DELIVERY_SCHEMA = "delivery-manifest.schema.json"
CONSENT_SCHEMA = "consent-record.schema.json"
MAX_EXPIRES_SECONDS = 604800  # the maximum in the delivery manifest schema and the longest B2 presigned URL
DEFAULT_EXPIRES_SECONDS = 3600
REVIEW_DEFAULT_EXPIRES_SECONDS = 300  # review URLs: long enough to listen, short enough to share with care
REVIEW_MAX_EXPIRES_SECONDS = 3600


@lru_cache(maxsize=None)
def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads((SCHEMA_DIR / name).read_text()))


def validate(name: str, document: Any, what: str) -> None:
    """Raise ConfigError unless `document` matches the named schema."""
    problems = sorted(_validator(name).iter_errors(document), key=lambda item: list(item.absolute_path))
    if problems:
        where = "/".join(str(part) for part in problems[0].absolute_path) or "(root)"
        raise ConfigError(f"{what} does not match its schema at {where}: {problems[0].message}")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def stamp(moment: datetime) -> str:
    """An RFC 3339 UTC date-time with a trailing Z."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_days(moment: datetime, days: int) -> datetime:
    return moment + timedelta(days=days)
