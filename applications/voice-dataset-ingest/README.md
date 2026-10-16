# voice-dataset-ingest

A small Python command-line tool that implements the [voice training data lifecycle](../../architectures/voice-training-data-lifecycle/architecture.md) on Backblaze B2: it accepts speech clips only under a current consent, uploads them with a presigned URL or resumable multipart upload, verifies signed Event Notifications, validates and normalizes each clip, writes a NeMo-style manifest last, and publishes an immutable dataset version with per-object Object Lock retention.

The tests run against an emulated S3 server on the loopback interface, so they need no B2 account and no credentials. Nothing here has been run against a live B2 account by this repository's CI; see [Known limitations](#known-limitations).

## Quick start

```bash
cd applications/voice-dataset-ingest
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -e .
python -m pytest -q
```

To try it against a bucket you own, follow [Setup](#setup) and the [walkthrough](#walkthrough).

## Commands

| Command | What it does |
|---|---|
| `synth` | Writes deterministic synthetic clips and consent records to a local folder. With `--put-consents` it also writes the synthetic consent records to `protected/consents/`. |
| `presign-upload` | Checks the consent, writes the clip's sidecar and prints a presigned PUT URL for one clip. |
| `upload` | Checks the consent, then uploads one clip with multipart upload, retrying and resuming an unfinished upload. |
| `handle-event` | Verifies one Event Notifications delivery (`--body` file and `--signature` value), claims each `eventId` once and processes the clip it names. |
| `process` | Validates and normalizes every clip of a batch, then writes the batch manifest last. |
| `release` | Publishes a version of the dataset with per-object Object Lock retention. |

There is no HTTP receiver in this tool. Whoever operates it delivers the webhook: save the request body to a file and call `handle-event` with that file and the value of the `X-Bz-Event-Notification-Signature` header.

## B2's role

- **Storage for every stage.** One bucket holds the whole workflow, with the zones as prefixes: `incoming/contributions/`, `work/candidates/`, `work/evaluations/`, `releases/datasets/` and `protected/consents/`. Production deployments use one bucket per zone, as the architecture does, so that lifecycle, Object Lock, replication, encryption and notification rules and application keys can be set per zone. This tool uses a single bucket (`B2_BUCKET_NAME`) to stay easy to run.
- **Presigned PUT and multipart upload.** Contributors can upload with a URL that signs one object and expires, without holding a key. Large files use multipart upload, and an interrupted upload resumes from the parts already stored.
- **Event Notifications.** B2 signs each webhook body with HMAC SHA-256. Delivery is at least once, so each `eventId` is processed once.
- **Object Lock.** A published version is written with governance-mode retention on each object, so it cannot be deleted or overwritten by mistake until the retention date. The bucket must already have Object Lock enabled; enabling it is permanent and is a bucket setting this tool never changes.
- **Not B2's job.** Consent decisions, transcription, redaction and review stay in your application. B2 stores the consent records and enforces the retention.

## Setup

1. Create a private bucket with Object Lock enabled at creation. Object Lock cannot be turned off afterwards, and objects under retention cannot be deleted before it ends, so use a bucket meant for this. See [`examples/object-lock.json`](../../examples/object-lock.json) and the [examples README](../../examples/README.md#object-lock-default-retention-protected-zone).
2. Create an application key limited to that bucket (see [Key capabilities](#key-capabilities-per-command)).
3. Copy `.env.example` to `.env` in the directory you run the command from and fill it in. The command reads `.env` from the current directory only, never from a parent directory, and variables already present in the environment take precedence. The endpoint is derived from `B2_REGION`; there is no endpoint setting.

| Variable | Purpose |
|---|---|
| `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY` | The application key |
| `B2_BUCKET_NAME` | The bucket |
| `B2_REGION` | The bucket's region, for example `us-west-004` |
| `B2_PUBLIC_URL_BASE` | Optional and unused by this tool; the bucket stays private |
| `B2_EVENT_SIGNING_SECRET` | The signing secret of the Event Notification rule; needed only by `handle-event` |

Install with `pip install -e .` (Python 3.10 or later). The runtime dependencies are `boto3`, `jsonschema` and `python-dotenv`.

## Walkthrough

The synthetic data is generated from a fixed seed: tones that stand in for speech, a few deliberately invalid clips, and consent records that are active, revoked, expired or without training use. No recording of a person is involved.

```bash
voice-dataset-ingest synth --out ./demo --put-consents
voice-dataset-ingest upload --batch demo-1 --file demo/clips/utt_0001.wav \
  --speaker spk_a --consent-id consent_synth_spk_a --text "The delivery window opens at nine."
voice-dataset-ingest upload --batch demo-1 --file demo/clips/utt_0003.wav \
  --speaker spk_a --consent-id consent_synth_spk_a --text "A silent clip."
voice-dataset-ingest process --batch demo-1
voice-dataset-ingest release --batch demo-1 --dataset demo --dataset-version v1 --retain-days 1
```

`utt_0003` is silent, so `process` writes it to `work/candidates/demo-1/rejected/` with its reason and leaves it out of the manifest. `release` writes `releases/datasets/demo/v1/`. Objects written by `release` cannot be deleted until their retention ends, so keep `--retain-days` short while you try it.

Uploading a clip of `spk_b`, `spk_c` or `spk_d` with their consent ids is refused, because their consents are revoked, expired or do not permit training.

To read the repository's own fixtures instead, read-only, put one consent record in the bucket and upload a fixture clip:

```bash
b2 file upload "$B2_BUCKET_NAME" ../../testdata/synthetic-speech/consents/consent_spk_a.json protected/consents/consent_spk_a.json
voice-dataset-ingest upload --batch fixtures-1 --file ../../testdata/synthetic-speech/audio/utt_0001.wav \
  --speaker spk_a --consent-id consent_spk_a --text "The delivery window opens at nine in the morning."
```

Presigned URLs and delivery of events:

```bash
voice-dataset-ingest presign-upload --batch demo-1 --clip-id utt_0002 \
  --speaker spk_a --consent-id consent_synth_spk_a --text "A second clip." --expires 600
# upload with the printed URL and headers, for example:
#   curl -X PUT -H "Content-Type: audio/wav" --data-binary @demo/clips/utt_0002.wav "<printed url>"
voice-dataset-ingest handle-event --body event.json --signature "v1=<hex digest from the header>"
```

A presigned URL is a bearer credential until it expires: do not log it or share it.

## Consent rules

A consent record is valid for a clip only when all of these hold:

- it validates against [`consent-record.schema.json`](../../schemas/consent-record.schema.json);
- `status` is `active`;
- `expires_at` is absent or in the future;
- `permitted_uses` includes `training`;
- `subject.speaker_id` matches the speaker of the clip.

Records live at `protected/consents/<consent_id>.json`. `presign-upload`, `upload`, `process` and `release` all require a valid record, and `release` checks it again for every clip, so a consent that is revoked or expires after upload stops publication, not only upload. A clip whose consent is no longer valid is left out of the version and reported in the command's output.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Processed, or ignored on purpose (for example an event for another prefix, or a repeated event) |
| 2 | Rejected: a signature, a consent, an event or an input did not pass its check. Nothing is written to B2; signature, size and input checks happen before any call to B2 |
| 1 | Any other error: a B2 error, a missing setting, a bucket without Object Lock, a version that already exists |

`handle-event` rejects a body larger than 1 MiB. A failed delivery releases its event claims, so B2's retry processes the events again.

## Key capabilities per command

Create the application key with only what the commands you run need, and limit it to the bucket. Reading a consent record needs `readFiles` for every command that checks consent.

| Command | Capabilities |
|---|---|
| `presign-upload` | `readFiles`, `writeFiles` |
| `upload` | `readFiles`, `writeFiles`, `listFiles` (to find an unfinished upload and its parts) |
| `handle-event`, `process` | `readFiles`, `listFiles`, `writeFiles` |
| `release` | `readFiles`, `listFiles`, `writeFiles`, plus `readBucketRetentions` and `writeFileRetentions` |
| `synth --put-consents` | `writeFiles` |

Never give this tool a key with `bypassGovernance` or `deleteFiles`. Deleting a version before its retention ends needs a key with `bypassGovernance`, which the tool does not use. Capability names are from the [B2 application keys documentation](https://www.backblaze.com/docs/cloud-storage-application-keys), read on 2026-10-15.

## Object Lock

`release` reads the bucket's Object Lock configuration (a read-only call) and refuses to publish unless Object Lock is enabled, pointing to `examples/object-lock.json`. When it is, it writes each object of the version, that is the accepted clips, `manifests/manifest.jsonl` and, last, `release-manifest.json`, with `GOVERNANCE` mode and a retention date of now plus `--retain-days`.

- `--retain-days` is required, positive and at most 3650 unless `--allow-long-retention` is passed.
- The tool never sets a default retention for the bucket and never changes Object Lock settings.
- It refuses to rewrite an existing version: a change is a new version.
- Governance mode is the only mode offered. Compliance mode would prevent withdrawing a version after a consent is revoked.
- Enabling Object Lock on a bucket is permanent, and objects under retention keep occupying storage until they expire.

## Objects it writes

| Prefix | Written by | Content |
|---|---|---|
| `protected/consents/<consent_id>.json` | `synth --put-consents` only | Synthetic consent records |
| `incoming/contributions/<batch>/<clip_id>.wav` and `.json` | `presign-upload`, `upload` | The clip and its sidecar (speaker, consent id, transcript, language, split) |
| `work/candidates/<batch>/audio/<clip_id>.wav` | `process`, `handle-event` | The normalized clip: 16 kHz, mono, 16-bit PCM |
| `work/candidates/<batch>/rejected/<clip_id>.wav` and `.json` | `process`, `handle-event` | A copy of a rejected clip and its reasons |
| `work/candidates/<batch>/results/<clip_id>.json` | `process`, `handle-event` | The decision for each clip |
| `work/candidates/<batch>/manifest.jsonl` | `process`, `handle-event` | The NeMo manifest, written last |
| `work/evaluations/validation/<batch>/report.json` | `process` | Counts and rejected clips |
| `releases/datasets/<dataset>/<version>/...` | `release` | Audio, `manifests/manifest.jsonl` and, last, `release-manifest.json`, each with retention |

The tool creates no bucket, key or rule.

## Configurations it ships

This application applies no bucket configuration: no lifecycle rule, CORS rule, Event Notification rule, Object Lock setting or replication rule. A test registers a hook on its S3 client and fails if the whole workflow sends any request that creates or configures a bucket. The templates and commands live in [`examples/`](../../examples/README.md):

- [`event-notifications.json`](../../examples/event-notifications.json): the rule that sends `b2:ObjectCreated:*` events for the contributions prefix to your webhook, which then calls `handle-event`.
- [`object-lock.json`](../../examples/object-lock.json): Object Lock settings for the bucket.
- [`lifecycle-incoming.json`](../../examples/lifecycle-incoming.json), [`lifecycle-work.json`](../../examples/lifecycle-work.json), [`lifecycle-releases.json`](../../examples/lifecycle-releases.json): expiry for raw uploads, candidates and rejected clips, and hidden release versions.
- [`cors.s3.json`](../../examples/cors.s3.json): CORS for browser uploads with presigned URLs.

The templates are written for one bucket per zone, where prefixes start at `contributions/` or `candidates/`. With this tool's single bucket, put the zone in front (for example `incoming/contributions/`) before applying them. Applying a lifecycle template replaces all of the bucket's existing lifecycle rules, so read the current rules and merge them first.

## Tests

```bash
pip install -r requirements-dev.txt -e .
python -m pytest -q
```

- The tests start a moto server on `127.0.0.1` (a real server is needed because presigned URLs are fetched over HTTP) and use the fake credential `testing`. They need no network beyond the loopback interface and no B2 account.
- Test data is generated by `src/voice_dataset_ingest/synth.py` from a fixed seed into a temporary directory. No test reads or writes `testdata/synthetic-speech/`.
- `tests/test_live.py` is skipped unless `RUN_B2_LIVE_TESTS=1` and the `B2_*` variables are set. It writes only under `live-test/<uuid>/`, deletes what it writes (its key needs `deleteFiles` for that run) and never calls `release`.
- What the tests cover: each consent failure at upload and at processing, presigned and resumable multipart upload, invalid and repeated event signatures, clip rejection reasons, normalization, manifest order, release retention per object, a bucket without Object Lock, an existing version, the retention cap, that no request configures a bucket, and where the env file is read from.

## Known limitations

- **Emulated S3 only.** The tests run against moto, not against a B2 account. Per-object governance retention, the Content-MD5 header that retention writes carry, and the Object Lock configuration read are exercised against the emulator and are not yet checked against a live bucket.
- **Single organization, single bucket.** There is no per-tenant isolation or per-tenant prefix. Production deployments use one bucket per zone.
- **Simple audio processing.** Normalization uses a basic resampler and downmix written with the standard library. It checks readable 16-bit PCM WAV files; other formats are rejected. It does not transcribe, align, redact or deduplicate.
- **No receiver, queue or scheduler.** Delivering webhooks, running `process` and `release`, and retrying are left to whoever operates the tool.
- **Candidates are not deleted.** A clip rejected after an earlier acceptance (for example because its consent was revoked) leaves its old candidate file in place and drops out of the manifest, because the tool does not hold `deleteFiles`. Lifecycle rules, applied by you, expire candidates.
- **Releases cannot be withdrawn by this tool.** Removing a version before its retention ends needs a key with `bypassGovernance`, which this tool never uses. Withdrawal is an operator action outside it.
- **Synthetic data.** The data from `synth` is tones, not speech, and its consent records describe synthetic subjects.
- **Region and bucket rules are not validated beyond their shape.** The tool derives the endpoint from `B2_REGION` and does not check that the bucket lives in that region.

## When to use it

Use it to see, in a few hundred lines, how consent gating, signed events, a manifest written last and per-object retention fit together on B2, or as a starting point for an ingest service. Do not use it as a production pipeline as it is: add the receiver, queue, review and monitoring your deployment needs.
