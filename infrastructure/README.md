# Infrastructure helpers

Deployment helpers for the architectures, with tests that run without a B2 account.

| Folder | What it contains | Used by |
|---|---|---|
| [`livekit-egress/`](livekit-egress/README.md) | An Egress request template that writes session audio to a B2 bucket through the S3-compatible API, and a script that fills it from environment variables | `voice-agent-session-archive` |
| [`b2-provisioning/`](b2-provisioning/README.md) | A script that prints, and only on request runs, the `b2` commands that create per-zone buckets and scoped application keys for each architecture | All three architectures |

Both folders use the zone variable names and purpose prefixes from [`examples/README.md`](../examples/README.md): `B2_INCOMING_BUCKET`, `B2_WORK_BUCKET`, `B2_RELEASES_BUCKET` and `B2_PROTECTED_BUCKET`. Credentials come from the environment only and are never stored in a file here.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests/test_infrastructure.py
```

The tests check the shape of the Egress template (region-derived endpoint, path-style addressing) and the commands the provisioning script prints. They do not start LiveKit and do not call B2.

## Scope

The layouts assume a single organization; per-tenant isolation is not covered. The Egress template has been checked against LiveKit's documentation, not against a running Egress service. See each folder's README for the details.
