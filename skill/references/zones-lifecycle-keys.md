# Zones, lifecycle and keys

## One bucket per zone

Lifecycle rules, Object Lock, replication, encryption defaults and Event Notification rules are set per bucket, and an application key admits one name prefix. The architectures therefore use one bucket per zone, with purpose prefixes inside each.

| Zone | Variable | Prefixes | Object Lock | Lifecycle template |
|---|---|---|---|---|
| Incoming | `B2_INCOMING_BUCKET` | `contributions/`, `sessions/`, `source/` | No | [`lifecycle-incoming.json`](../../examples/lifecycle-incoming.json) |
| Work | `B2_WORK_BUCKET` | `candidates/`, `scratch/`, `evaluations/` | No | [`lifecycle-work.json`](../../examples/lifecycle-work.json) |
| Releases | `B2_RELEASES_BUCKET` | `datasets/`, `deliveries/` | Optional, governance mode, set at bucket creation | [`lifecycle-releases.json`](../../examples/lifecycle-releases.json) |
| Protected | `B2_PROTECTED_BUCKET` | `consents/`, `sessions/` | Yes, governance-mode default retention | [`lifecycle-protected.json`](../../examples/lifecycle-protected.json) |

Each architecture uses only the prefixes that apply to it and names its buckets `<bucket-prefix>-voice-dataset-<zone>`, `<bucket-prefix>-voice-generation-<zone>` or `<bucket-prefix>-voice-sessions-<zone>`. The object layouts:

- [Voice training data lifecycle](../../architectures/voice-training-data-lifecycle/object-layout.md)
- [Batch voice generation and dubbing](../../architectures/batch-voice-generation-and-dubbing/object-layout.md)
- [Voice agent session archive](../../architectures/voice-agent-session-archive/object-layout.md)

All buckets are private and use SSE-B2 as the default encryption ([template](../../examples/encryption.json)). A single bucket with prefixes works for a small deployment; every lifecycle and notification rule is then scoped by prefix. There is no per-tenant prefix, and isolation between tenants is not covered.

## Lifecycle rules

- Incoming zone: raw uploads, recordings and sources are hidden after a number of days and deleted the next day. Set the period longer than the longest processing, retry and review time.
- Work zone: candidates and scratch data expire; current evaluation results are kept and superseded versions are deleted after a grace period.
- Releases zone: current versions are never hidden by lifecycle; hidden versions are deleted after a grace period, so a withdrawn release can be hidden and then removed.
- Protected zone: the lifecycle only cancels unfinished uploads. Lifecycle cannot delete a version under Object Lock, and retention differs by class, so retention is set by Object Lock and a retention job deletes after it ends.
- Every zone cancels unfinished multipart uploads, which are stored and billed until completed or cancelled.
- Day counts in the templates are examples. Set them from the user's retention policy and consent terms.
- **Applying lifecycle rules replaces all of a bucket's rules.** Read the current rules (`b2 bucket get`), merge, then apply. The [configuration README](../../examples/README.md#lifecycle-rules) has the commands.

## Object Lock, replication and events

- Object Lock cannot be disabled once enabled and cannot be enabled on a bucket that is a replication source. Use governance mode by default, so keys with `bypassGovernance` can remove a record after a consent withdrawal; use compliance mode only where a regulation or contract requires it, because nobody can shorten it. Template: [`object-lock.json`](../../examples/object-lock.json).
- Cloud Replication copies new objects to another bucket or region ([template](../../examples/replication.json)). Deletes and hide markers are not replicated, so a withdrawal must be applied to the replica as well. If the source uses Object Lock, the destination must too.
- Event Notifications ([template](../../examples/event-notifications.json)) call a signed webhook. They require enabling for the account through Backblaze Support, are delivered at least once, and must be answered quickly. Verify the HMAC SHA-256 signature on the raw body, process each `eventId` once and queue the work outside B2.

## Application keys

One key per component and prefix, with the narrowest capabilities. Examples, from the generation and dubbing layout:

| Component | Bucket and prefix | Capabilities |
|---|---|---|
| Source uploader or URL signer | incoming, `source/` | `writeFiles` |
| Job runner | incoming, `source/` | `listFiles`, `readFiles` (also signs the provider's presigned GET URLs) |
| Job runner | work, `candidates/` | `listFiles`, `readFiles`, `writeFiles` |
| CDN worker | releases, `deliveries/` | `readFiles` |
| Consent service | protected, `consents/` | `listFiles`, `readFiles`, `writeFiles` |
| LiveKit Egress | incoming, `sessions/` | `writeFiles` (the Egress request carries the key; build it server-side and never log it) |

No runtime key has `deleteFiles`, `bypassGovernance` or `writeKeys` unless its job requires it. The master key is never used by an application. A presigned URL carries the capabilities of the key that signed it, for one operation on one object. The commands are in [scoped application keys](../../examples/README.md#scoped-application-keys); the shared B2 skill and the [provisioning helper](../../infrastructure/b2-provisioning/README.md) create them (the helper prints commands by default).

## Presigned URLs and multipart

- Browser uploads use presigned PUT (one object, one content type, short expiry), not POST. Large files use multipart upload with retry, resume, complete and abort; the bucket's CORS rule must expose `ETag` ([template](../../examples/cors.s3.json)).
- Provider downloads use presigned GET with a lifetime longer than the provider's download window.
- Reviewers and recipients use short-lived presigned GET URLs issued by the application; the review tool holds no key.
- Working code: [`examples/python/`](../../examples/python/b2_client.py) and [`examples/typescript/`](../../examples/typescript/).
