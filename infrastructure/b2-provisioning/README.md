# B2 bucket and key provisioning

[`provision.py`](provision.py) prints the [`b2` command line](https://www.backblaze.com/docs/cloud-storage-command-line-tools) commands that create the buckets and scoped application keys of each architecture. It uses only the Python standard library.

**A run prints commands and executes nothing.** `--dry-run` is the default. The commands run only when you pass `--apply`.

```bash
# Print the commands for every architecture (nothing is executed)
python infrastructure/b2-provisioning/provision.py --bucket-prefix my-org

# One architecture
python infrastructure/b2-provisioning/provision.py --bucket-prefix my-org --architecture voice-agent-session-archive

# Run the commands with the b2 tool, using the account it is authorized for
b2 account authorize
python infrastructure/b2-provisioning/provision.py --bucket-prefix my-org --apply
```

Bucket names are unique across all of B2, so choose your own `--bucket-prefix`; `--apply` refuses the placeholder `my-org`. The script does not read or print credentials. `b2 key create` prints each key once, so copy that output into your secret manager.

## What it creates

One bucket per zone, named `<bucket-prefix>-<name>-<zone>`, private, with SSE-B2 as the default encryption. The protected zone is created with Object Lock enabled, which cannot be turned off afterwards. One application key per purpose prefix, limited to that bucket and prefix; there is no per-tenant prefix.

| Architecture (`--architecture`) | Bucket name part | Incoming | Work | Releases | Protected |
|---|---|---|---|---|---|
| `voice-training-data-lifecycle` | `voice-dataset` | `contributions/` | `candidates/`, `scratch/`, `evaluations/` | `datasets/` | `consents/` |
| `batch-voice-generation-and-dubbing` | `voice-generation` | `source/` | `candidates/`, `scratch/` | `deliveries/` | `consents/` |
| `voice-agent-session-archive` | `voice-sessions` | `sessions/` | `evaluations/`, `scratch/` | `datasets/` | `sessions/`, `consents/` |

Key names are `<name>-<zone>-<prefix>`, for example `voice-sessions-incoming-sessions`. Each key gets `listFiles`, `readFiles` and `writeFiles`; keys in the protected zone also get `readFileRetentions` and `readFileLegalHolds`. No key has `deleteFiles`, `bypassGovernance` or `writeKeys`. Narrow the capabilities further for components that only read or only write, as in the [scoped key examples](../../examples/README.md#scoped-application-keys).

After the commands, the script prints the value of `B2_INCOMING_BUCKET`, `B2_WORK_BUCKET`, `B2_RELEASES_BUCKET` and `B2_PROTECTED_BUCKET` for each architecture. Export the ones for the architecture you run.

Lifecycle, Event Notifications, CORS, Object Lock retention and replication are not applied by this script. Apply them from the [configuration templates](../../examples/README.md), reading the warning about lifecycle rules first.

## Clean up

`--cleanup` prints the commands that remove what this script creates. It never runs them, and it cannot be combined with `--apply`; review the output and run the commands yourself.

```bash
python infrastructure/b2-provisioning/provision.py --bucket-prefix my-org --cleanup
```

The commands, per architecture, are:

```bash
# 1. Delete each application key. List the keys to find their IDs.
b2 key list
b2 key delete <applicationKeyId>

# 2. Cancel unfinished large file uploads, delete every version, then delete the bucket.
b2 file large unfinished cancel b2://<bucket-name>
b2 rm --recursive --versions b2://<bucket-name>
b2 bucket delete <bucket-name>

# Protected zone: also bypass governance retention (needs a key with bypassGovernance).
b2 rm --recursive --versions --bypass-governance b2://<bucket-name>
```

`b2 rm` is irreversible: run it with `--dry-run` first to list what it would delete. A bucket must be empty, with no unfinished uploads, before `b2 bucket delete` succeeds. Versions under compliance-mode retention or a legal hold cannot be deleted until the retention ends or the hold is removed. The key that runs the clean up needs `deleteKeys`, `deleteBuckets` and `deleteFiles`; use your account's own key for it, not one of the keys created here.

## Tests

`tests/test_infrastructure.py` checks the printed plan without a B2 account: a default run executes nothing, each architecture has at least two zone buckets, each prefix has its own key, and no tenant prefix appears.
