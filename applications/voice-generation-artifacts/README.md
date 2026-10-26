# voice-generation-artifacts

A small Python command-line tool that implements the [batch voice generation and dubbing](../../architectures/batch-voice-generation-and-dubbing/architecture.md) architecture on Backblaze B2: it checks that a current consent record permits generation for every speaker, hands a source recording to a worker through a presigned GET URL, keeps every output as an expiring candidate with one stem per speaker, signs short-lived URLs so reviewers can listen to the candidates, promotes approved candidates to a published version by server-side copy with lineage, and issues a delivery manifest of presigned URLs.

**The audio it produces is synthetic placeholder audio.** The only worker in this repository, `PlaceholderWorker`, uses no model, no network service and no provider key. It writes audible tones of the source's duration, one stem per speaker and language plus a mix. It does not dub, translate or synthesize speech, and this tool adds no voice model and no provider adapter. The `Worker` interface is where one would plug in.

The tests run against an emulated S3 server on the loopback interface, so they need no B2 account and no credentials. An opt-in live check, which is not part of CI, was run against a real B2 test bucket on 2026-10-16 and covers storage operations only. A manual walkthrough of `synth-source`, `submit`, `promote` and `deliver` was run against a real B2 test bucket with Object Lock on 2026-10-26; see [Known limitations](#known-limitations) for what each run did and did not check.

## Quick start

```bash
cd applications/voice-generation-artifacts
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -e .
python -m pytest -q
```

To try it against a bucket you own, follow [Setup](#setup) and the [walkthrough](#walkthrough).

## Commands

| Command | What it does |
|---|---|
| `synth-source` | Writes a deterministic synthetic source recording and one synthetic consent record per speaker (`--speaker`, by default `spk_a` and `spk_b`) to a local folder. The consent ids are `consent-<source_id>-<speaker>`, so runs with different source ids that share a bucket keep separate records. With `--put` it also uploads the source to `incoming/source/`, and with `--put-consents` it writes the consent records to `protected/consents/`. |
| `submit` | Checks the consent records named with `--consent-id` (see [Consent](#consent)), signs a short-lived GET URL for the source, runs the placeholder worker and stores its outputs as candidates under `work/candidates/<job>/<attempt>/`, with the job manifest written last. |
| `review` | Prints a short-lived presigned GET URL for every candidate of an attempt, so a reviewer can listen without a key. `--expires` defaults to 300 seconds and is at most 3600; `--out` writes the URLs to a file only its owner can read and prints a summary without them. It writes nothing to B2. |
| `promote` | Copies an approved attempt inside B2 to `releases/deliveries/<job>/<version>/` and writes the version's `job-manifest.json` last, with the review decision and lineage. Needs `--approve`. |
| `deliver` | Signs a GET URL for every object of a published version and stores a delivery manifest. `--expires` defaults to 3600 seconds. |

## B2's role

- **Storage for every stage.** One bucket holds the whole workflow, with the zones as prefixes: `incoming/source/`, `work/candidates/`, `releases/deliveries/` and `releases/delivery-manifests/`. Production deployments use one bucket per zone, as the architecture does, so that lifecycle, Object Lock, replication and encryption rules and application keys can be set per zone. This tool uses a single bucket (`B2_BUCKET_NAME`) to stay easy to run.
- **Presigned GET URLs.** A worker that runs elsewhere, such as a hosted provider, reads the source from a URL that signs one object and expires, so it never holds a key. `submit` gives the worker such a URL, and `deliver` issues URLs to recipients.
- **Server-side copy.** `promote` publishes a candidate with a copy request inside B2. The audio is not downloaded and uploaded again, so promotion does not depend on the operator's bandwidth.
- **Lifecycle rules.** Candidates that are not promoted expire on their own. The template and command are in [Configurations it ships](#configurations-it-ships).
- **Consent records next to the work.** Consent records live in the bucket under `protected/consents/`. `submit` reads them before it reads the source or writes anything, and records the consent ids in the job manifest.
- **Not B2's job.** The generation itself, the review decision and granting or revoking consent stay in your application and your consent service.

## Setup

1. Create a private bucket. See the [examples README](../../examples/README.md) for the commands.
2. Create an application key limited to that bucket (see [Key capabilities](#key-capabilities-per-command)).
3. Copy `.env.example` to `.env` in the directory you run the command from and fill it in. The command reads `.env` from the current directory only, never from a parent directory, and variables already present in the environment take precedence. The endpoint is derived from `B2_REGION`; there is no endpoint setting.

| Variable | Purpose |
|---|---|
| `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY` | The application key |
| `B2_BUCKET_NAME` | The bucket |
| `B2_REGION` | The bucket's region, for example `us-west-004` |
| `B2_PUBLIC_URL_BASE` | Optional and unused by this tool; see the delivery manifest below |

Install with `pip install -e .` (Python 3.10 or later). The runtime dependencies are `boto3`, `jsonschema` and `python-dotenv`.

## Walkthrough

The source is generated from a fixed seed: a tone that stands in for a recording, so no person is recorded and no model is involved. Run the commands from an empty working folder; they write the demo files to `./demo` in the current directory.

Each run uses its own source id and job id, so several people can follow these steps against one bucket. `synth-source` prints the consent ids it wrote as `consent_ids`.

```bash
RUN="$(date -u +%Y%m%d%H%M%S)"
voice-generation-artifacts synth-source --out ./demo --put --put-consents --source-id "interview-$RUN" --seconds 4
voice-generation-artifacts submit --source-id "interview-$RUN" --job-id "job-$RUN" \
  --target-language es --target-language fr --speaker spk_a --speaker spk_b \
  --consent-id "consent-interview-$RUN-spk_a" --consent-id "consent-interview-$RUN-spk_b"
voice-generation-artifacts review --job-id "job-$RUN" --attempt a1 --out ./demo/review.json
# listen to the candidates through the URLs in ./demo/review.json, then:
voice-generation-artifacts promote --job-id "job-$RUN" --attempt a1 --version v1 --approve
voice-generation-artifacts deliver --job-id "job-$RUN" --version v1 --out ./demo/delivery.json
```

`submit` writes, for each target language, `<language>/mix.wav` and one `<language>/<speaker>.wav` stem per speaker, then `job-manifest.json`. `promote` writes the same paths under `releases/deliveries/<job>/v1/`. A change after review is a new attempt (`--attempt a2`) and, once approved, a new version.

Each output's `expires_at` in the manifest is `--candidate-days` after the job was created (15 by default, which matches the 14 days to hide plus 1 day to delete in the lifecycle template). The manifest records when a candidate is expected to be removed; the removal itself is done by the lifecycle rule you apply.

## Consent

`submit` needs a `--consent-id` for the job's speakers and checks each record at `protected/consents/<consent_id>.json` before it reads the source or writes anything. A record is valid only when all of these hold, following the same rules as [`voice-dataset-ingest`](../voice-dataset-ingest/README.md) with `generation` as the required use:

- it validates against [`consent-record.schema.json`](../../schemas/consent-record.schema.json) (an unchanged copy ships inside the package) and carries the consent id it is stored under;
- `status` is `active`, and `expires_at` is absent or in the future;
- `permitted_uses` includes `generation`;
- `subject.speaker_id` is one of the job's speakers.

Every speaker of the job must be covered by at least one valid record. Otherwise `submit` exits with code 2, names the consent or the speaker in its message and writes nothing. `synth-source --put-consents` writes synthetic records that pass these checks; in production your consent service writes them.

## Review URLs

`review` reads the attempt's job manifest, confirms that every candidate exists with the size the manifest records, and signs one GET URL per candidate. The URLs are bearer credentials, handled like the delivery manifest's: they are never stored in the bucket or logged, `--out` writes them to a file only its owner can read, and the default lifetime of 300 seconds (at most 3600) is just long enough to listen. The reviewer needs no key. The decision is then confirmed with `promote --approve`.

## Delivery manifest

`deliver` writes a manifest that follows [`delivery-manifest.schema.json`](../../schemas/delivery-manifest.schema.json): the release id, the issue and expiry times, and one presigned GET URL for each object of the version, including `job-manifest.json`.

**The delivery manifest is a bearer credential.** Anyone who holds it can read every object it lists until `expires_at`, without a key. So:

- Treat it like a secret: do not log it, commit it or paste it into a ticket. The command prints a summary with no URL; `--out` writes a copy that only its owner can read.
- It is stored in the private bucket under `releases/delivery-manifests/`. Never put it in a public bucket and never serve it behind `B2_PUBLIC_URL_BASE` or any other public URL.
- `--expires` defaults to 3600 seconds and is at most 604800 (the longest lifetime the manifest schema allows). Use the shortest lifetime that lets the recipient finish downloading. A URL carries the permissions of the key that signed it, so sign with a key that can only read.
- The shipped lifecycle template removes stored delivery manifests after 9 days, longer than any URL they hold stays valid.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Done |
| 2 | Rejected: an identifier, a language, a speaker, an expiry, a source or a consent record did not pass its check, `review` named an attempt that does not exist, or `promote` was run without `--approve`. Nothing is written to B2 for that command |
| 1 | Any other error: a B2 error, a missing setting, an attempt or version that already exists, a candidate that no longer matches its manifest |

## Key capabilities per command

Create the application key with only what the commands you run need, and limit it to the bucket.

| Command | Capabilities |
|---|---|
| `synth-source --put`, `synth-source --put-consents` | `writeFiles` |
| `submit` | `readFiles` (the consent records and the source), `listFiles` (to refuse an attempt that exists), `writeFiles` |
| `review` | `readFiles` |
| `promote` | `readFiles`, `listFiles`, `writeFiles` |
| `deliver` | `readFiles`, `writeFiles` |

Never give this tool a key with `deleteFiles`. Capability names are from the [B2 application keys documentation](https://www.backblaze.com/docs/cloud-storage-application-keys).

## Objects it writes

| Prefix | Written by | Content |
|---|---|---|
| `incoming/source/<source_id>.wav` | `synth-source --put` | A synthetic source recording |
| `protected/consents/<consent_id>.json` | `synth-source --put-consents` only | A synthetic consent record; your consent service writes real ones |
| `work/candidates/<job>/<attempt>/<language>/mix.wav` | `submit` | The mix of a language, synthetic placeholder audio |
| `work/candidates/<job>/<attempt>/<language>/<speaker>.wav` | `submit` | The stem of one speaker in one language |
| `work/candidates/<job>/<attempt>/job-manifest.json` | `submit` | The job manifest, written last. It records the source key, version and SHA-256, the worker, each output's key, size, SHA-256 and expiry, and the review status `pending` |
| `releases/deliveries/<job>/<version>/<language>/...` | `promote` | Copies of the approved outputs |
| `releases/deliveries/<job>/<version>/job-manifest.json` | `promote` | The same manifest with `review.status` `approved` and a `promotion` block, written last. A version exists only when this object does |
| `releases/delivery-manifests/<job>/<version>/<id>.json` | `deliver` | A delivery manifest. A bearer credential |

The tool creates no bucket, key or rule. The manifests follow [`generation-job-manifest.schema.json`](../../schemas/generation-job-manifest.schema.json) and the delivery schema above; unchanged copies of the schemas ship inside the package.

### Lineage

The published `job-manifest.json` answers where each delivered object came from. `input.key`, `input.version_id` (when B2 reports one) and `input.sha256` identify the source; each `outputs[].key` is the candidate that was copied, at the same path under the version prefix; `provider.name` is the worker (`placeholder-tone-v1`); `promotion.lineage` repeats the job id, the input SHA-256 and the output SHA-256 values. Promotion checks that every candidate still has the size its manifest records before it copies anything.

## Configurations it ships

This application applies no bucket configuration: no lifecycle rule, CORS rule, Event Notification rule, Object Lock setting or replication rule. A test registers a hook on its S3 client and fails if the whole workflow sends any request that creates or configures a bucket. Candidate expiry ships as a template and a command that you run yourself:

> **WARNING: applying lifecycle rules replaces ALL of the bucket's existing lifecycle rules.** The list you pass becomes the bucket's complete list. Read the current rules first with `b2 bucket get "$B2_BUCKET_NAME"`, merge them into the template, and only then apply.

```bash
b2 bucket update --lifecycle-rules "$(cat lifecycle-single-bucket.json)" "$B2_BUCKET_NAME"
```

[`lifecycle-single-bucket.json`](lifecycle-single-bucket.json) expires unpromoted candidates (14 days to hide, 1 day to delete), expires stored delivery manifests after 9 days, and cancels unfinished large uploads. It is the repository's [`lifecycle-work.json`](../../examples/lifecycle-work.json) with this tool's single-bucket prefixes. The [examples README](../../examples/README.md) has the other templates, such as [`lifecycle-releases.json`](../../examples/lifecycle-releases.json) for hidden delivery versions in a bucket of their own.

## Tests

```bash
pip install -r requirements-dev.txt -e .
python -m pytest -q
```

- The tests start a moto server on `127.0.0.1` (a real server is needed because presigned URLs are fetched over HTTP) and use the fake credential `testing`. They need no network beyond the loopback interface and no B2 account.
- Test data is generated by `src/voice_generation_artifacts/synth.py` from a fixed seed. No test reads or writes `testdata/synthetic-speech/`.
- `tests/test_live.py` is skipped unless `RUN_B2_LIVE_TESTS=1` and the `B2_*` variables are set. It is not run in CI. It writes only under `live-test/<uuid>/` and deletes what it writes (its key needs `deleteFiles` for that run). Against a real bucket it checks a put, a server-side copy, a presigned GET, and that a presigned GET URL signed for 3 seconds works at first and is refused after that. Real B2 was observed to answer HTTP 401 for an expired presigned URL on 2026-10-16; that is observed behaviour, not a documented guarantee, so the test accepts 401 or 403. It was run once on 2026-10-16 against a real B2 test bucket and passed: authentication and the endpoint derived from the region, put, get, list, server-side copy, a presigned GET through the real endpoint, delete, and presigned URL expiry (an expired URL is refused).
- What the tests cover: audible and repeatable placeholder audio, the job manifest against its schema, the consent check (a missing, revoked, expired or invalid record, a record without `generation`, another speaker, a speaker without consent: each refused with exit code 2 before the source is read and with nothing written), `review` URLs that read the candidates with the default and maximum lifetime and write nothing to B2, candidates only under the candidates prefix, stems per speaker, server-side copy with lineage, a version that is never rewritten, the delivery manifest written last, presigned URLs that work and expire as declared, the default and maximum lifetime, that no request configures a bucket, and where the env file is read from.

## Known limitations

- **Synthetic placeholder audio only.** The worker produces tones, not speech. There is no model and no provider adapter, so nothing is dubbed, translated or synthesized.
- **The live check covers storage operations only.** The CI tests run against moto. On 2026-10-16 the opt-in live check passed against a real B2 test bucket for authentication and the endpoint derived from the region, put, get, list, server-side copy, presigned GET, delete, and presigned URL expiry (real B2 answered HTTP 401 for an expired URL on that date; that is observed behavior, not a documented guarantee). The check calls the storage functions of this tool and the S3 operations they wrap, not the commands. The size limit of a single copy request on B2 was not checked; each object is copied with one request.
- **The commands were run once by hand against a real bucket.** On 2026-10-26 a manual walkthrough (not `tests/test_live.py`) ran against a real B2 test bucket with Object Lock and SSE-B2, using a key without `deleteFiles`: `synth-source --put`, `submit` (two languages, two speakers, six outputs), `promote --approve` (six objects copied to the version prefix) and `deliver` (seven presigned URLs, all answered 200; an unsigned GET answered 401). The job manifest was the last object written under the candidate prefix and under the version prefix, the six delivered objects matched the SHA-256 values in the manifest, the manifest held no signed URL, and the objects reported server-side encryption AES256 and no Object Lock retention, because the tool sets none. A repeated `submit` of an attempt (exit 1), `promote` without `--approve` (exit 2), a repeated `promote` of a version (exit 1), an out-of-range `--expires`, a bad source id, a missing source and a nonexistent attempt (exit 2) were refused. Lifecycle expiry of candidates and cleanup were not observed (the key has no `deleteFiles`); only the `expires_at` field in the manifest was. After the consent check and `review` were added, those commands were run once by hand on 2026-10-26 against the same kind of test bucket. `submit` with a valid consent record for each speaker succeeded and recorded the consent ids in the job manifest. `submit` was refused with exit code 2, and nothing was found under that job's candidate or release prefix (listed read-only), for a consent id with no record, for a record of another speaker and for a job in which one speaker had no record. `review` printed six presigned URLs; one answered 200 and the same URL without its signature answered 401; with `--out` the URLs went to a file readable by its owner only and the printed summary held none; an out-of-range `--expires` and a nonexistent attempt were refused with exit code 2. `promote --approve` and `deliver` then ran as before.
- **No provider and no bucket configuration were involved.** ElevenLabs, Deepgram and AssemblyAI were not called, and no lifecycle, CORS, notification or other bucket configuration was applied or read; those templates are checked for shape only.
- **Single organization, single bucket.** There is no per-tenant isolation or per-tenant prefix. Production deployments use one bucket per zone.
- **Review decisions, consent records and scheduling belong to the operator.** `review` gives reviewers short-lived URLs, and the decision is confirmed with `promote --approve`. Your consent service grants and revokes the records that `submit` checks. Running commands, retrying and scheduling are handled by whoever operates the tool.
- **A failed promotion needs a new version name.** The tool refuses to write into a version prefix that already holds objects, and it has no `deleteFiles`, so objects left by a failed copy stay until you remove them. They carry no `job-manifest.json`, so they are not a published version.
- **Lifecycle rules remove candidates and delivery manifests.** The tool holds no `deleteFiles`; the lifecycle rules you apply expire them.
- **Withdrawal is an operator action.** Presigned URLs already issued work until they expire, and copies already delivered stay with their recipients. Removing a published version is an operator step outside the tool.
- **Small sources.** The placeholder worker reads the source into memory and accepts WAV files of at most 120 seconds.

## When to use it

Use it to see, in a few hundred lines, how consent checks, candidates, review, promotion by copy, lineage and bearer-style delivery manifests fit together on B2, or as a starting point for a job runner: replace `PlaceholderWorker` with an adapter for your provider or model. It is a reference implementation; a production pipeline adds the provider adapter, a review interface and scheduling your deployment needs.
