"""Error types that decide the exit code of the command line.

Exit code 0: processed, or ignored on purpose.
Exit code 2: rejected. A token, a webhook body or an input did not pass its check, and no call to B2
was made.
Exit code 1: any other error (B2 or configuration).
"""
from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REJECTED = 2


class ArchiveError(Exception):
    """Base class for errors this application raises on purpose."""

    exit_code = EXIT_ERROR


class ConfigError(ArchiveError):
    """A setting is missing or malformed, or the session is not set up for the command."""


class Conflict(ArchiveError):
    """The requested change would overwrite something that must stay as it is."""


class Rejected(ArchiveError):
    """An input, token or event was rejected before any call to B2."""

    exit_code = EXIT_REJECTED


class TokenRejected(Rejected):
    """The webhook's Authorization token is missing or does not verify."""
