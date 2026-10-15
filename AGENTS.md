# Agent guide

Use this repository to design, review or extend storage workflows for voice and speech AI on Backblaze B2: speech training datasets, batch voice generation and dubbing, and voice agent session archives.

## Sources of truth

1. `catalog/architectures.yaml` is the machine-readable catalog of architectures and integrations, validated by `schemas/architecture-catalog.schema.json`.
2. `architectures/<id>/architecture.md` holds each design, its boundaries, where B2 does not fit, security, evidence and known limitations; `decisions.md` and `object-layout.md` sit next to it. The folder name is the catalog id.
3. `schemas/` defines the session, generation job, consent, dataset line, release and delivery manifests; `manifests/` holds a valid example of each.
4. `docs/b2-capabilities.md` maps each B2 capability to what it requires, its limitations and the architectures that use it.
5. Current Backblaze product behavior is authoritative at <https://www.backblaze.com/docs/cloud-storage>. Pricing is authoritative at <https://www.backblaze.com/cloud-storage/pricing>.

State the evidence basis when it matters to a recommendation. Documentation review and emulator-backed tests are not a production deployment, and nothing here has been run against a live B2 account or the named providers.

## Repository layout

| Path | Contents |
|---|---|
| `architectures/` | One folder per architecture |
| `catalog/` | The architecture and integration catalog |
| `docs/` | Capability map, generated architecture index, overview diagram (`docs/images/`) |
| `examples/` | B2 configuration templates (JSON), scoped key commands, Python (`examples/python/`) and TypeScript (`examples/typescript/`) examples |
| `infrastructure/` | LiveKit Egress request template and the bucket and key provisioning helper |
| `schemas/`, `manifests/` | JSON Schemas and valid example manifests |
| `testdata/synthetic-speech/` | Synthetic speech fixtures, valid and deliberately invalid |
| `skill/` | Agent skill material; `skill/references/architecture-catalog.md` is generated |
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

## Operational invariants

- Derive the endpoint `https://s3.<region>.backblazeb2.com` from the bucket's region; do not hardcode a real region, bucket or account.
- Read credentials and bucket names from the environment (`B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`, `B2_REGION`, `B2_INCOMING_BUCKET`, `B2_WORK_BUCKET`, `B2_RELEASES_BUCKET`, `B2_PROTECTED_BUCKET`, `B2_EVENT_SIGNING_SECRET`). Never commit keys, real presigned URLs, recordings of real people or customer data.
- Use one bucket per zone and one scoped application key per component and prefix. Keep master or administrative keys out of runtime services.
- Treat presigned URLs and delivery manifests as bearer credentials: short lifetimes, never logged.
- Write each manifest last, as the completion marker, and never rewrite a published release or delivery version.
- Verify Event Notification signatures on the raw body and process each `eventId` once; delivery is at least once.
- Check consent before processing, generating or selecting voice data.
- Keep live audio, in-call state, queues, models, orchestration, catalogs and vector search off object storage.
- Applying a lifecycle template replaces all of a bucket's rules: read and merge the current rules first.
- The provisioning helper prints commands by default; `--apply` executes them and `--cleanup` only prints removal commands.
