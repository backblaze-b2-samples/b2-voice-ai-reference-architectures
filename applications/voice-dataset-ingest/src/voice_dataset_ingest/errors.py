"""Error types that decide the exit code of the command line.

Exit code 0: processed, or ignored on purpose.
Exit code 2: rejected. A signature, a consent record, an event or an input did not pass its check,
and nothing was written to B2.
Exit code 1: any other error (B2 or configuration).
"""
from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REJECTED = 2


class IngestError(Exception):
    """Base class for errors this application raises on purpose."""

    exit_code = EXIT_ERROR


class ConfigError(IngestError):
    """A setting is missing or malformed, or the bucket is not set up for the command."""


class Rejected(IngestError):
    """An input, signature or event was rejected before any write to B2."""

    exit_code = EXIT_REJECTED


class ConsentError(Rejected):
    """No valid consent covers the clip. `code` says why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Conflict(IngestError):
    """The requested change would overwrite something that must stay as it is."""
