# Object layout: voice training data lifecycle

One bucket per zone, with purpose prefixes inside each bucket. Lifecycle rules, Object Lock, replication, default encryption and Event Notification rules are configured per bucket, and an application key admits a single prefix, so separate buckets keep each policy and each key simple. There is no per-tenant prefix: the layout assumes a single organization.

Bucket names follow [`infrastructure/b2-provisioning/`](../../infrastructure/b2-provisioning/README.md): `<bucket-prefix>-voice-dataset-<zone>`. The environment variables are the ones the [examples](../../examples/README.md#code-examples) read.

| Zone | Bucket | Variable | Object Lock | Lifecycle template |
|---|---|---|---|---|
| Incoming | `<bucket-prefix>-voice-dataset-incoming` | `B2_INCOMING_BUCKET` | No | [`lifecycle-incoming.json`](../../examples/lifecycle-incoming.json) |
| Work | `<bucket-prefix>-voice-dataset-work` | `B2_WORK_BUCKET` | No | [`lifecycle-work.json`](../../examples/lifecycle-work.json) |
| Releases | `<bucket-prefix>-voice-dataset-releases` | `B2_RELEASES_BUCKET` | Optional, governance mode, set at creation | [`lifecycle-releases.json`](../../examples/lifecycle-releases.json) |
| Protected | `<bucket-prefix>-voice-dataset-protected` | `B2_PROTECTED_BUCKET` | Yes, governance default retention | [`lifecycle-protected.json`](../../examples/lifecycle-protected.json) |

All four buckets are private and use SSE-B2 as the default encryption.

## Incoming bucket

```text
contributions/
  <yyyy-mm-dd>/
    <upload_id>.wav           # clip as uploaded
    <upload_id>.json          # sidecar: consent_id, prompt text, capture settings
```

- **Writes:** the contributor app, through presigned PUT URLs signed by a key limited to `contributions/` with `writeFiles` only.
- **Reads:** the validation worker.
- **Events:** an Event Notification rule on `contributions/` for `b2:ObjectCreated:*` ([template](../../examples/event-notifications.json)).
- **Lifecycle:** objects under `contributions/` are hidden after the example period and deleted the next day; unfinished large uploads are cancelled. The template also carries rules for `sessions/` and `source/`, which this architecture does not use; they match nothing in this bucket.
- **Names:** `upload_id` is random. Object names never contain a person's name, email, phone number or the consent text.

## Work bucket

```text
candidates/
  <batch_id>/
    audio/<clip_id>.wav       # normalized clip
    manifest.jsonl            # NeMo lines for the batch, with sha256 and consent_id
scratch/
  <job_id>/...                # temporary output of a worker or builder run
evaluations/
  validation/<batch_id>/report.json     # accepted and rejected counts
  validation/<batch_id>/rejects.jsonl   # one line per rejected clip with its defect codes
  review/<project_id>/...               # Label Studio target storage (annotations)
  mlflow/<experiment_id>/<run_id>/...   # MLflow artifact root
```

- **Writes:** the validation worker (`candidates/`, `evaluations/validation/`), Label Studio target storage (`evaluations/review/`), MLflow (`evaluations/mlflow/`). Each writer has its own key; a key limited to `evaluations/` covers the three subfolders, or narrower prefixes such as `evaluations/review/` can be used.
- **Reads:** Label Studio source storage (`candidates/`), the release builder.
- **Lifecycle:** `candidates/` and `scratch/` are hidden after the example periods and then deleted; in `evaluations/`, superseded versions are deleted after the example period and current versions are kept; unfinished large uploads are cancelled.
- **CORS:** allow `GET` and `HEAD` from the Label Studio origin only, when Label Studio uses pre-signed URLs.

## Releases bucket

```text
datasets/
  <dataset>/
    <version>/
      audio/<clip_id>.wav
      manifests/train.jsonl          # NeMo JSONL
      manifests/dev.jsonl
      manifests/test.jsonl
      shards/lhotse-shar/<split>/cuts.000000.jsonl.gz
      shards/lhotse-shar/<split>/recording.000000.tar
      shards/webdataset/<split>-000000.tar
      shards/mds/<split>/index.json
      shards/mds/<split>/shard.00000.mds
      croissant.json
      README.md
      release-manifest.json          # written last; the release exists only once this exists
  dvc/                               # DVC remote (content-addressed), separate from releases
```

- **Writes:** the release builder only. A `<version>` folder is written once; any change is a new version.
- **Reads:** training and evaluation jobs, with read-only keys limited to `datasets/` and an expiry.
- **Lifecycle:** current versions are never hidden by lifecycle; hidden versions are deleted after a grace period, which lets a withdrawn release be hidden and then removed. Unfinished large uploads are cancelled.
- **Replication:** optional, to a bucket in another region ([template](../../examples/replication.json)). Deletes and hide markers are not replicated, so a withdrawal must be applied to the replica as well.
- **Object Lock:** optional. If used, it must be enabled on this bucket and on the replication destination at creation, and governance mode keeps the ability to remove a release after a consent withdrawal.

## Protected bucket

```text
consents/
  <consent_id>.json           # consent and license record; a withdrawal is a new version
```

- **Writes:** the consent service, with `writeFiles` on `consents/`.
- **Reads:** the validation worker, the release builder and auditors (with `readFileRetentions` and `readFileLegalHolds`).
- **Object Lock:** enabled at bucket creation, with governance-mode default retention ([template](../../examples/object-lock.json)); legal holds where an investigation or contract requires it.
- **Lifecycle:** only cancels unfinished large uploads; retention is governed by Object Lock.

## Keys

`provision.py` creates one key per purpose prefix (for example `voice-dataset-work-candidates`) with `listFiles`, `readFiles` and `writeFiles`. Narrow them per component:

| Component | Bucket and prefix | Capabilities |
|---|---|---|
| Upload signer | incoming, `contributions/` | `writeFiles` |
| Validation worker | incoming, `contributions/` | `listFiles`, `readFiles` |
| Validation worker | protected, `consents/` | `listFiles`, `readFiles` |
| Validation worker | work, `candidates/` | `listFiles`, `writeFiles` |
| Validation worker | work, `evaluations/` | `writeFiles` |
| Consent service | protected, `consents/` | `listFiles`, `readFiles`, `writeFiles` |
| Label Studio source | work, `candidates/` | `listFiles`, `readFiles` |
| Label Studio target | work, `evaluations/review/` | `listFiles`, `writeFiles` |
| MLflow server | work, `evaluations/mlflow/` | `listFiles`, `readFiles`, `writeFiles` |
| Release builder | work, `candidates/` and `evaluations/` (two keys) | `listFiles`, `readFiles` |
| Release builder | protected, `consents/` | `listFiles`, `readFiles` |
| Release builder | releases, `datasets/` | `listFiles`, `readFiles`, `writeFiles` |
| Training job | releases, `datasets/` | `listFiles`, `readFiles`, with an expiry |
| DVC users | releases, `datasets/dvc/` | `listFiles`, `readFiles`, plus `writeFiles` for whoever pushes |

## Clean up

`python infrastructure/b2-provisioning/provision.py --bucket-prefix <your-prefix> --architecture voice-training-data-lifecycle --cleanup` prints the commands that remove these keys and buckets. Read the notes in the [provisioning README](../../infrastructure/b2-provisioning/README.md#clean-up) first: deletion is irreversible, and versions under compliance retention or a legal hold cannot be deleted.
