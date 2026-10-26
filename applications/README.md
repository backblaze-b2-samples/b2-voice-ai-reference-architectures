# Sample applications

The applications in this folder are small Python command-line tools that implement one architecture of the [catalog](../catalog/architectures.yaml) each. They live in this repository, one folder per application, and their tests run against an emulated S3 server on the loopback interface, so they need no B2 account and no credentials. An optional `tests/test_live.py` in each application runs against a real bucket only when `RUN_B2_LIVE_TESTS=1` and the `B2_*` variables are set; it is not part of CI. Each one was run once on 2026-10-16 against a real B2 test bucket and covers storage operations only: authentication and endpoint derivation, put, get, head, list, presigned PUT and GET, server-side copy, manifest write order, delete, multipart resume and presigned URL expiry. It does not cover bucket configuration, Event Notification delivery, LiveKit Egress or any named provider. Separately, on 2026-10-26 a manual walkthrough of the `voice-dataset-ingest` commands, including `release` with per-object GOVERNANCE retention, was run against a real B2 test bucket with Object Lock; it covers that application only. Each application's Known limitations says what it did and did not check.

The [architectures](../docs/architecture-index.md) are complete without an application: each one ships its schemas, B2 configuration templates, examples and test fixtures, and can be validated without a B2 account.

## Applications

| Application | Architecture | Run it |
|---|---|---|
| [`voice-dataset-ingest`](voice-dataset-ingest/README.md) | `voice-training-data-lifecycle` | `cd applications/voice-dataset-ingest && pip install -r requirements-dev.txt -e . && python -m pytest -q` |
| [`voice-generation-artifacts`](voice-generation-artifacts/README.md) | `batch-voice-generation-and-dubbing` | `cd applications/voice-generation-artifacts && pip install -r requirements-dev.txt -e . && python -m pytest -q` |
| [`voice-session-archive`](voice-session-archive/README.md) | `voice-agent-session-archive` | `cd applications/voice-session-archive && pip install -r requirements-dev.txt -e . && python -m pytest -q` |

Each application gets a section on this page, a row in the matrix of the `applications` job in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), and a `links.application` field in its catalog entry that points to its README.

## voice-dataset-ingest

### What it shows

Consent-gated ingest of speech clips. A clip is accepted only under an active consent that permits training; it is uploaded with a presigned PUT URL or resumable multipart upload; a signed Event Notification is verified and each `eventId` processed once; the clip is validated and normalized to 16 kHz mono 16-bit PCM; the NeMo manifest is written last; and a published dataset version is protected with per-object Object Lock retention, on a bucket that already has Object Lock enabled. The tool has six commands: `synth`, `presign-upload`, `upload`, `handle-event`, `process` and `release`.

### Architecture

[`voice-training-data-lifecycle`](../architectures/voice-training-data-lifecycle/architecture.md)

### Configurations it ships

None applied. The application never creates or configures a bucket, and a test proves that its workflow sends no such request. It uses the templates in [`examples/`](../examples/README.md): Event Notifications, Object Lock, lifecycle rules and CORS. Applying a lifecycle template replaces all of a bucket's existing lifecycle rules.

### How to run

Prerequisites: Python 3.10 or later; for a real bucket, a private bucket created with Object Lock enabled and an application key limited to it. Environment variables: `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`, `B2_BUCKET_NAME`, `B2_REGION`, and `B2_EVENT_SIGNING_SECRET` for `handle-event`; the endpoint is derived from `B2_REGION`. The command reads `.env` from the current directory only.

```bash
cd applications/voice-dataset-ingest
pip install -r requirements-dev.txt -e .
python -m pytest -q                      # emulated S3, no B2 account, no credentials
voice-dataset-ingest synth --out ./demo  # deterministic synthetic clips and consent records
```

It starts with synthetic data generated from a fixed seed. See the [application README](voice-dataset-ingest/README.md) for the walkthrough, the key capabilities each command needs and the exit codes.

### Known limitations

The tests run against an emulated S3 server; the opt-in live check confirmed put, get, list, presigned PUT, presigned PUT expiry and multipart resume against a real B2 bucket on 2026-10-16, and a manual walkthrough on 2026-10-26 covered `release` with per-object GOVERNANCE retention, but not Event Notification delivery. The tool uses one bucket with zone prefixes (production uses one bucket per zone), covers a single organization with no per-tenant isolation, normalizes audio with a simple standard-library resampler, has no HTTP receiver or queue, and cannot withdraw a published version, because that needs a key with `bypassGovernance` that it never uses. The synthetic clips are tones, not speech.

## voice-generation-artifacts

### What it shows

The artifact side of batch voice generation. A worker receives a presigned GET URL for the source, never a key; every output is kept as an expiring candidate, with one stem per speaker and a mix per language; a reviewed attempt is promoted to a published version by server-side copy, with lineage from the delivered object back to the source version, the candidate, the SHA-256 values and the worker; and a delivery manifest carries presigned GET URLs with a default lifetime of 3600 seconds. The tool has four commands: `synth-source`, `submit`, `promote` and `deliver`. The audio is synthetic placeholder audio: the only worker uses no model, no network service and no provider key, and writes audible tones, not speech. It adds no voice model and no provider adapter.

### Architecture

[`batch-voice-generation-and-dubbing`](../architectures/batch-voice-generation-and-dubbing/architecture.md)

### Configurations it ships

None applied. The application never creates or configures a bucket, and a test proves that its workflow sends no such request. It ships [`lifecycle-single-bucket.json`](voice-generation-artifacts/lifecycle-single-bucket.json), candidate expiry with this tool's single-bucket prefixes, and a command to apply it; applying a lifecycle template replaces all of a bucket's existing lifecycle rules. It also uses the templates in [`examples/`](../examples/README.md).

### How to run

Prerequisites: Python 3.10 or later; for a real bucket, a private bucket and an application key limited to it. Environment variables: `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`, `B2_BUCKET_NAME` and `B2_REGION`; the endpoint is derived from `B2_REGION`. The command reads `.env` from the current directory only.

```bash
cd applications/voice-generation-artifacts
pip install -r requirements-dev.txt -e .
python -m pytest -q                                   # emulated S3, no B2 account, no credentials
voice-generation-artifacts synth-source --out ./demo  # deterministic synthetic source recording
```

It starts with a source generated from a fixed seed. See the [application README](voice-generation-artifacts/README.md) for the walkthrough, the key capabilities each command needs and the exit codes.

### Known limitations

The audio is synthetic placeholder audio, tones rather than speech, and nothing is dubbed or synthesized. The tests run against an emulated S3 server; the opt-in live check confirmed put, get, list, server-side copy, presigned GET and presigned GET expiry against a real B2 bucket on 2026-10-16, but no provider was called. The tool uses one bucket with zone prefixes (production uses one bucket per zone), covers a single organization with no per-tenant isolation, has no review step, consent service or queue, and cannot remove a published version. A delivery manifest is a bearer credential until its URLs expire.

## voice-session-archive

### What it shows

Archiving a LiveKit voice session. LiveKit Egress writes the recording directly to B2 through the S3-compatible endpoint, with its own key that has only `writeFiles` and is limited to `incoming/sessions/`; the application receives the `egress_ended` webhook, verifies its signed token on the raw body (HS256 only, `exp` required, issuer and body hash checked) before any call to B2, processes each event id only once, confirms with a HEAD request that the recording is in the bucket with the reported size, and writes the session manifest last with the evaluation `pending`. An evaluation step then checks integrity, duration and consent and records which uses the session may be selected for. The tool has six commands: `synth`, `register-session`, `egress-request`, `handle-webhook`, `serve` (an HTTP receiver on `127.0.0.1` with a 1 MiB body limit) and `evaluate`.

### Architecture

[`voice-agent-session-archive`](../architectures/voice-agent-session-archive/architecture.md)

### Configurations it ships

None applied. The application never creates or configures a bucket, and a test proves that its workflow sends no such request. It ships the LiveKit Egress output as the `egress-request` command, which always prints placeholders instead of credentials, next to the [LiveKit Egress template](../infrastructure/livekit-egress/README.md). The lifecycle, Object Lock and Event Notification templates are in [`examples/`](../examples/README.md); applying a lifecycle template replaces all of a bucket's existing lifecycle rules.

### How to run

Prerequisites: Python 3.10 or later; for a real recording, a private bucket, an application key for the tool, a separate Egress key with only `writeFiles` on `incoming/sessions/`, and a LiveKit server with Egress (self-hosted or LiveKit Cloud). Environment variables: `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`, `B2_BUCKET_NAME`, `B2_REGION`, and `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` for the webhook; the endpoint is derived from `B2_REGION`. The command reads `.env` from the current directory only.

```bash
cd applications/voice-session-archive
pip install -r requirements-dev.txt -e .
python -m pytest -q   # emulated S3 and signed sample webhooks, no B2 account, no LiveKit server
voice-session-archive synth --out ./demo --room support-room --session-id session-0001
```

It starts with a placeholder recording and an unsigned sample `egress_ended` body generated from a fixed seed. See the [application README](voice-session-archive/README.md) for the walkthrough, the steps for a real LiveKit recording, the key capabilities and the exit codes.

### Known limitations

The tests run against an emulated S3 server and signed sample webhooks; the opt-in live check confirmed put, head, get and the order of the recording and the session manifest against a real B2 bucket on 2026-10-16, without a LiveKit server, so LiveKit Egress and real webhook delivery were not exercised. The placeholder recording is tones, not speech. The tool uses one bucket with zone prefixes (production uses one bucket per zone), covers a single organization with no per-tenant isolation, covers LiveKit only, handles one webhook at a time without a queue, does not copy sessions to a protected zone or apply retention, and runs only integrity, duration and consent checks in its evaluation.

## How each entry is described

Every application listed here has a section with these five parts:

| Part | What it states |
|---|---|
| What it shows | The workflow the application demonstrates and the parts of the architecture it exercises |
| Architecture | The architecture it implements, by catalog id |
| Configurations it ships | The B2 settings the application delivers as files and commands (for example lifecycle, CORS, Event Notifications, Object Lock, replication), and the confirmation that it applies none of them to a bucket |
| How to run | The prerequisites, the environment variables (`B2_*`) and the commands to run it and its tests, including the data it starts with |
| Known limitations | What the application does not do, what is simulated or synthetic, and what was and was not verified |

## Shape of an application

Each application keeps the same shape: `pyproject.toml`, the package in `src/<package>/`, an `.env.example` file with the `B2_*` names, tests in `tests/`, and a README with the sections "B2's role", "Setup", "Tests" and "Known limitations". It uses one bucket with the zones as top-level prefixes (`incoming/`, `work/`, `releases/`, `protected/`); production deployments use one bucket per zone, as the architectures do. Applications never configure a bucket: lifecycle, CORS, Event Notifications, Object Lock and replication ship as templates and commands, and a test in each application proves that its workflow sends no bucket configuration request.

The full rules are in the [Applications section of `AGENTS.md`](../AGENTS.md#applications). `python tools/validate_contracts.py` checks them for every folder in `applications/`, and CI runs it.

An application follows the same B2 conventions as the [examples](../examples/README.md): `B2_*` environment variable names, an endpoint derived from `B2_REGION` and a descriptive user agent.
