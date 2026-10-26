"""Command line: synth, register-session, egress-request, handle-webhook, serve and evaluate.

Exit codes: 0 processed (or ignored on purpose), 2 rejected (token, webhook body or input, always
before any call to B2), 3 `evaluate` completed with the status `failed` (the result is printed and
written as usual), 1 any other error (B2 or configuration).

The env file is read here, at the start of `main`, from the current directory only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from . import __version__, config, egress, evaluation, server, sessions, storage, synth, webhook
from .errors import EXIT_ERROR, EXIT_EVALUATION_FAILED, EXIT_OK, ArchiveError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-session-archive", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    def session_arguments(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--room", required=True, help="LiveKit room name")
        sub.add_argument("--session-id", required=True)

    sub = commands.add_parser("synth", help="write a placeholder recording, a consent record and an unsigned egress_ended body")
    session_arguments(sub)
    sub.add_argument("--out", type=Path, required=True, help="directory to write into")
    sub.add_argument("--seconds", type=float, default=4.0)
    sub.add_argument("--seed", type=int, default=7)
    sub.add_argument("--consent-id", default=None,
                     help="id of the demo consent record (default consent-<session_id>-caller)")
    sub.add_argument("--put", action="store_true",
                     help="also upload the recording into the session folder, as Egress would, and the consent record")

    sub = commands.add_parser("register-session", help="record participants, consent ids and retention before the call")
    session_arguments(sub)
    sub.add_argument("--participant", action="append", default=[], help="<participant_id>:<caller|agent|observer>")
    sub.add_argument("--consent-id", action="append", default=[])
    sub.add_argument("--retention-class", default="standard", choices=sessions.RETENTION_CLASSES)

    sub = commands.add_parser("egress-request", help="print the LiveKit Egress request, with placeholders for the key")
    session_arguments(sub)

    sub = commands.add_parser("handle-webhook", help="verify and handle one LiveKit webhook body")
    sub.add_argument("--body", type=Path, required=True, help="file holding the raw request body")
    sub.add_argument("--authorization", default="", help="value of the Authorization header")
    sub.add_argument("--state-dir", type=Path, default=Path(config.DEFAULT_STATE_DIR))

    sub = commands.add_parser("serve", help="receive LiveKit webhooks over HTTP")
    sub.add_argument("--host", default=server.DEFAULT_HOST, help="address to bind (default 127.0.0.1)")
    sub.add_argument("--port", type=int, default=server.DEFAULT_PORT)
    sub.add_argument("--state-dir", type=Path, default=Path(config.DEFAULT_STATE_DIR))

    sub = commands.add_parser("evaluate", help="run the session evaluation and write the manifest again, last; "
                                               "exit code 3 when the status is failed")
    session_arguments(sub)
    return parser


def make_receiver(settings: config.Settings, state_dir: Path, endpoint_url: str | None) -> webhook.Receiver:
    api_key, api_secret = settings.require_livekit()
    return webhook.Receiver(api_key, api_secret, settings.bucket, webhook.open_store(state_dir),
                            lambda: storage.make_client(settings, endpoint_url))


def _run(args: argparse.Namespace, endpoint_url: str | None) -> Any:
    if args.command == "egress-request":
        return egress.build_request(config.Target.from_environ(), args.room, args.session_id)

    if args.command == "handle-webhook":
        body = webhook.read_body(args.body)
        settings = config.Settings.from_environ()
        return make_receiver(settings, args.state_dir, endpoint_url).handle(body, args.authorization)

    if args.command == "serve":
        settings = config.Settings.from_environ()
        httpd = server.make_server(make_receiver(settings, args.state_dir, endpoint_url), args.host, args.port)
        print(f"listening on http://{httpd.server_address[0]}:{httpd.server_address[1]}", file=sys.stderr)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
        return {"status": "stopped"}

    if args.command == "synth":
        storage.check_id(args.room, "room", storage.ROOM_PATTERN)
        storage.check_id(args.session_id, "session id")
        consent_id = storage.check_id(args.consent_id or synth.demo_consent_id(args.session_id), "consent id")
        plan = synth.build_demo(args.out, args.room, args.session_id, args.seconds, args.seed, consent_id=consent_id)
        if args.put:
            settings = config.Settings.from_environ()
            client = storage.make_client(settings, endpoint_url)
            storage.put_bytes(client, settings.bucket, plan["recording_key"],
                              Path(plan["recording"]).read_bytes(), "audio/wav")
            storage.put_json(client, settings.bucket, storage.consent_key(plan["consent_id"]),
                             json.loads(Path(plan["consent"]).read_text()))
        return plan

    if args.command == "register-session":
        registration = sessions.build_registration(args.room, args.session_id, args.participant,
                                                   args.consent_id, args.retention_class)
        settings = config.Settings.from_environ()
        return sessions.register_session(storage.make_client(settings, endpoint_url), settings.bucket, registration)

    settings = config.Settings.from_environ()
    return evaluation.evaluate(storage.make_client(settings, endpoint_url), settings.bucket, args.room, args.session_id)


def main(argv: list[str] | None = None, *, endpoint_url: str | None = None) -> int:
    """Run one command and return its exit code. `endpoint_url` exists only for the tests."""
    args = build_parser().parse_args(argv)
    try:
        config.load_env_file()
        result = _run(args, endpoint_url)
    except ArchiveError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except (ClientError, BotoCoreError, OSError) as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    if args.command == "evaluate" and result.get("status") == "failed":
        return EXIT_EVALUATION_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
