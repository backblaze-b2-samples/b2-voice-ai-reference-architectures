# Sample applications

The applications in this folder are small Python command-line tools that implement one architecture of the [catalog](../catalog/architectures.yaml) each. They live in this repository, one folder per application, and their tests run against an emulated S3 server on the loopback interface, so they need no B2 account and no credentials. An optional `tests/test_live.py` in each application runs against a real bucket only when `RUN_B2_LIVE_TESTS=1` and the `B2_*` variables are set.

The [architectures](../docs/architecture-index.md) are complete without an application: each one ships its schemas, B2 configuration templates, examples and test fixtures, and can be validated without a B2 account.

## Applications

No application is published in this folder yet. When one is added, it gets a section on this page, a row in the matrix of the `applications` job in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), and a `links.application` field in its catalog entry that points to its README.

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
