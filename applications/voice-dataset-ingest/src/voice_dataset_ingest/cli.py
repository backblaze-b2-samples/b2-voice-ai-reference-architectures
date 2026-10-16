"""Command line: synth, presign-upload, upload, handle-event, process and release.

Exit codes: 0 processed (or ignored on purpose), 2 rejected (signature, consent, event or input,
always before anything is written to B2), 1 any other error (B2 or configuration).

The env file is read here, at the start of `main`, from the current directory only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from . import __version__, config, events, processing, release, storage, synth, uploads
from .errors import EXIT_ERROR, EXIT_OK, IngestError
from .shared import notifications


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-dataset-ingest", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("synth", help="write deterministic synthetic clips and consent records")
    sub.add_argument("--out", type=Path, required=True, help="directory to write into")
    sub.add_argument("--seed", type=int, default=7)
    sub.add_argument("--put-consents", action="store_true",
                     help="also write the synthetic consent records to protected/consents/ in the bucket")

    def clip_arguments(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--batch", required=True)
        sub.add_argument("--speaker", required=True)
        sub.add_argument("--consent-id", required=True)
        sub.add_argument("--text", required=True, help="reference transcript")
        sub.add_argument("--lang", default="en")
        sub.add_argument("--split", default="train", choices=storage.SPLITS)

    sub = commands.add_parser("presign-upload", help="sign a PUT URL for one clip (needs an active consent)")
    clip_arguments(sub)
    sub.add_argument("--clip-id", required=True)
    sub.add_argument("--expires", type=int, default=uploads.DEFAULT_EXPIRES, help="seconds")

    sub = commands.add_parser("upload", help="upload one clip with multipart upload, resuming (needs an active consent)")
    clip_arguments(sub)
    sub.add_argument("--clip-id", help="defaults to the file name without its extension")
    sub.add_argument("--file", type=Path, required=True)
    sub.add_argument("--part-size", type=int, default=uploads.multipart.MIN_PART_SIZE)

    sub = commands.add_parser("handle-event", help="verify and process one Event Notifications delivery")
    sub.add_argument("--body", type=Path, required=True, help="file holding the raw request body")
    sub.add_argument("--signature", default="", help="value of the X-Bz-Event-Notification-Signature header")
    sub.add_argument("--state-dir", type=Path, default=Path(config.DEFAULT_STATE_DIR))

    sub = commands.add_parser("process", help="validate and normalize a batch, then write its manifest last")
    sub.add_argument("--batch", required=True)

    sub = commands.add_parser("release", help="publish a version with per-object Object Lock retention")
    sub.add_argument("--batch", required=True)
    sub.add_argument("--dataset", required=True)
    sub.add_argument("--dataset-version", dest="version", required=True, help="version name, for example v1")
    sub.add_argument("--retain-days", type=int, required=True)
    sub.add_argument("--allow-long-retention", action="store_true")
    return parser


def _run(args: argparse.Namespace, endpoint_url: str | None) -> Any:
    if args.command == "handle-event":
        settings = config.Settings.from_environ()
        secret = settings.require_event_signing_secret()
        body = events.read_body(args.body)
        parsed = events.parse_events(secret, body, args.signature, settings.bucket)  # rejects before any B2 call
        args.state_dir.mkdir(parents=True, exist_ok=True)
        store = notifications.EventStore(str(args.state_dir / "events.sqlite3"))
        return events.handle_events(storage.make_client(settings, endpoint_url), settings.bucket, store, parsed)

    if args.command == "synth":
        plan = synth.build_dataset(args.out, args.seed)
        if args.put_consents:
            settings = config.Settings.from_environ()
            client = storage.make_client(settings, endpoint_url)
            for item in plan["consents"]:
                document = json.loads((args.out / item["file"]).read_text())
                storage.put_json(client, settings.bucket, storage.consent_key(item["consent_id"]), document)
        return plan

    settings = config.Settings.from_environ()
    client = storage.make_client(settings, endpoint_url)
    if args.command in {"presign-upload", "upload"}:
        clip_id = args.clip_id or (args.file.stem if args.command == "upload" else None)
        sidecar = uploads.build_sidecar(args.batch, clip_id or "", args.speaker, args.consent_id, args.text,
                                        args.lang, args.split)
        if args.command == "presign-upload":
            return uploads.presign_upload(client, settings.bucket, sidecar, args.expires)
        return uploads.upload_clip(client, settings.bucket, sidecar, args.file, args.part_size)
    if args.command == "process":
        storage.check_id(args.batch, "batch")
        return processing.process_batch(client, settings.bucket, args.batch)
    return release.release_version(client, settings.bucket, args.batch, args.dataset, args.version,
                                   args.retain_days, args.allow_long_retention)


def main(argv: list[str] | None = None, *, endpoint_url: str | None = None) -> int:
    """Run one command and return its exit code. `endpoint_url` exists only for the tests."""
    args = build_parser().parse_args(argv)
    try:
        config.load_env_file()
        result = _run(args, endpoint_url)
    except IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except (ClientError, BotoCoreError, OSError) as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
