"""Command line: synth-source, submit, promote and deliver.

Exit codes: 0 done, 2 rejected (an input did not pass its check, before anything is written to B2),
1 any other error (B2, configuration, an existing version or attempt).

The env file is read here, at the start of `main`, from the current directory only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from . import __version__, config, contracts, delivery, jobs, storage, synth
from .errors import EXIT_ERROR, EXIT_OK, GenerationError, Rejected
from .worker import PlaceholderWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-generation-artifacts", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("synth-source", help="write a deterministic synthetic source recording")
    sub.add_argument("--out", type=Path, required=True, help="directory to write <source-id>.wav into")
    sub.add_argument("--source-id", default="demo-source")
    sub.add_argument("--seconds", type=float, default=4.0)
    sub.add_argument("--seed", type=int, default=7)
    sub.add_argument("--put", action="store_true", help="also upload it to incoming/source/ in the bucket")

    sub = commands.add_parser("submit", help="run the placeholder worker and store its outputs as candidates")
    sub.add_argument("--source-id", required=True)
    sub.add_argument("--target-language", dest="target_languages", action="append", default=[], metavar="LANG")
    sub.add_argument("--speaker", dest="speakers", action="append", default=[], metavar="ID")
    sub.add_argument("--source-language", default="en")
    sub.add_argument("--kind", default="dubbing", choices=jobs.JOB_KINDS)
    sub.add_argument("--job-id", help="generated when omitted")
    sub.add_argument("--attempt", default="a1")
    sub.add_argument("--seed", type=int, default=7, help="seed of the placeholder worker")
    sub.add_argument("--source-expires", type=int, default=jobs.DEFAULT_SOURCE_EXPIRES,
                     help="seconds the source URL given to the worker stays valid")
    sub.add_argument("--candidate-days", type=int, default=jobs.DEFAULT_CANDIDATE_DAYS,
                     help="days until lifecycle rules remove an unpromoted candidate; recorded in the manifest")
    sub.add_argument("--consent-id", dest="consent_ids", action="append", default=[], metavar="ID",
                     help="consent record id to record in the manifest; not checked by this tool")

    sub = commands.add_parser("promote", help="publish an approved attempt as a version, by server-side copy")
    sub.add_argument("--job-id", required=True)
    sub.add_argument("--attempt", required=True)
    sub.add_argument("--version", dest="version", required=True, help="version name, for example v1")
    sub.add_argument("--approve", action="store_true", help="confirm that review approved these candidates")

    sub = commands.add_parser("deliver", help="sign GET URLs for a published version and store the delivery manifest")
    sub.add_argument("--job-id", required=True)
    sub.add_argument("--version", dest="version", required=True)
    sub.add_argument("--expires", type=int, default=contracts.DEFAULT_EXPIRES_SECONDS,
                     help=f"seconds the URLs stay valid (default {contracts.DEFAULT_EXPIRES_SECONDS}, at most "
                          f"{contracts.MAX_EXPIRES_SECONDS})")
    sub.add_argument("--out", type=Path, help="also write the manifest to this local file, readable by its owner only")
    return parser


def _run(args: argparse.Namespace, endpoint_url: str | None) -> Any:
    if args.command == "synth-source" and not args.put:
        return _synth_source(args, None, None)
    settings = config.Settings.from_environ()
    client = storage.make_client(settings, endpoint_url)
    if args.command == "synth-source":
        return _synth_source(args, client, settings.bucket)
    if args.command == "submit":
        return jobs.submit_job(client, settings.bucket, PlaceholderWorker(args.seed), source_id=args.source_id,
                               target_languages=args.target_languages, speakers=args.speakers or ["spk_a", "spk_b"],
                               source_language=args.source_language, kind=args.kind, job_id=args.job_id,
                               attempt=args.attempt, source_expires=args.source_expires,
                               candidate_days=args.candidate_days, consent_ids=args.consent_ids)
    if args.command == "promote":
        return delivery.promote_attempt(client, settings.bucket, args.job_id, args.attempt, args.version, args.approve)
    return delivery.deliver_version(client, settings.bucket, args.job_id, args.version, args.expires, args.out)


def _synth_source(args: argparse.Namespace, client: Any, bucket: str | None) -> dict[str, Any]:
    storage.check_id(args.source_id, "source id")
    if not 0.5 <= args.seconds <= 120:
        raise Rejected("--seconds must be between 0.5 and 120")
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.source_id}.wav"
    data = synth.make_source(args.seconds, args.seed)
    path.write_bytes(data)
    result: dict[str, Any] = {"file": str(path), "seconds": args.seconds, "seed": args.seed}
    if client is not None and bucket is not None:
        key = storage.source_key(args.source_id)
        storage.put_bytes(client, bucket, key, data, "audio/wav")
        result["key"] = key
    return result


def main(argv: list[str] | None = None, *, endpoint_url: str | None = None) -> int:
    """Run one command and return its exit code. `endpoint_url` exists only for the tests."""
    args = build_parser().parse_args(argv)
    try:
        config.load_env_file()
        if args.command == "submit" and not args.target_languages:
            raise Rejected("give at least one --target-language")
        result = _run(args, endpoint_url)
    except GenerationError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except (ClientError, BotoCoreError, OSError) as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
