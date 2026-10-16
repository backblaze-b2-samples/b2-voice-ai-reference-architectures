"""Settings for the command line.

The settings are the repository's `B2_*` variables plus the LiveKit API key and secret that sign
the webhooks. The endpoint is derived from `B2_REGION`, so there is no endpoint setting to keep in
sync.

The env file is read only by the command-line entry point (`load_env_file`), only from the
current directory, and never on import. Variables already present in the environment win.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from .errors import ConfigError

REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]+-[0-9]{3}$")
BUCKET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{4,62}$")
ENV_FILE_NAME = ".env"
DEFAULT_STATE_DIR = ".voice-session-archive"


def endpoint_for_region(region: str) -> str:
    """The S3-compatible endpoint of a B2 region (for example us-west-004)."""
    if not REGION_PATTERN.match(region):
        raise ConfigError(f"B2_REGION must look like 'us-west-004', got {region!r}")
    return f"https://s3.{region}.backblazeb2.com"


def load_env_file(directory: Path | None = None) -> list[str]:
    """Copy the variables of `<directory>/.env` into the environment, without overriding any.

    Only the given directory (the current directory by default) is read: there is no search in
    parent directories, so another project's credentials are never picked up by accident.
    Returns the names that were set from the file.
    """
    path = (directory or Path.cwd()) / ENV_FILE_NAME
    if not path.is_file():
        return []
    added = []
    for name, value in dotenv_values(path).items():
        if value is not None and name not in environ:
            environ[name] = value
            added.append(name)
    return added


def _need(source: Mapping[str, str], name: str) -> str:
    value = source.get(name, "")
    if not value:
        raise ConfigError(f"Set {name} in the environment or in .env in the current directory")
    return value


@dataclass(frozen=True)
class Target:
    """Bucket and region only: what `egress-request` needs, with no credential at all."""

    bucket: str
    region: str

    @property
    def endpoint(self) -> str:
        return endpoint_for_region(self.region)

    @classmethod
    def from_mapping(cls, source: Mapping[str, str]) -> "Target":
        region = _need(source, "B2_REGION")
        endpoint_for_region(region)
        bucket = _need(source, "B2_BUCKET_NAME")
        if not BUCKET_PATTERN.match(bucket):
            raise ConfigError("B2_BUCKET_NAME is not a valid bucket name")
        return cls(bucket=bucket, region=region)

    @classmethod
    def from_environ(cls) -> "Target":
        return cls.from_mapping(environ)


@dataclass(frozen=True)
class Settings:
    key_id: str
    application_key: str
    bucket: str
    region: str
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None

    @property
    def endpoint(self) -> str:
        return endpoint_for_region(self.region)

    @classmethod
    def from_mapping(cls, source: Mapping[str, str]) -> "Settings":
        target = Target.from_mapping(source)
        return cls(
            key_id=_need(source, "B2_APPLICATION_KEY_ID"),
            application_key=_need(source, "B2_APPLICATION_KEY"),
            bucket=target.bucket,
            region=target.region,
            livekit_api_key=source.get("LIVEKIT_API_KEY") or None,
            livekit_api_secret=source.get("LIVEKIT_API_SECRET") or None,
        )

    @classmethod
    def from_environ(cls) -> "Settings":
        return cls.from_mapping(environ)

    def require_livekit(self) -> tuple[str, str]:
        """The LiveKit API key and secret that sign the webhooks."""
        if not self.livekit_api_key or not self.livekit_api_secret:
            raise ConfigError("Set LIVEKIT_API_KEY and LIVEKIT_API_SECRET to verify LiveKit webhooks")
        return self.livekit_api_key, self.livekit_api_secret
