# B2 configuration templates

JSON templates for the bucket settings the architectures use, and the `b2` command line tool commands that apply them. Each template matches the request shape of the Backblaze B2 native API (or, for CORS, the S3-compatible API), and `tests/test_config_templates.py` checks that shape without a B2 account.

What each capability does, and its limits, is in the [capability map](../docs/b2-capabilities.md).

| Template | Applies to | Purpose |
|---|---|---|
| [`cors.s3.json`](cors.s3.json) | Any bucket a browser reads from or uploads to | Presigned PUT uploads and GET playback from one web origin; exposes `ETag` for browser multipart uploads |
| [`lifecycle-incoming.json`](lifecycle-incoming.json) | Incoming zone | Expires raw contributions, session recordings and generation sources after processing |
| [`lifecycle-work.json`](lifecycle-work.json) | Work zone | Expires candidates and scratch data; deletes superseded evaluation results |
| [`lifecycle-releases.json`](lifecycle-releases.json) | Releases zone (published versions) | Keeps every current version; deletes superseded versions after a grace period |
| [`lifecycle-protected.json`](lifecycle-protected.json) | Protected zone | Only cancels unfinished uploads; retention is governed by Object Lock |
| [`event-notifications.json`](event-notifications.json) | Incoming zone | Calls a signed webhook when contributions, sessions or generation sources arrive |
| [`object-lock.json`](object-lock.json) | Protected zone | Governance-mode default retention for consent records and retained sessions |
| [`replication.json`](replication.json) | Releases zone | Copies released versions to a bucket in another region |
| [`encryption.json`](encryption.json) | Every zone | SSE-B2 as the bucket default |

The day counts in the lifecycle templates and the retention period are example values. Set them from your own retention policy and consent terms.

## Layout

One bucket per zone, with purpose prefixes inside each bucket. Lifecycle, Object Lock, replication, encryption defaults and notification rules are configured per bucket, and an application key admits a single prefix, so separate buckets keep each policy and each key simple.

| Zone | Bucket (placeholder) | Prefixes |
|---|---|---|
| Incoming | `$B2_INCOMING_BUCKET` | `contributions/`, `sessions/`, `source/` |
| Work | `$B2_WORK_BUCKET` | `candidates/`, `scratch/`, `evaluations/` |
| Releases | `$B2_RELEASES_BUCKET` | `datasets/`, `deliveries/` |
| Protected | `$B2_PROTECTED_BUCKET` | `consents/`, `sessions/` |

The layouts assume a single organization; per-tenant isolation is not covered.

## Before you start

Install the `b2` command line tool (version 4 or later) and authorize it with a key that can create buckets and keys. The tool reads `B2_APPLICATION_KEY_ID` and `B2_APPLICATION_KEY` from the environment; keep both in your shell or secret manager, never in this repository.

```bash
export B2_INCOMING_BUCKET=<your-prefix>-incoming
export B2_WORK_BUCKET=<your-prefix>-work
export B2_RELEASES_BUCKET=<your-prefix>-releases
export B2_PROTECTED_BUCKET=<your-prefix>-protected
b2 account authorize
```

## Create the buckets with default encryption

```bash
for bucket in "$B2_INCOMING_BUCKET" "$B2_WORK_BUCKET" "$B2_RELEASES_BUCKET"; do
  b2 bucket create --default-server-side-encryption SSE-B2 "$bucket" allPrivate
done

# Object Lock is enabled at creation for the protected zone. It cannot be disabled later.
b2 bucket create --file-lock-enabled --default-server-side-encryption SSE-B2 "$B2_PROTECTED_BUCKET" allPrivate
```

`encryption.json` is the same setting in the native API shape (`defaultServerSideEncryption` in `b2_create_bucket` or `b2_update_bucket`). It applies only to objects written after it is set.

## Lifecycle rules

> **WARNING: applying lifecycle rules replaces ALL of a bucket's existing lifecycle rules.** The list you pass becomes the bucket's complete list; any rule not in it is removed. Read the current rules first with `b2 bucket get "$BUCKET"`, merge them into the template, and only then apply. A rule with an empty `fileNamePrefix` applies to every object in the bucket.

```bash
b2 bucket update --lifecycle-rules "$(cat examples/lifecycle-incoming.json)" "$B2_INCOMING_BUCKET"
b2 bucket update --lifecycle-rules "$(cat examples/lifecycle-work.json)" "$B2_WORK_BUCKET"
b2 bucket update --lifecycle-rules "$(cat examples/lifecycle-releases.json)" "$B2_RELEASES_BUCKET"
b2 bucket update --lifecycle-rules "$(cat examples/lifecycle-protected.json)" "$B2_PROTECTED_BUCKET"
```

Every zone has a rule that cancels unfinished large file uploads, because their parts are stored until the upload completes or is cancelled. Lifecycle rules never delete a version protected by Object Lock.

## Object Lock default retention (protected zone)

```bash
b2 bucket update --default-retention-mode governance --default-retention-period "30 days" "$B2_PROTECTED_BUCKET"
```

Governance mode can be overridden by a key with `bypassGovernance`; compliance mode cannot be shortened by anyone. Use compliance only where a regulation or contract requires it. Individual objects can also carry their own retention or a legal hold. If you also lock the releases zone, enable Object Lock on that bucket at creation and on its replication destination, because Object Lock cannot be enabled on a bucket that is already a replication source.

## Event Notifications (incoming zone)

Event Notifications must be enabled for the account through Backblaze Support. Generate the 32-character alphanumeric signing secret in your secret manager and pass it through the environment; it is not stored in the template.

```bash
b2 bucket notification-rule create "b2://$B2_INCOMING_BUCKET/contributions/" validate-contributions \
  --event-type "b2:ObjectCreated:*" --webhook-url "https://hooks.example.com/b2/contributions" \
  --sign-secret "$B2_EVENT_SIGNING_SECRET" --enable
b2 bucket notification-rule create "b2://$B2_INCOMING_BUCKET/sessions/" evaluate-sessions \
  --event-type "b2:ObjectCreated:*" --webhook-url "https://hooks.example.com/b2/sessions" \
  --sign-secret "$B2_EVENT_SIGNING_SECRET" --enable
b2 bucket notification-rule create "b2://$B2_INCOMING_BUCKET/source/" review-generation-sources \
  --event-type "b2:ObjectCreated:*" --webhook-url "https://hooks.example.com/b2/sources" \
  --sign-secret "$B2_EVENT_SIGNING_SECRET" --enable
```

`event-notifications.json` holds the same rules in the shape of `b2_set_bucket_notification_rules`. The receiver verifies `X-Bz-Event-Notification-Signature` against the raw body, answers 200 within three seconds, and processes each `eventId` only once, because delivery is at least once.

## Cloud Replication (releases zone)

The destination bucket lives in a second account (one account per region). Configure a `b2` profile for it, then let the tool create the replication keys:

```bash
b2 replication setup --destination-profile second-region --name releases-second-region \
  --include-existing-files "$B2_RELEASES_BUCKET" "<destination-bucket-name>"
```

`replication.json` shows the resulting `replicationConfiguration` in the native API shape. A bucket accepts at most two replication rules. Deletes, hide markers and SSE-C objects are not replicated, and the destination's own lifecycle rules apply to the replicas.

## CORS for browser uploads and playback

`b2 bucket update --cors-rules` sets rules in the native API format. Presigned URLs in these architectures come from the S3-compatible API, so this template uses the S3 `PutBucketCors` format and is applied with an S3 client pointed at the B2 endpoint, using a key with `writeBuckets` on the bucket:

```bash
aws s3api put-bucket-cors --endpoint-url "https://s3.$B2_REGION.backblazeb2.com" \
  --bucket "$B2_INCOMING_BUCKET" --cors-configuration file://examples/cors.s3.json
```

Replace `https://app.example.com` with your application's origin. Rules set through the S3-compatible API cannot be edited through the native API, and the reverse.

## Scoped application keys

Give each component its own key, limited to one bucket, one prefix and only the capabilities it uses. The secret is printed once; store it in your secret manager. `--duration` (seconds) makes a key expire.

```bash
# Server that signs presigned PUT URLs for contributors: write-only, one prefix.
b2 key create --bucket "$B2_INCOMING_BUCKET" --name-prefix contributions/ contributions-uploader writeFiles

# Webhook worker that validates new contributions: read the incoming zone.
b2 key create --bucket "$B2_INCOMING_BUCKET" --name-prefix contributions/ contributions-reader listFiles,readFiles

# Same worker writing accepted clips to the work zone.
b2 key create --bucket "$B2_WORK_BUCKET" --name-prefix candidates/ candidates-writer listFiles,writeFiles

# Training job at a compute provider: read-only on one release, expires after seven days.
b2 key create --bucket "$B2_RELEASES_BUCKET" --name-prefix datasets/ --duration 604800 training-reader listFiles,readFiles

# Auditor of consent records: read-only on the protected zone.
b2 key create --bucket "$B2_PROTECTED_BUCKET" --name-prefix consents/ consent-auditor listFiles,readFiles,readFileRetentions,readFileLegalHolds
```

No key used by an application has `deleteFiles`, `bypassGovernance` or `writeKeys` unless its job requires it.
