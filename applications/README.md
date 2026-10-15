# Sample applications

No sample applications are linked yet. When one is linked, its entry in this page and its `links.application` field in the [catalog](../catalog/architectures.yaml) point to a public repository, and the architecture that it implements links back through the catalog.

The [architectures](../docs/architecture-index.md) are complete without an application: each one ships its schemas, B2 configuration templates, examples and test fixtures, and can be validated without a B2 account.

## How each entry is described

Every sample application listed here has these five parts:

| Part | What it states |
|---|---|
| What it shows | The workflow the application demonstrates and the parts of the architecture it exercises |
| Architecture | The architecture it implements, by catalog id |
| Configurations it ships | The B2 settings the application delivers as files and commands (for example lifecycle, CORS, Event Notifications, Object Lock, replication), and the confirmation that it applies none of them to a shared bucket |
| How to deploy | The prerequisites, the environment variables (`B2_*`) and the commands to run it, including the data it starts with and the version of this repository that data came from |
| Known limitations | What the application does not do, what is simulated or synthetic, and what was and was not verified |

An application is linked only when a fresh clone follows the same B2 conventions as the [examples](../examples/README.md): `B2_*` environment variable names, an endpoint derived from `B2_REGION` and a descriptive user agent.
