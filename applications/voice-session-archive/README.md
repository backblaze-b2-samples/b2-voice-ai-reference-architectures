# voice-session-archive

A small Python command-line tool that implements the [voice agent session archive](../../architectures/voice-agent-session-archive/architecture.md) architecture on Backblaze B2 for LiveKit: LiveKit Egress writes the session recording straight to B2, and this tool receives LiveKit's `egress_ended` webhook, verifies its signature, processes each event id only once, confirms that the recording is in the bucket with the reported size and writes the session manifest last. A separate step evaluates the session and records which uses it may be selected for.

The tool does not start LiveKit. Its tests send signed sample `egress_ended` bodies to an emulated S3 server on the loopback interface, so they need no LiveKit server, no B2 account and no credentials. An opt-in live check against a real B2 test bucket, which is not part of CI, covers the storage operations only and involves no LiveKit server. A manual walkthrough of the commands, with a stand-in for Egress, was run against a real B2 test bucket with Object Lock on 2026-10-26; see [Known limitations](#known-limitations). To see it work against a real recording you need your own LiveKit (self-hosted or LiveKit Cloud) with Egress configured to write to B2; see [Run it against a real LiveKit recording](#run-it-against-a-real-livekit-recording).

## Quick start

```bash
cd applications/voice-session-archive
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -e .
python -m pytest -q
```

## Commands

| Command | What it does |
|---|---|
| `synth` | Writes deterministic demo data to a local folder: a placeholder recording (tones, not speech), a synthetic consent record and an unsigned `egress_ended` body. The consent id is `consent-<session_id>-caller` unless `--consent-id` sets another, so runs with different session ids that share a bucket keep separate consent records. With `--put` it also uploads the recording into the session folder, as Egress would, and the consent record to `protected/consents/`. |
| `register-session` | Before the call, writes `incoming/sessions/<room>/<session_id>/session.json` with the participants (`--participant <id>:<caller\|agent\|observer>`), the consent ids and the retention class. LiveKit does not know these, so the manifest takes them from here. |
| `egress-request` | Prints the LiveKit Egress request for the session: audio-only room recording, OGG, written to `incoming/sessions/<room>/<session_id>/`. The credentials are always placeholders. |
| `handle-webhook` | Verifies and handles one webhook: `--body <file>` holds the raw request body, `--authorization <token>` the `Authorization` header value. |
| `serve` | Receives webhooks over HTTP on `127.0.0.1:8080` by default (`--host`, `--port`). |
| `evaluate` | Runs the session evaluation and writes the manifest again, last, with the result. It prints the result and exits with code 3 when the status is `failed`, so a script or scheduler can act on it. |

`handle-webhook` and `serve` keep the processed event ids in a SQLite file under `--state-dir` (by default `.voice-session-archive/` in the current directory).

## B2's role

- **Destination of the recording.** LiveKit Egress uploads the recording through B2's S3-compatible endpoint with `force_path_style`; the tool never copies audio. B2 is not in the live audio path: LiveKit carries the call, and B2 receives the file after Egress writes it.
- **Storage of the session record.** The registration, the session manifest and the evaluation results live next to the recording, with the zones as prefixes of one bucket: `incoming/sessions/`, `work/evaluations/` and `protected/consents/`. Production deployments use one bucket per zone, as the architecture does, so that lifecycle, Object Lock, replication and encryption rules and application keys can be set per zone. This tool uses a single bucket (`B2_BUCKET_NAME`) to stay easy to run.
- **Confirmation before the manifest.** `head_object` confirms that the recording exists with the size the webhook reports, and the tool reads it once to compute its SHA-256, before the manifest is written.
- **Not B2's job.** Starting and recording the call (LiveKit), delivering and retrying webhooks (LiveKit), queuing work, speech or content checks, and the decision to keep a session stay outside B2.

## Setup

1. Create a private bucket. See the [examples README](../../examples/README.md) for the commands.
2. Create an application key for this tool, limited to that bucket (see [Key capabilities](#key-capabilities)).
3. Copy `.env.example` to `.env` in the directory you run the command from and fill it in. The command reads `.env` from the current directory only, never from a parent directory, and variables already present in the environment take precedence. The endpoint is derived from `B2_REGION`; there is no endpoint setting.

| Variable | Purpose |
|---|---|
| `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY` | The application key of this tool |
| `B2_BUCKET_NAME` | The bucket |
| `B2_REGION` | The bucket's region, for example `us-west-004` |
| `B2_PUBLIC_URL_BASE` | Optional and unused by this tool |
| `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | The LiveKit API key and secret that sign the webhooks; needed by `handle-webhook` and `serve` |

Install with `pip install -e .` (Python 3.10 or later). The runtime dependencies are `boto3`, `jsonschema` and `python-dotenv`; the webhook token is verified with the Python standard library.

## Walkthrough with synthetic data

This runs the whole workflow against your bucket without LiveKit: `synth --put` plays the part of Egress, and the token is signed locally with PyJWT, which `requirements-dev.txt` installs.

Before you start:

- Run the commands from an empty working folder. The tool writes the demo files to `./demo` and its state folder, `.voice-session-archive/`, in the current directory.
- Export `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in your shell. The token line reads them from the environment; `handle-webhook` also reads them from `.env`.

Each run uses its own session id, so several people can follow these steps against one bucket. `synth` derives the consent id from it (`consent-<session_id>-caller`) and prints it as `consent_id`.

```bash
SESSION_ID="session-$(date -u +%Y%m%d%H%M%S)"
voice-session-archive synth --out ./demo --room support-room --session-id "$SESSION_ID" --put
voice-session-archive register-session --room support-room --session-id "$SESSION_ID" \
  --participant caller_1:caller --participant agent_1:agent --consent-id "consent-$SESSION_ID-caller"
TOKEN=$(python -c 'import base64, hashlib, os, time, jwt
body = open("demo/egress-ended.json", "rb").read()
now = int(time.time())
print(jwt.encode({"iss": os.environ["LIVEKIT_API_KEY"], "nbf": now, "exp": now + 300,
                  "sha256": base64.b64encode(hashlib.sha256(body).digest()).decode()},
                 os.environ["LIVEKIT_API_SECRET"], algorithm="HS256"))')
voice-session-archive handle-webhook --body demo/egress-ended.json --authorization "$TOKEN"
voice-session-archive evaluate --room support-room --session-id "$SESSION_ID"
```

Running `handle-webhook` again with the same body prints `duplicate`: the event id was already processed. `evaluate` prints `passed` and exits 0; a session whose consent record is missing, inactive or expired prints `failed` and exits 3.

## Run it against a real LiveKit recording

You need a LiveKit server with Egress, either self-hosted or LiveKit Cloud, and a way to call its Egress API (a LiveKit server SDK or the LiveKit CLI).

1. **Create a separate key for Egress.** Egress needs to write the recording, so it gets its own application key with only `writeFiles`, limited to this bucket and to the `incoming/sessions/` prefix. Never give LiveKit the key this tool runs with. With the B2 command-line tool:

   ```bash
   b2 key create --bucket "$B2_BUCKET_NAME" --name-prefix incoming/sessions/ livekit-egress-sessions writeFiles
   ```

   Keep the key id and key it prints in the secret store of the server that calls the Egress API.
2. **Register the session** before the call: `voice-session-archive register-session --room <room> --session-id <session_id> --participant ... --consent-id ...`.
3. **Print the Egress request**: `voice-session-archive egress-request --room <room> --session-id <session_id> > egress-request.json`. It has placeholders, `<key-id-of-the-egress-key>` and `<application-key-of-the-egress-key>`, where the Egress key goes; fill them in server-side, right before the Egress API call, and never log the filled request. The request asks for an audio-only room recording with the agent and the other participants on separate channels (`DUAL_CHANNEL_AGENT`) and disables Egress's own JSON manifest. The [LiveKit Egress template](../../infrastructure/livekit-egress/README.md) explains each S3 field, and how the same `s3` block moves to the newer `StartEgress` request.
4. **Point LiveKit's webhooks at the receiver.** Run `voice-session-archive serve` and expose it through a TLS-terminating reverse proxy; LiveKit Cloud and remote LiveKit servers cannot reach `127.0.0.1`. In LiveKit Cloud, add the webhook URL in the project settings and choose the signing API key; for a self-hosted server, set the `webhook` section of its configuration (`api_key` and `urls`). Set `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` to that key.
5. **Start the Egress** with the request from step 3. When it ends, LiveKit sends `egress_ended`; the receiver writes `incoming/sessions/<room>/<session_id>/session-manifest.json`.
6. **Evaluate** with `voice-session-archive evaluate --room <room> --session-id <session_id>`. Exit code 3 means the evaluation completed with the status `failed`; the result is in the manifest and under `work/evaluations/`.

LiveKit retries webhooks but does not guarantee delivery. If no `egress_ended` arrives, the recording stays in the session folder without a manifest; query the egress through LiveKit's API and deliver its result again, or treat the session as incomplete.

## Webhook verification

LiveKit sends each webhook as a POST whose `Authorization` header holds a signed JWT, and the token carries a SHA-256 of the body. The receiver implements that scheme with the Python standard library and accepts a webhook only when all of these hold:

- the body is at most 1 MiB;
- the token's header has `alg` equal to `HS256`; `none` and every other algorithm are refused;
- the HMAC SHA-256 signature, computed with `LIVEKIT_API_SECRET`, matches; it is compared in constant time;
- `exp` is present and has not passed, with 30 seconds of clock leeway; `nbf`, when present, has been reached;
- `iss` equals `LIVEKIT_API_KEY`;
- the `sha256` claim equals the base64 SHA-256 of the raw body, compared in constant time;
- for `egress_ended`, every recording key in `egressInfo.fileResults` is under `incoming/sessions/<room>/`.

All of this happens before any call to B2. Events other than `egress_ended`, and an `egress_ended` whose status is not `EGRESS_COMPLETE`, are acknowledged and ignored: no manifest is written. Each event id is claimed once in the SQLite store; a repeated delivery is acknowledged without being processed again, and if processing fails the claim is released so that LiveKit's retry can process it.

The HTTP receiver answers 411 without `Content-Length`, 413 for a larger body (without reading it), 401 without `Authorization` or with a token that does not verify, 400 for a body that is not a usable event, 200 when the event was archived, ignored or already processed, and 500 for other errors, so that LiveKit retries. It handles one request at a time and never logs headers or tokens.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Done, or ignored on purpose (another event, a failed Egress, a repeated event id) |
| 2 | Rejected: the token, the webhook body, a recording key or an input did not pass its check. No call to B2 was made |
| 1 | Any other error: a B2 error, a missing setting, a session that is not registered, a recording that is not in the bucket or has another size, a manifest that already exists |
| 3 | `evaluate` completed and the status is `failed`. The result is printed, and the evaluation result and the new manifest version are written as for any other status |

## Session manifest and evaluation

The manifest follows [`session-manifest.schema.json`](../../schemas/session-manifest.schema.json) (an unchanged copy ships inside the package) and is checked against it before it is written. It records the session id, start, end and duration from the Egress result, `source` `livekit` with `native-s3-output`, the registered participants, consent ids and retention class, and each recording with its path, size, SHA-256 and encoding. The evaluation starts as `pending` with an empty `eligible_for`, so no selection job can pick the session yet.

`evaluate` runs the checks this tool can run on its own and writes the result to `work/evaluations/<room>/<session_id>/<time>.json`, then the manifest again, last:

| Group | Check | What it verifies |
|---|---|---|
| Quality | `integrity` | Every listed file exists with the recorded size and SHA-256 |
| Quality | `duration` | A recording's reported duration agrees with the session's within 2 seconds (a warning otherwise) |
| Safety | `consent-recorded` | The session names at least one consent id |
| Safety | `consent-active` | Every consent record exists under `protected/consents/`, matches [`consent-record.schema.json`](../../schemas/consent-record.schema.json), is `active` and has not expired |

The session `passed` only when both groups pass; `eligible_for` then lists the uses every consent permits (`human-review` needs `evaluation`, `analytics` needs `analytics`, `model-improvement` needs `training`). A failed check gives `failed` (exit code 3) and a warning `flagged` (exit code 0), both with an empty `eligible_for`. Speech detection, transcript coverage, sensitive identifiers and content policy belong to your own evaluator.

## Key capabilities

| Who | Capabilities |
|---|---|
| LiveKit Egress | `writeFiles`, limited to `incoming/sessions/`, in a key of its own |
| `register-session` | `readFiles` (to refuse an existing session), `writeFiles` |
| `handle-webhook`, `serve` | `readFiles`, `writeFiles` |
| `evaluate` | `readFiles`, `writeFiles` |
| `synth --put` | `writeFiles` |

Never give this tool a key with `deleteFiles`. Capability names are from the [B2 application keys documentation](https://www.backblaze.com/docs/cloud-storage-application-keys).

## Objects it writes

| Key | Written by | Content |
|---|---|---|
| `incoming/sessions/<room>/<session_id>/session.json` | `register-session` | Participants, consent ids and retention class |
| `incoming/sessions/<room>/<session_id>/<time>.ogg` | LiveKit Egress | The recording (`recording.wav` from `synth --put`) |
| `incoming/sessions/<room>/<session_id>/session-manifest.json` | `handle-webhook`, `serve`, then `evaluate` | The session manifest, written last |
| `work/evaluations/<room>/<session_id>/<time>.json` | `evaluate` | One evaluation result per run |
| `protected/consents/<consent_id>.json` | `synth --put` only | A synthetic consent record; your consent service writes real ones |

The tool creates no bucket, key or rule.

## Configurations it ships

This application applies no bucket configuration: no lifecycle rule, CORS rule, Event Notification rule, Object Lock setting or replication rule. A test registers a hook on its S3 client and fails if the whole workflow sends any request that creates or configures a bucket. The Egress output ships as the `egress-request` command and the [LiveKit Egress template](../../infrastructure/livekit-egress/README.md). The repository's [`examples/`](../../examples/README.md) folder has the lifecycle, Object Lock and Event Notification templates the architecture uses; applying a lifecycle template replaces all of a bucket's lifecycle rules.

## Tests

```bash
pip install -r requirements-dev.txt -e .
python -m pytest -q
```

- The tests start a moto server on `127.0.0.1` and use the fake credential `testing`. They need no network beyond the loopback interface, no B2 account and no LiveKit server.
- Webhook tokens are signed at run time with PyJWT (a development dependency), an implementation independent of the receiver's own verifier, using readable test secrets. No token is stored in the repository.
- Test data comes from `src/voice_session_archive/synth.py` with a fixed seed. No test reads or writes `testdata/synthetic-speech/`.
- `tests/test_live.py` is skipped unless `RUN_B2_LIVE_TESTS=1` and the `B2_*` variables are set, and is not run in CI. It writes, reads and deletes a recording and a session manifest under `live-test/<uuid>/` (its key needs `deleteFiles` for that run). It was run once on 2026-10-16 against a real B2 test bucket and passed: authentication and the endpoint derived from the region, put, head, get, the recording written before the session manifest, the manifest round trip and delete.
- What the tests cover: the manifest written last and valid against its schema; rejection of a wrong secret, a wrong issuer, a changed body, an expired token, `alg` `none` and other algorithms, a token without `exp`, a missing `Authorization`, a body over 1 MiB and a recording key outside the prefix, each without a call to B2; one processing per event id; ignored events and failed Egress results; a missing recording or a size mismatch without a manifest; the Egress request with the region's endpoint, path style and no secret; the loopback receiver; the evaluation and its exit code 3 for `failed`; a demo consent id per session id; that no request configures a bucket; and where the env file is read from.

## Known limitations

- **The live check covers storage operations only.** The CI tests run against moto and signed sample bodies. On 2026-10-16 the opt-in live check passed against a real B2 test bucket for authentication and the endpoint derived from the region, put, head, get, manifest write order and round trip, and delete. It calls the storage functions of this tool, not `handle-webhook` or `serve`. No LiveKit server, LiveKit Egress or real webhook delivery was involved, and no bucket configuration was applied or read.
- **The commands were run once by hand against a real bucket.** On 2026-10-26 a manual walkthrough (not `tests/test_live.py`) ran against a real B2 test bucket with Object Lock and SSE-B2, using a key without `deleteFiles`, with `synth --put` standing in for Egress and no LiveKit server: `register-session`, `egress-request`, `handle-webhook` (`archived`, then `duplicate` for the same event id), `evaluate` (`passed`) and `serve` on the loopback interface (HTTP 200 for `archived` and for `duplicate`, 401 for a malformed token and for a missing Authorization header). The recording and `session.json` were written before the first manifest version, `evaluate` rewrote the manifest as a new version, and the objects reported server-side encryption AES256 and no object retention. A wrong secret, a missing Authorization header, a changed body, an invalid role and a recording key outside `incoming/sessions/` (exit 2), and a duplicate registration, `evaluate` without a manifest, an unregistered session and a missing recording (exit 1) were refused. LiveKit Egress, real webhook delivery, lifecycle expiry and cleanup were not exercised.
- **Webhook fields come from documentation.** The webhook fields (`event`, `id`, `createdAt`, `egressInfo`, `fileResults`, `filename`, `size`, `duration`, `status`) were taken from LiveKit's documentation on 2026-10-15 ([webhooks](https://docs.livekit.io/home/server/webhooks/), [Egress API](https://docs.livekit.io/home/egress/api/), [tokens](https://docs.livekit.io/frontends/authentication/tokens/)). Those pages do not name the signing algorithm or the encoding of the body hash; the receiver accepts only HS256 and a base64 SHA-256, and fails closed on anything else.
- **Timestamps.** LiveKit's API reference does not state the unit of `startedAt` and `endedAt`; the tool tells nanoseconds, microseconds, milliseconds and seconds apart by magnitude. File durations are read as nanoseconds, as documented.
- **One recording layout.** Recordings must be inside `incoming/sessions/<room>/<session_id>/`, as `egress-request` asks; a recording written elsewhere in the room's prefix is reported as a configuration error and gets no manifest.
- **Single organization, single bucket.** There is no per-tenant isolation or per-tenant prefix. Production deployments use one bucket per zone.
- **Retention, protected copies and selection belong to other components.** The manifest records the retention class; applying retention, copying sessions to a protected bucket under Object Lock and selecting sessions into datasets are handled by the components that own them. The evaluation runs only integrity, duration and consent checks.
- **One request at a time.** The receiver hashes the recording before it answers. For long sessions, a production receiver acknowledges quickly and hands the work to a queue.
- **Webhook delivery.** The tool processes the webhooks LiveKit delivers. A periodic check of open sessions, for a webhook that LiveKit stops retrying, belongs to the deployment around it.
