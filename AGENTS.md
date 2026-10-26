# Agent guide

Use this repository to design, review or extend storage workflows for voice and speech AI on Backblaze B2: speech training datasets, batch voice generation and dubbing, and voice agent session archives.

## Sources of truth

1. `catalog/architectures.yaml` is the machine-readable catalog of architectures and integrations, validated by `schemas/architecture-catalog.schema.json`.
2. `architectures/<id>/architecture.md` holds each design, its boundaries, where B2 does not fit, security, evidence and known limitations; `decisions.md` and `object-layout.md` sit next to it. The folder name is the catalog id.
3. `schemas/` defines the session, generation job, consent, dataset line, release and delivery manifests; `manifests/` holds a valid example of each.
4. `docs/b2-capabilities.md` maps each B2 capability to what it requires, its limitations and the architectures that use it.
5. Current Backblaze product behavior is authoritative at <https://www.backblaze.com/docs/cloud-storage>. Pricing is authoritative at <https://www.backblaze.com/cloud-storage/pricing>.

State the evidence basis when it matters to a recommendation. Documentation review and emulator-backed tests are not a production deployment. The opt-in live checks (`tests/test_live.py` in each application) were run once on 2026-10-16 against a real B2 test bucket and cover storage operations only: authentication and endpoint derivation, put, get, head, list, presigned PUT and GET, server-side copy, manifest write order, delete, multipart resume and presigned URL expiry. On 2026-10-26 three manual walkthroughs of the application commands, one per application (not `tests/test_live.py`), ran against a real B2 test bucket with Object Lock and SSE-B2, with a key that had no `deleteFiles`. `voice-dataset-ingest`: upload, process, `release` with GOVERNANCE retention (the same retain-until date on all three released objects, read back with `head_object`), the refusal of a repeated release of the same version, the refusal of an upload with an unknown consent id (nothing written) and a presigned PUT. `voice-generation-artifacts`: synth-source, submit, promote with `--approve` and deliver (seven presigned URLs answered 200, an unsigned GET 401), the manifest written last under the candidate and release prefixes, the delivered objects matching the manifest SHA-256, and the refusal of a repeated submit, a promote without `--approve`, a repeated promote and invalid arguments. `voice-session-archive`: synth, register-session, egress-request, handle-webhook (archived, then duplicate), evaluate and serve on the loopback interface, with the recording and `session.json` written before the first manifest, and the refusal of a wrong secret, a missing Authorization header, a changed body and a duplicate registration. After the consent check and `review` were added, those commands were run once by hand on 2026-10-26 against the same kind of test bucket. `submit` with a valid consent record for each speaker succeeded and recorded the consent ids in the job manifest. `submit` was refused with exit code 2, and nothing was found under that job's candidate or release prefix (listed read-only), for a consent id with no record, for a record of another speaker and for a job in which one speaker had no record. `review` printed six presigned URLs; one answered 200 and the same URL without its signature answered 401; with `--out` the URLs went to a file readable by its owner only and the printed summary held none; an out-of-range `--expires` and a nonexistent attempt were refused with exit code 2. `promote --approve` and `deliver` then ran as before. After the exit code and the demo consent id changed, `voice-session-archive` was run once by hand on 2026-10-26 against the same kind of test bucket. `synth --put` for two sessions wrote one consent record per session, under an id that contains the session id, without overwriting the other (one object version each). `evaluate` of a session with a valid consent record printed `passed` and exited 0. `evaluate` of a session registered with a consent id that has no record printed `failed`, exited 3, still printed the result and still wrote the evaluation result and a new manifest version. Bucket configuration, lifecycle expiry, cleanup, Event Notification delivery, LiveKit Egress, real webhook delivery, the named providers, tenant isolation and deletion after retention expires have not been validated live. Do not state more than that.

## Repository layout

| Path | Contents |
|---|---|
| `architectures/` | One folder per architecture |
| `catalog/` | The architecture and integration catalog |
| `docs/` | Category page (`voice-speech-ai.md`), capability map, generated architecture index, related sample applications (`related-b2-samples.md`), overview diagram (`docs/images/`) |
| `examples/` | B2 configuration templates (JSON), scoped key commands, Python (`examples/python/`) and TypeScript (`examples/typescript/`) examples |
| `infrastructure/` | LiveKit Egress request template and the bucket and key provisioning helper |
| `schemas/`, `manifests/` | JSON Schemas and valid example manifests |
| `testdata/synthetic-speech/` | Synthetic speech fixtures, valid and deliberately invalid |
| `skill/` | The `b2-voice-speech-ai` agent skill (`SKILL.md`, `references/`); `skill/references/architecture-catalog.md` is generated |
| `applications/` | Python command-line applications that implement the architectures; see `applications/README.md` |
| `metadata/` | Index of the repository's metadata files |
| `tools/` | Generator, validators, link check, fixture builder, diagram renderer |
| `tests/` | Tests that run without a B2 account |

## Commands

Set up once:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-catalog.txt -r requirements-dev.txt
```

Run before every commit (CI runs the same):

```bash
python -m pytest -q tests
python tools/validate_contracts.py
python tools/validate_contracts.py --capability-map docs/b2-capabilities.md
python tools/generate_catalog.py --check
python tools/check_markdown_links.py
bash tools/run_ts_examples.sh
```

`python tools/check_markdown_links.py --external docs/related-b2-samples.md` checks that the external links of that page respond; it needs network access, so CI does not run it. Re-run the four B2 convention checks on a fresh clone of a sample and update its date on that page before adding or keeping a row.

Run one application's tests from its own folder; CI does the same for each application in the `applications` job matrix. They need no B2 account: the applications use a single `B2_BUCKET_NAME` (a bucket on the emulated S3 server) with the zones as prefixes. The live checks run only on request, as described under Applications.

```bash
cd applications/<name>
pip install -r requirements-dev.txt -e .
python -m pytest -q
```

Regenerate after editing the catalog, and re-render after editing the diagram script:

```bash
python tools/generate_catalog.py
python tools/render_diagram.py
```

`tools/make_fixtures.py` rebuilds the synthetic audio and needs `requirements-fixtures.txt`; CI never runs it and reads the committed fixtures.

## Generated files

Do not hand-edit the generated sections in `README.md` (between the `catalog:` markers), `docs/architecture-index.md`, `skill/references/architecture-catalog.md` or `llms.txt`. Edit the catalog and regenerate in the same change. Commit `docs/images/voice-ai-architectures.png` whenever `tools/render_diagram.py` changes; it must stay 1600x900.

## Rules for changes

- Every architecture keeps the sections the validator requires: Boundaries, Where B2 does not fit, Evidence, Object layout, Security and a Mermaid diagram. A validation status claims no more than its evidence: when only documentation supports an entry, it is `documentation-reviewed`.
- Mark each integration `native-s3-output` only when the tool's own documentation shows it writing to or reading from an S3-compatible endpoint; otherwise it is `application-copy`.
- Attribute to a provider only what its documentation or code shows, with the date it was read. Mark anything unverified.
- Link the B2 pricing page instead of quoting prices or allowances.
- Keep the README sections `Where state lives`, `Clean up`, `Known limitations` (which states that isolation between tenants is not covered) and `Partner integration options`.
- Do not use the em dash character in any file; the validator rejects it.

## Applications

Each application in `applications/<name>/` is a small Python command-line tool that implements one architecture of the catalog. Every application keeps this shape:

- `pyproject.toml` with the project name `<name>`, a console script of the same name and the schemas as package data; the package in `src/<package>/` (the folder name with hyphens replaced by underscores); `requirements.txt` and `requirements-dev.txt` with an upper bound on every dependency; a `.gitignore`.
- A `.env.example` file with `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`, `B2_BUCKET_NAME` and `B2_REGION`, and `B2_PUBLIC_URL_BASE` commented out. The endpoint is derived from `B2_REGION`; do not add an endpoint variable. The command-line entry point reads the env file only from the current directory.
- One bucket per run, with the zones as top-level prefixes (`incoming/`, `work/`, `releases/`, `protected/`). The README says that production deployments use one bucket per zone, as the architectures do.
- Tests in `tests/` against an emulated S3 server (moto) on the loopback interface, with no other network access and no credentials. `tests/test_live.py` runs only when `RUN_B2_LIVE_TESTS=1` and the `B2_*` variables are set, is not part of CI, writes only under `live-test/<uuid>/`, removes what it writes and never calls retention or release operations. A run needs a key with `writeFiles`, `readFiles`, `listFiles` and `deleteFiles` on a test bucket. When a live run is reported, state its date and which checks it covered, and keep the README, the architecture Evidence section and the catalog `validation` block in agreement with it.
- A README with the sections "B2's role", "Setup", "Tests" and "Known limitations".
- A server or webhook receiver only when the workflow needs one; no web frontend or dashboard.
- Bucket configuration (lifecycle, CORS, Event Notifications, Object Lock, replication) ships as templates and commands. Application code never applies it, and a test proves that the workflow sends no bucket configuration request. A README that shows a lifecycle or CORS command says, before the command, that it replaces all of the bucket's rules.
- `storage.py` builds the application's own S3 client, with its own user agent. Other code reused from `examples/python/` or `tools/` is copied unchanged into `src/<package>/shared/`, and schemas into `src/<package>/schemas/`. The validator fails when a copy differs from its source: change the source, then copy it again.
- Each application owns its test and demo data, generated deterministically inside the application (a synthesis module or a seeded fixture written to a temporary directory). It may read `testdata/synthetic-speech/` where that fits, never as a requirement, and it never modifies or regenerates that folder, which stays a fixture of the repository's own tools and tests.
- Each application is listed in the `applications` job matrix of `.github/workflows/ci.yml`, has a section in `applications/README.md`, and is linked from its catalog entry through `links.application`.

## Operational invariants

- Derive the endpoint `https://s3.<region>.backblazeb2.com` from the bucket's region; do not hardcode a real region, bucket or account.
- Read credentials and bucket names from the environment. The examples and architectures use `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`, `B2_REGION`, `B2_INCOMING_BUCKET`, `B2_WORK_BUCKET`, `B2_RELEASES_BUCKET`, `B2_PROTECTED_BUCKET` and `B2_EVENT_SIGNING_SECRET`; the applications use `B2_BUCKET_NAME` instead of the zone buckets. Never commit keys, real presigned URLs, recordings of real people or customer data.
- In the examples and architectures, use one bucket per zone and one scoped application key per component and prefix. The applications use a single `B2_BUCKET_NAME` with the zones as top-level prefixes (see Applications). Keep master or administrative keys out of runtime services.
- Treat presigned URLs and delivery manifests as bearer credentials: short lifetimes, never logged.
- Write each manifest last, as the completion marker, and never rewrite a published release or delivery version.
- Verify Event Notification signatures on the raw body and process each `eventId` once; delivery is at least once.
- Check consent before processing, generating or selecting voice data.
- Keep live audio, in-call state, queues, models, orchestration, catalogs and vector search off object storage.
- Applying a lifecycle template replaces all of a bucket's rules: read and merge the current rules first.
- The provisioning helper prints commands by default; `--apply` executes them and `--cleanup` only prints removal commands.
