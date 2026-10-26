"""Error types that decide the exit code of the command line.

Exit code 0: done.
Exit code 2: rejected. An input did not pass its check (an identifier, a source, a consent record, an
expiry, a missing approval), and nothing was written to B2 for that command.
Exit code 1: any other error (B2, configuration, an existing version).
"""
from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REJECTED = 2


class GenerationError(Exception):
    """Base class for errors this application raises on purpose."""

    exit_code = EXIT_ERROR


class ConfigError(GenerationError):
    """A setting is missing or malformed, or stored data does not match what a command needs."""


class Rejected(GenerationError):
    """An input was rejected before anything was written to B2."""

    exit_code = EXIT_REJECTED


class ConsentError(Rejected):
    """No valid consent covers the job. `code` says why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Conflict(GenerationError):
    """The requested change would overwrite something that must stay as it is."""
