# Contributing

The architecture catalog is the extension point. Edit `catalog/architectures.yaml`, then run `python tools/generate_catalog.py`; do not hand-edit generated README tables, indexes or `llms.txt`.

- New architecture: add `architectures/<id>/` with `architecture.md`, `decisions.md` and `object-layout.md`; the folder name is the catalog id. `architecture.md` has Boundaries, Where B2 does not fit, Evidence (dated), Security and a Mermaid diagram; the object layout has its own section. Add its audience, problem, workflow, boundaries, scale, validation evidence, security, compliance and links to the catalog. Reuse `examples/`, `testdata/`, `manifests/` and `schemas/`.
- New integration: add it under the relevant catalog architecture with its connection method and type (`native-s3-output` or `application-copy`), evidence date and authoritative documentation. Add an architecture only if the tool materially changes the data flow or security boundary.
- New application: follow the "Applications" rules in `AGENTS.md`. In short: `applications/<name>/` with `pyproject.toml`, the package in `src/<package>/`, a `.env.example` file with the `B2_*` names, tests against moto in `tests/` plus an opt-in `tests/test_live.py`, and a README with "B2's role", "Setup", "Tests" and "Known limitations". Code from `examples/python/` or `tools/` is copied unchanged into `src/<package>/shared/`. Each application owns its test and demo data, generated deterministically inside it; it may read `testdata/synthetic-speech/` but never modifies it. Add it to the CI job matrix, to `applications/README.md` and to the catalog's `links.application` in the same change.
- Regenerate `README.md`, `docs/architecture-index.md`, `skill/references/architecture-catalog.md` and `llms.txt` in the same change.
- Overview diagram: edit `tools/render_diagram.py`, run `python tools/render_diagram.py` and commit `docs/images/voice-ai-architectures.png` (1600x900) in the same change.
- Keep the README sections `Where state lives`, `Clean up`, `Known limitations` and `Partner integration options`; update `Clean up` when an example or helper creates a new bucket, key or rule.

Evidence rules: attribute to a partner only what its docs or code show; label B2-side patterns as such; never hardcode a region or endpoint; link B2 pricing instead of quoting numbers; mark unverified claims. A validation status claims no more than its evidence: when only documentation supports an entry, its status is `documentation-reviewed`.

Security: no real keys anywhere; secrets in a gitignored `0600` environment file; scoped keys, not master, unless provisioning requires it (say so).

Tests: `python -m pytest -q tests`, `python tools/validate_contracts.py`, `python tools/validate_contracts.py --capability-map docs/b2-capabilities.md`, `python tools/generate_catalog.py --check`, `python tools/check_markdown_links.py` and `bash tools/run_ts_examples.sh`. Use moto for S3 data operations and environment-gated acceptance tests for Object Lock, key scoping and lifecycle.

Contributions are accepted under the repository's [MIT License](../LICENSE).
